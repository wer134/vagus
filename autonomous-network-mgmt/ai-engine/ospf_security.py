"""OSPF LSA 이상 탐지 — RFC 2328 기반.

비인증 경로(check_lsa)의 탐지 규칙:
  1. unknown_router  — 등록되지 않은 라우터 ID (출처 위장)
  2. seq_jump        — 시퀀스 번호 급등/롤백 (번호 조작)
  3. lsa_flood       — 단시간 내 LSA 반복 재발송 (flooding)

인증 경로(check_lsa_authenticated, RFC 2328 Appendix D / RFC 5709)의 추가 탐지:
  4. auth_none         — 인증 없는 평문 LSA
  5. auth_downgrade    — 단순 비밀번호로 강제 다운그레이드 시도
  6. auth_digest_mismatch — 다이제스트 불일치 (위조/오류)
  7. auth_replay       — crypto_seq 재생 공격
  8. auth_weak_algo    — 정책(SHA256) 대비 약한 알고리즘(MD5) 사용

인증을 통과한 LSA는 추가로 1~3번 규칙도 적용한다(defense-in-depth) — 키가 유출된
공격자가 유효한 서명으로 비정상 시퀀스를 보내는 경우까지 잡기 위함.
"""
import hashlib
import hmac
import time
from collections import defaultdict
from dataclasses import dataclass

KNOWN_ROUTERS      = {"r1", "r2", "r3", "r4"}
SEQ_JUMP_THRESHOLD = 50       # delta 이 값 초과 or 음수 → 시퀀스 조작 의심
REORIG_WINDOW_SEC  = 5.0      # flooding 감지 윈도우 (초)
REORIG_MAX_COUNT   = 3        # 윈도우 내 이 횟수 이상이면 flooding
_HISTORY_TTL_SEC   = 60.0     # 오래된 이력 보관 기간

# ── OSPF 인증 (RFC 2328 Appendix D / RFC 5709) ──────────────────────────────
AUTH_NONE          = 0   # RFC 2328 D.1 — 인증 없음
AUTH_SIMPLE        = 1   # RFC 2328 D.2 — 평문 비밀번호
AUTH_CRYPTO_MD5    = 2   # RFC 2328 D.3 — Keyed-MD5
AUTH_CRYPTO_SHA256 = 3   # RFC 5709 — HMAC-SHA256 (MD5보다 강한 대안)

REQUIRED_AUTH_TYPE = AUTH_CRYPTO_SHA256  # 이 네트워크의 인증 정책

# key_id → 공유 비밀키 (키 회전 시 새 key_id 추가, 구 키는 한동안 유지 후 폐기)
_OSPF_AUTH_KEYS: dict[int, bytes] = {
    1: b"anm-ospf-key-v1-deprecated",
    2: b"anm-ospf-key-v2-active",
}
ACTIVE_KEY_ID = 2

_DIGEST_ALGOS = {AUTH_CRYPTO_MD5: hashlib.md5, AUTH_CRYPTO_SHA256: hashlib.sha256}


def compute_digest(router_id: str, seq_no: int, crypto_seq: int | None,
                    key_id: int | None, auth_type: int) -> str | None:
    """LSA 페이로드(라우터ID+시퀀스+크립토시퀀스)에 대한 HMAC 다이제스트 계산."""
    key  = _OSPF_AUTH_KEYS.get(key_id) if key_id is not None else None
    algo = _DIGEST_ALGOS.get(auth_type)
    if key is None or algo is None:
        return None
    payload = f"{router_id}:{seq_no}:{crypto_seq}".encode()
    return hmac.new(key, payload, algo).hexdigest()


@dataclass
class OspfLsaPacket:
    router_id:  str
    seq_no:     int
    auth_type:  int = AUTH_NONE
    key_id:     int | None = None
    crypto_seq: int | None = None   # RFC 2328 Appendix D.3 비재생 시퀀스 (LSA seq_no와 별도)
    digest:     str | None = None

    @classmethod
    def signed(cls, router_id: str, seq_no: int, crypto_seq: int,
               key_id: int = ACTIVE_KEY_ID, auth_type: int = AUTH_CRYPTO_SHA256) -> "OspfLsaPacket":
        """정상 라우터가 보내는 것처럼 올바르게 서명된 패킷 생성 (테스트/데모용)."""
        digest = compute_digest(router_id, seq_no, crypto_seq, key_id, auth_type)
        return cls(router_id, seq_no, auth_type, key_id, crypto_seq, digest)


@dataclass
class _LsaEvent:
    seq_no:    int
    timestamp: float


