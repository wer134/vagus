"""랜덤 네트워크 메트릭 생성기 — 링크 스트레스 기반 모델.

핵심 피드백 루프:
  혼잡 링크에 높은 OSPF cost → 트래픽 우회 → 해당 링크 혼잡 완화 → 지연/손실 감소
  → 에이전트의 행동이 다음 관측값에 직접 반영됨

링크 스트레스 → 노드 메트릭:
  각 노드의 메트릭은 인접 링크 스트레스의 평균으로 계산한다.

시뮬레이션 시간 (2026-09-09 변경):
  시간은 `tick()`을 호출할 때만 1스텝 진행한다. `get_*_metrics()`는 순수 조회다.
  이전에는 메트릭 조회마다 스트레스가 갱신되어 "관측 횟수 = 시뮬레이션 시간"이 되었고,
  실험 스크립트가 검증용으로 /metrics를 한 번 더 읽는 것만으로 OODA 사이클당 2틱이
  진행되어 TTR이 측정 방식에 의존했다 (cowork/AUDIT_2026-09-09.md P1).
  누가 tick을 호출하는가:
    - /auto-step (api_server): 사이클 시작 시 POST /debug/tick 1회
    - NetworkEnv.step() (local_mode): 행동 적용 후 tick() 1회
    - mock_snmp_agent SIM_CLOCK=realtime:<ms>: 백그라운드 스레드가 주기적으로 tick (데모용)
"""
import random
import threading
import time

# 시뮬레이터 동작 버전 (결과 파일의 contract 블록에 기록 — cowork/ROADMAP.md E-4)
#   1 = 관측 호출이 시간을 진행시키던 모델 (~2026-09-08)
#   2 = 명시적 tick() 분리 (2026-09-09, AUDIT P1)
SIM_VERSION = 2

NODES = ["r1", "r2", "r3", "r4"]

LINKS_LIST = ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]

# 하위 호환용 tuple 리스트
LINKS = [tuple(lk.split("-")) for lk in LINKS_LIST]

LINK_ENDPOINTS: dict[str, tuple[str, str]] = {
    "r1-r2": ("r1", "r2"),
    "r1-r3": ("r1", "r3"),
    "r2-r3": ("r2", "r3"),
    "r2-r4": ("r2", "r4"),
    "r3-r4": ("r3", "r4"),
    "r1-r4": ("r1", "r4"),
}

# 노드별 인접 링크 목록 (메트릭 집계용)
NODE_LINKS: dict[str, list[str]] = {
    n: [lk for lk, ep in LINK_ENDPOINTS.items() if n in ep]
    for n in NODES
}

# OSPF 코스트
_ospf_costs: dict[str, int] = {lk: 10 for lk in LINKS_LIST}

# 혼잡 주입 상태
_congested_links: set[str] = set()

# 공격 주입 상태
_attack_state: str | None = None   # None | "ddos" | "portscan"

# 링크별 스트레스 [0,1]  (0=정상, 1=완전혼잡)
_link_stress: dict[str, float] = {lk: 0.02 for lk in LINKS_LIST}

# 시뮬레이션 틱 카운터 (reset 시 0)
_tick_count: int = 0

# 노이즈 전용 난수원 — reset_state(seed=)로 재현 가능. 전역 random과 분리해
# 다른 모듈의 random 사용이 시뮬레이션 재현성을 깨지 않도록 한다.
_rng = random.Random()

# 상태 변경 직렬화 (Flask threaded=True, FastAPI 스레드풀 동시 호출 대비)
_lock = threading.RLock()

# 스트레스 모델 상수
_DECAY     = 0.90    # 스텝당 자연 회복률
_LOAD_GAIN = 0.01    # 트래픽 부하 → 스트레스 증가 계수
_CONG_GAIN = 0.50    # 혼잡 이벤트 → 스트레스 증가 계수 (cost<100일 때만)
_NOISE     = 0.015   # 관측 노이즈 표준편차

# cost ≥ 이 값이면 트래픽이 완전 우회 → 혼잡 격리 (스트레스 증가 중단)
BYPASS_COST_THRESHOLD = 100


def _link_key(a: str, b: str) -> str:
    return "-".join(sorted([a, b]))


def _compute_link_traffic() -> dict[str, float]:
    """OSPF cost의 역수 기반 각 링크의 트래픽 비율 계산."""
    inv = {lk: 1.0 / c for lk, c in _ospf_costs.items()}
    total = sum(inv.values())
    return {lk: w / total for lk, w in inv.items()}


