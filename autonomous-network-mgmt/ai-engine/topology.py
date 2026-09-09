"""토폴로지 상수 — AI 엔진 쪽의 단일 출처.

anomaly_detector / environment.network_env / api_server가 여기서 import한다.
simulation/metric_generator.py는 별도 배포 단위(가상 장비)라 자체 상수를 갖는다 —
동일성은 아래 자가 테스트로 확인한다 (cowork/AUDIT_2026-09-09.md C8).
Java 쪽 AiEngineClient.NODE_ORDER / LINK_ORDER도 같은 순서여야 한다.
"""

NODES      = ["r1", "r2", "r3", "r4"]
LINKS      = ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]
OSPF_COSTS = [10, 20, 50, 100, 200]

N_NODES = len(NODES)
N_LINKS = len(LINKS)

LINK_ENDPOINTS: dict[str, tuple[str, str]] = {lk: tuple(lk.split("-")) for lk in LINKS}

# 노드별 인접 링크 (LINKS 순서 유지)
NODE_LINKS: dict[str, list[str]] = {
    n: [lk for lk in LINKS if n in LINK_ENDPOINTS[lk]] for n in NODES
}

# 관측 정규화 상한 (NetworkEnv / api_server 공용)
MAX_BW   = 1000.0
MAX_LAT  = 200.0
MAX_COST = 200.0

# 트래픽 우회 임계 cost — metric_generator.BYPASS_COST_THRESHOLD와 같아야 한다
BYPASS_COST = 100


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "simulation"))
    import metric_generator as mg
    assert mg.NODES == NODES and mg.LINKS_LIST == LINKS, (mg.NODES, mg.LINKS_LIST)
    assert {n: sorted(v) for n, v in mg.NODE_LINKS.items()} == {n: sorted(v) for n, v in NODE_LINKS.items()}
    assert mg.BYPASS_COST_THRESHOLD == BYPASS_COST
    assert BYPASS_COST in OSPF_COSTS
    print("OK — simulation/metric_generator 토폴로지와 일치")
