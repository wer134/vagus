"""랜덤 네트워크 메트릭 생성기 — 링크 스트레스 기반 모델.

핵심 피드백 루프:
  혼잡 링크에 높은 OSPF cost → 트래픽 우회 → 해당 링크 혼잡 완화 → 지연/손실 감소
  → 에이전트의 행동이 다음 관측값에 직접 반영됨

링크 스트레스 → 노드 메트릭:
  각 노드의 메트릭은 인접 링크 스트레스의 평균으로 계산한다.
"""
import random
import time

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
    """메트릭 조회마다 1스텝 링크 스트레스 갱신.

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

        noise = random.gauss(0, _NOISE)
        _link_stress[lk] = max(0.0, min(1.0, s * _DECAY + load_s + cong_s + noise))


def _node_stress(node_id: str) -> float:
    """인접 링크 스트레스 평균 → 노드 스트레스 계산."""
    connected = NODE_LINKS[node_id]
    return sum(_link_stress[lk] for lk in connected) / len(connected)


def _stress_to_metrics(node_id: str) -> dict:
    s = _node_stress(node_id)
    # bandwidth: 950 Mbps (s=0) → 10 Mbps (s=1)
    bw  = max(1.0,  min(1000.0, 950.0 * (1 - s) + 10.0 * s + random.gauss(0, 10)))
    # latency: 3 ms (s=0) → 180 ms (s=1)
    lat = max(0.5,  3.0 + 177.0 * s + random.gauss(0, 1.5))
    # packet loss: 0 (s=0) → 0.04 (s=1)
    loss = max(0.0, min(0.5, 0.04 * s + random.gauss(0, 0.001)))
    return {
        "nodeId":     node_id,
        "bandwidth":  round(bw,   2),
        "latency":    round(lat,  2),
        "packetLoss": round(loss, 4),
        "timestamp":  int(time.time() * 1000),
    }


# ── 공개 API ────────────────────────────────────────────────────────────────

def get_node_metrics(node_id: str) -> dict:
    _update_link_stress()
    return _stress_to_metrics(node_id)


def get_all_metrics() -> list[dict]:
    _update_link_stress()
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
    if key in _ospf_costs:
        _ospf_costs[key] = cost
        return True
    return False


def inject_congestion(link: str) -> bool:
    parts = link.split("-")
    if len(parts) != 2:
        return False
    key = _link_key(parts[0], parts[1])
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
    _congested_links.discard(key)
    return True


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
    """현재 공격 상태에 따라 보안 피처를 포함한 메트릭을 반환한다."""
    base = get_node_metrics(node_id)
    if _attack_state == "ddos":
        syn_ratio        = round(random.uniform(0.45, 0.85), 3)
        unique_src_count = int(random.uniform(50,  300))
        pkt_rate         = int(random.uniform(15000, 50000))
    elif _attack_state == "portscan":
        syn_ratio        = round(random.uniform(0.20, 0.40), 3)
        unique_src_count = int(random.uniform(800, 2000))
        pkt_rate         = int(random.uniform(500,  3000))
    else:
        syn_ratio        = round(random.uniform(0.02, 0.10), 3)
        unique_src_count = int(random.uniform(10,   80))
        pkt_rate         = int(random.uniform(100, 1000))
    base.update({
        "syn_ratio":        syn_ratio,
        "unique_src_count": unique_src_count,
        "pkt_rate":         pkt_rate,
    })
    return base


def get_attack_state() -> str | None:
    return _attack_state


def reset_state() -> None:
    """전체 상태 초기화 (에피소드 리셋용)."""
    global _congested_links, _attack_state
    _congested_links = set()
    _attack_state    = None
    for lk in LINKS_LIST:
        _link_stress[lk] = 0.02
        _ospf_costs[lk] = 10