def _update_link_stress() -> None:
    """1스텝 링크 스트레스 갱신 — tick()에서만 호출된다.

    핵심 동작:
    - 혼잡 링크에 cost ≥ BYPASS_COST_THRESHOLD 설정 → 트래픽 우회 → cong_s=0 → 빠른 회복
    - 미대응 시 cong_s=0.5 지속 → 스트레스 1.0 포화
    """
    traffic = _compute_link_traffic()
    n_links = len(LINKS_LIST)

    for lk in LINKS_LIST:
        s = _link_stress[lk]

        # 트래픽 부하 스트레스 (해당 링크를 흐르는 트래픽 비율)
        load_s = traffic[lk] * _LOAD_GAIN * n_links

        # 혼잡 이벤트: cost가 낮으면 트래픽이 계속 흘러 혼잡 지속
        #             cost가 높으면 트래픽 우회로 혼잡 격리 → 스트레스 증가 없음
        if lk in _congested_links:
            if _ospf_costs[lk] < BYPASS_COST_THRESHOLD:
                cong_s = _CONG_GAIN
            else:
                cong_s = 0.0  # 트래픽 우회 성공 → 혼잡 격리
        else:
            cong_s = 0.0

        noise = _rng.gauss(0, _NOISE)
        _link_stress[lk] = max(0.0, min(1.0, s * _DECAY + load_s + cong_s + noise))


def _node_stress(node_id: str) -> float:
    """인접 링크 스트레스 평균 → 노드 스트레스 계산."""
    connected = NODE_LINKS[node_id]
    return sum(_link_stress[lk] for lk in connected) / len(connected)


def _stress_to_metrics(node_id: str) -> dict:
    s = _node_stress(node_id)
    # bandwidth: 950 Mbps (s=0) → 10 Mbps (s=1)
    bw  = max(1.0,  min(1000.0, 950.0 * (1 - s) + 10.0 * s + _rng.gauss(0, 10)))
    # latency: 3 ms (s=0) → 180 ms (s=1)
    lat = max(0.5,  3.0 + 177.0 * s + _rng.gauss(0, 1.5))
    # packet loss: 0 (s=0) → 0.04 (s=1)
    loss = max(0.0, min(0.5, 0.04 * s + _rng.gauss(0, 0.001)))
    return {
        "nodeId":     node_id,
        "bandwidth":  round(bw,   2),
        "latency":    round(lat,  2),
        "packetLoss": round(loss, 4),
        "timestamp":  int(time.time() * 1000),
        "tick":       _tick_count,
    }


# ── 공개 API ────────────────────────────────────────────────────────────────

def tick(n: int = 1) -> int:
    """시뮬레이션 시간을 n스텝 진행하고 현재 틱 번호를 반환한다."""
    global _tick_count
    with _lock:
        for _ in range(max(0, int(n))):
            _update_link_stress()
            _tick_count += 1
        return _tick_count


def get_tick() -> int:
    return _tick_count


def get_node_metrics(node_id: str) -> dict:
    """순수 조회 — 시뮬레이션 시간을 진행시키지 않는다 (관측 노이즈만 새로 샘플링)."""
    with _lock:
        return _stress_to_metrics(node_id)


def get_all_metrics() -> list[dict]:
    """순수 조회 — 시뮬레이션 시간을 진행시키지 않는다."""
    with _lock:
        return [_stress_to_metrics(n) for n in NODES]


def get_ospf_costs() -> dict:
    return dict(_ospf_costs)


def get_node_stress() -> dict:
    """디버그/대시보드용 노드별 스트레스 레벨 반환."""
    return {n: round(_node_stress(n), 4) for n in NODES}


def get_link_stress() -> dict:
    """디버그용 링크별 스트레스 반환."""
    return {lk: round(s, 4) for lk, s in _link_stress.items()}


def set_ospf_cost(link: str, cost: int) -> bool:
    parts = link.split("-")
    if len(parts) != 2:
        return False
    key = _link_key(parts[0], parts[1])
    with _lock:
        if key in _ospf_costs:
            _ospf_costs[key] = cost
            return True
    return False


