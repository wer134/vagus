"""OSPF LSA 이상 탐지 — RFC 2328 기반, 인증 없는 환경에서의 위조 LSA 탐지.

탐지 규칙:
  1. unknown_router  — 등록되지 않은 라우터 ID (출처 위장)
  2. seq_jump        — 시퀀스 번호 급등/롤백 (번호 조작)
  3. lsa_flood       — 단시간 내 LSA 반복 재발송 (flooding)
"""
import time
from collections import defaultdict
from dataclasses import dataclass

KNOWN_ROUTERS      = {"r1", "r2", "r3", "r4"}
SEQ_JUMP_THRESHOLD = 50       # delta 이 값 초과 or 음수 → 시퀀스 조작 의심
REORIG_WINDOW_SEC  = 5.0      # flooding 감지 윈도우 (초)
REORIG_MAX_COUNT   = 3        # 윈도우 내 이 횟수 이상이면 flooding
_HISTORY_TTL_SEC   = 60.0     # 오래된 이력 보관 기간


@dataclass
class _LsaEvent:
    seq_no:    int
    timestamp: float


class OspfSecurityMonitor:
    """비인증 OSPF 환경 위조 LSA 탐지 모니터."""

    def __init__(self):
        self._history: dict[str, list[_LsaEvent]] = defaultdict(list)
        self._alerts:  list[dict] = []

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