class OspfSecurityMonitor:
    """비인증 OSPF 환경 위조 LSA 탐지 모니터."""

    def __init__(self):
        self._history: dict[str, list[_LsaEvent]] = defaultdict(list)
        self._alerts:  list[dict] = []
        self._crypto_seq_history: dict[str, int] = {}

    def check_lsa(self, router_id: str, seq_no: int) -> dict:
        """LSA 수신 시 호출. 의심 여부와 이유를 반환한다."""
        now = time.time()
        result: dict = {
            "suspicious": False,
            "rule":       None,
            "reason":     None,
            "router_id":  router_id,
            "seq_no":     seq_no,
        }

        # Rule 1: 미등록 라우터 ID
        if router_id not in KNOWN_ROUTERS:
            result.update(
                suspicious=True,
                rule="unknown_router",
                reason=f"라우터 ID '{router_id}' 미등록 — 출처 위장 의심",
            )
            self._record(result, now)
            return result

        history = self._history[router_id]

        # Rule 2: 시퀀스 번호 점프
        if history:
            last_seq = history[-1].seq_no
            delta = seq_no - last_seq
            if delta < 0 or delta > SEQ_JUMP_THRESHOLD:
                result.update(
                    suspicious=True,
                    rule="seq_jump",
                    reason=(
                        f"시퀀스 점프: {last_seq} → {seq_no} "
                        f"(Δ={delta:+d}, 임계=±{SEQ_JUMP_THRESHOLD}) — 번호 조작 의심"
                    ),
                )
                self._record(result, now)
                history.append(_LsaEvent(seq_no=seq_no, timestamp=now))
                self._trim(router_id, now)
                return result

        # Rule 3: LSA 재발송 폭탄 (flooding)
        recent = [e for e in history if e.timestamp >= now - REORIG_WINDOW_SEC]
        if len(recent) >= REORIG_MAX_COUNT:
            result.update(
                suspicious=True,
                rule="lsa_flood",
                reason=(
                    f"LSA flooding: {REORIG_WINDOW_SEC:.0f}초 내 "
                    f"{len(recent) + 1}회 재발송 (임계={REORIG_MAX_COUNT})"
                ),
            )
            self._record(result, now)

        history.append(_LsaEvent(seq_no=seq_no, timestamp=now))
        self._trim(router_id, now)
        return result

    def inject_fake_lsa(self, router_id: str = "r99", seq_no: int = 99999) -> dict:
        """데모용: 위조 LSA 주입 시뮬레이션."""
        return self.check_lsa(router_id, seq_no)

    def verify_auth(self, packet: "OspfLsaPacket") -> dict:
        """RFC 2328 Appendix D / RFC 5709 기반 OSPF 인증 검증.

        Returns: {"auth_ok": bool, "rule": str|None, "reason": str|None}
        """
        if packet.auth_type == AUTH_NONE:
            return {"auth_ok": False, "rule": "auth_none",
                     "reason": "인증 없음 — 평문 LSA, 누구나 위조 가능"}
        if packet.auth_type == AUTH_SIMPLE:
            return {"auth_ok": False, "rule": "auth_downgrade",
                     "reason": "단순 비밀번호 인증 — 스니핑으로 평문 노출, 정책(SHA256) 위반"}

        expected = compute_digest(
            packet.router_id, packet.seq_no, packet.crypto_seq, packet.key_id, packet.auth_type,
        )
        if expected is None or packet.digest is None or not hmac.compare_digest(expected, packet.digest):
            return {"auth_ok": False, "rule": "auth_digest_mismatch",
                     "reason": f"다이제스트 불일치 (key_id={packet.key_id}) — 위조 또는 잘못된 키"}

        last_seq = self._crypto_seq_history.get(packet.router_id, -1)
        if packet.crypto_seq is None or packet.crypto_seq <= last_seq:
            return {"auth_ok": False, "rule": "auth_replay",
                     "reason": f"재생 공격 의심 — crypto_seq {packet.crypto_seq} <= 마지막 수신값 {last_seq}"}
        self._crypto_seq_history[packet.router_id] = packet.crypto_seq

        if packet.auth_type != REQUIRED_AUTH_TYPE:
            return {"auth_ok": False, "rule": "auth_weak_algo",
                     "reason": f"인증은 유효하지만 정책(SHA256) 대비 약한 알고리즘(type={packet.auth_type}) 사용 — 다운그레이드 위험"}

        return {"auth_ok": True, "rule": None, "reason": None}

    def check_lsa_authenticated(self, packet: "OspfLsaPacket") -> dict:
        """인증 검증 후 통과한 LSA에 대해서만 기존 콘텐츠 이상 탐지(check_lsa)를 추가 적용한다.

        키가 유출된 공격자가 유효한 서명으로 비정상 시퀀스를 보내는 경우까지
        잡기 위한 defense-in-depth 구조.
        """
        auth_result = self.verify_auth(packet)
        if not auth_result["auth_ok"]:
            result = {
                "suspicious": True,
                "rule":       auth_result["rule"],
                "reason":     auth_result["reason"],
                "router_id":  packet.router_id,
                "seq_no":     packet.seq_no,
            }
            self._record(result, time.time())
            return result
        return self.check_lsa(packet.router_id, packet.seq_no)

    def recent_alerts(self, n: int = 20) -> list[dict]:
        return list(self._alerts[-n:])

    def _record(self, result: dict, ts: float) -> None:
        self._alerts.append({**result, "timestamp": round(ts, 3)})
        if len(self._alerts) > 200:
            self._alerts.pop(0)

    def _trim(self, router_id: str, now: float) -> None:
        cutoff = now - _HISTORY_TTL_SEC
        self._history[router_id] = [
            e for e in self._history[router_id] if e.timestamp >= cutoff
        ]