def inject_congestion(link: str) -> bool:
    parts = link.split("-")
    if len(parts) != 2:
        return False
    key = _link_key(parts[0], parts[1])
    with _lock:
        if key in _ospf_costs:
            _congested_links.add(key)
            # 즉각 스트레스 최대치로 급등 (확실한 SLA 위반 유발)
            _link_stress[key] = 0.95
            return True
    return False


def clear_congestion(link: str) -> bool:
    parts = link.split("-")
    if len(parts) != 2:
        return False
    key = _link_key(parts[0], parts[1])
    with _lock:
        _congested_links.discard(key)
    return True


def get_congested_links() -> list[str]:
    return sorted(_congested_links)


def inject_attack(attack_type: str) -> bool:
    """보안 공격 시뮬레이션 주입 ('ddos' | 'portscan')."""
    global _attack_state
    if attack_type not in ("ddos", "portscan"):
        return False
    _attack_state = attack_type
    return True


def clear_attack() -> None:
    global _attack_state
    _attack_state = None


def get_security_metrics(node_id: str) -> dict:
    """현재 공격 상태에 따라 보안 피처를 포함한 메트릭을 반환한다 (순수 조회)."""
    base = get_node_metrics(node_id)
    if _attack_state == "ddos":
        syn_ratio        = round(_rng.uniform(0.45, 0.85), 3)
        unique_src_count = int(_rng.uniform(50,  300))
        pkt_rate         = int(_rng.uniform(15000, 50000))
    elif _attack_state == "portscan":
        syn_ratio        = round(_rng.uniform(0.20, 0.40), 3)
        unique_src_count = int(_rng.uniform(800, 2000))
        pkt_rate         = int(_rng.uniform(500,  3000))
    else:
        syn_ratio        = round(_rng.uniform(0.02, 0.10), 3)
        unique_src_count = int(_rng.uniform(10,   80))
        pkt_rate         = int(_rng.uniform(100, 1000))
    base.update({
        "syn_ratio":        syn_ratio,
        "unique_src_count": unique_src_count,
        "pkt_rate":         pkt_rate,
    })
    return base


def get_attack_state() -> str | None:
    return _attack_state


def reset_state(seed: int | None = None) -> None:
    """전체 상태 초기화 (에피소드 리셋용). seed를 주면 노이즈가 재현된다."""
    global _congested_links, _attack_state, _tick_count
    with _lock:
        _congested_links = set()
        _attack_state    = None
        _tick_count      = 0
        for lk in LINKS_LIST:
            _link_stress[lk] = 0.02
            _ospf_costs[lk] = 10
        if seed is not None:
            _rng.seed(seed)


# ── 자가 테스트 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 1) 조회는 시간을 진행시키지 않는다
    reset_state(seed=1)
    inject_congestion("r3-r4")
    before = dict(_link_stress)
    for _ in range(100):
        get_all_metrics(); get_node_metrics("r1"); get_security_metrics("r2")
    assert _link_stress == before and get_tick() == 0, "조회가 스트레스를 바꿨다"
    print("OK — 조회 100회 후 스트레스/틱 불변")

    # 2) tick만 시간을 진행시킨다: 미대응 혼잡은 포화, cost>=100이면 감쇠
    tick(5)
    assert get_tick() == 5 and _link_stress["r3-r4"] > 0.9, _link_stress
    set_ospf_cost("r3-r4", 100)
    s0 = _link_stress["r3-r4"]
    tick(10)
    assert _link_stress["r3-r4"] < s0 * 0.5, (s0, _link_stress["r3-r4"])
    print("OK — tick 진행 및 우회(cost>=100) 시 감쇠 확인")

    # 3) seed 재현성
    reset_state(seed=42); inject_congestion("r1-r2"); tick(7); a = get_link_stress()
    reset_state(seed=42); inject_congestion("r1-r2"); tick(7); b = get_link_stress()
    assert a == b, (a, b)
    print("OK — seed 재현성")

    # 4) 회복에 필요한 최소 틱 (물리 시정수): cost 100 적용 후 양 끝 노드 SLA 회복까지
    reset_state(seed=0); inject_congestion("r3-r4"); set_ospf_cost("r3-r4", 100)
    n = 0
    while any(m["latency"] >= 50 for m in get_all_metrics()) and n < 50:
        tick(); n += 1
    print(f"OK — cost=100 즉시 적용 시 SLA 회복까지 {n}틱 (측정 스크립트가 아닌 시뮬레이터의 시정수)")
    print("\n모든 자가 테스트 통과")