ospf_monitor = OspfSecurityMonitor()


# ── 인증 우회 시나리오 (데모/검증용) ──────────────────────────────────────────

def simulate_replay_attack(monitor: OspfSecurityMonitor, router_id: str = "r1") -> dict:
    """정상 서명된 LSA를 캡처해 그대로 재전송하는 재생 공격을 시뮬레이션한다.

    crypto_seq 검증으로 재생 시도가 차단되는지 확인.
    """
    crypto_seq = int(time.time() * 1000)
    packet = OspfLsaPacket.signed(router_id, seq_no=100, crypto_seq=crypto_seq)
    first  = monitor.check_lsa_authenticated(packet)   # 정상 수신
    replay = monitor.check_lsa_authenticated(packet)   # 동일 패킷 재전송 (캡처-재생)
    return {"first_receipt": first, "replay_attempt": replay}


def simulate_auth_downgrade(monitor: OspfSecurityMonitor, router_id: str = "r1") -> dict:
    """공격자가 인증 없이(또는 단순 비밀번호로) LSA를 주입하는 다운그레이드 공격을 시뮬레이션한다."""
    packet = OspfLsaPacket(router_id=router_id, seq_no=101, auth_type=AUTH_NONE)
    return monitor.check_lsa_authenticated(packet)


def simulate_key_compromise_forge(
    monitor: OspfSecurityMonitor, router_id: str = "r1", leaked_key_id: int = ACTIVE_KEY_ID,
) -> dict:
    """공격자가 유출된 키로 유효한 다이제스트를 만들어 비정상 시퀀스의 LSA를 위조하는 시나리오.

    암호학적 인증 자체는 통과한다(키가 노출됐기 때문) — 정상 베이스라인을 먼저 확립한 뒤
    같은 키로 큰 시퀀스 점프를 위조해, check_lsa_authenticated의 defense-in-depth 단계
    (기존 seq_jump 규칙)에서만 잡힐 수 있다는 한계를 보여주는 데모.
    """
    base_crypto_seq = int(time.time() * 1000)
    baseline = OspfLsaPacket.signed(router_id, seq_no=10, crypto_seq=base_crypto_seq, key_id=leaked_key_id)
    baseline_result = monitor.check_lsa_authenticated(baseline)  # 정상 베이스라인 확립

    forged_seq        = 99999  # 정상 범위를 벗어난 시퀀스로 위조
    forged_crypto_seq = base_crypto_seq + 1_000_000  # 미래 crypto_seq로 재생 검사도 통과
    forged = OspfLsaPacket.signed(router_id, seq_no=forged_seq, crypto_seq=forged_crypto_seq, key_id=leaked_key_id)
    forged_result = monitor.check_lsa_authenticated(forged)

    return {"baseline": baseline_result, "forged_with_leaked_key": forged_result}


if __name__ == "__main__":
    m = OspfSecurityMonitor()

    r1 = simulate_replay_attack(m, router_id="r1")
    assert r1["first_receipt"]["suspicious"] is False, r1
    assert r1["replay_attempt"]["suspicious"] is True and r1["replay_attempt"]["rule"] == "auth_replay", r1
    print("OK — replay 공격 탐지:", r1["replay_attempt"]["reason"])

    r2 = simulate_auth_downgrade(m, router_id="r2")
    assert r2["suspicious"] is True and r2["rule"] == "auth_none", r2
    print("OK — 다운그레이드 공격 탐지:", r2["reason"])

    r3 = simulate_key_compromise_forge(m, router_id="r3")
    assert r3["baseline"]["suspicious"] is False, r3
    assert r3["forged_with_leaked_key"]["suspicious"] is True and r3["forged_with_leaked_key"]["rule"] == "seq_jump", r3
    print("OK — 키 유출 위조는 인증을 통과하지만 seq_jump 규칙으로 탐지:", r3["forged_with_leaked_key"]["reason"])

    # MD5(약한 알고리즘)로는 통과하더라도 정책(SHA256) 위반으로 플래그
    weak = OspfLsaPacket.signed("r4", seq_no=10, crypto_seq=1, auth_type=AUTH_CRYPTO_MD5)
    weak_result = m.check_lsa_authenticated(weak)
    assert weak_result["suspicious"] is True and weak_result["rule"] == "auth_weak_algo", weak_result
    print("OK — MD5 다운그레이드 탐지:", weak_result["reason"])

    print("\n모든 자가 테스트 통과")
