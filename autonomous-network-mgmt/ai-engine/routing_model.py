"""라우팅 모델 기반 계획기 — Analytics v3 (cowork/ROADMAP.md §6, 2026-11-15).

규칙(v1, v2)은 "고장 링크의 cost를 100으로 올린다" 한 가지 조치만 안다. 링크에 용량이 있으면
그 조치가 우회 트래픽을 약한 링크로 몰아 새 위반을 만들 수 있고, 그때 필요한 것은 여러 링크의
cost를 함께 바꿔 우회 경로를 고르는 일이다. 이 모듈은 그것을 **모델로 탐색**한다.

가진 것 (전부 관측하거나 설정으로 아는 값):
  - 토폴로지와 현재 OSPF cost      → 최단경로(ECMP)를 스스로 계산할 수 있다
  - 링크 용량 (SNMP ifSpeed)        → 관측
  - 노드 쌍별 수요                  → 직접 관측되지 않는다. cost가 전부 같을 때는 각 쌍이 직결
                                      링크만 쓰므로 그때의 링크 부하가 곧 수요다 (estimate_demand)

하는 일: cost 배치 5^6개를 전수로 대입해 (1) 고장 링크에 트래픽이 실리지 않고 (2) 어떤 링크도
이용률 1을 넘지 않는 배치 중, 현재 배치에서 가장 적게 바꾸는 것을 고른다.

이것은 **시뮬레이터와 같은 라우팅 모델을 가진** 계획기다 — 모델이 맞는 한 풀이가 있는 시나리오는
전부 푼다. 그래서 이것은 학습 계층이 넘어야 할 기준선이 아니라 **상한선**이며, 학습이 의미를
가지는 자리는 이 모델이 틀리는 곳(수요가 바뀌거나 알 수 없을 때)이다. 아래 자가 테스트가
"모델이 시뮬레이터와 일치한다"는 사실을 명시적으로 확인한다.
"""
import itertools

import numpy as np

from topology import LINKS, NODES, NODE_LINKS, LINK_ENDPOINTS, OSPF_COSTS

PAIRS: list[tuple[str, str]] = [(s, d) for i, s in enumerate(NODES) for d in NODES[i + 1:]]


def _simple_paths(src: str, dst: str) -> list[list[str]]:
    paths: list[list[str]] = []

    def walk(node: str, visited: frozenset, acc: list[str]) -> None:
        if node == dst:
            paths.append(list(acc))
            return
        for lk in NODE_LINKS[node]:
            a, b = LINK_ENDPOINTS[lk]
            nxt = b if a == node else a
            if nxt not in visited:
                acc.append(lk)
                walk(nxt, visited | {nxt}, acc)
                acc.pop()

    walk(src, frozenset({src}), [])
    return paths


_PATHS = {pair: _simple_paths(*pair) for pair in PAIRS}
_DIRECT = {pair: "-".join(pair) if "-".join(pair) in LINKS else "-".join(reversed(pair)) for pair in PAIRS}

ALL_ASSIGNMENTS: list[tuple[int, ...]] = list(itertools.product(OSPF_COSTS, repeat=len(LINKS)))


def link_loads(costs: dict[str, int], demand: dict[tuple[str, str], float]) -> dict[str, float]:
    """OSPF cost 최단경로(ECMP 균등 분배)로 수요를 실은 링크별 부하."""
    load = {lk: 0.0 for lk in LINKS}
    for pair, paths in _PATHS.items():
        d = demand.get(pair, 0.0)
        if d <= 0:
            continue
        pc = [sum(costs[lk] for lk in p) for p in paths]
        best = min(pc)
        chosen = [p for p, c in zip(paths, pc) if c == best]
        for p in chosen:
            for lk in p:
                load[lk] += d / len(chosen)
    return load


def estimate_demand(link_metrics: list[dict], costs: dict[str, int]) -> dict[tuple[str, str], float] | None:
    """링크 부하에서 쌍별 수요를 읽는다. cost가 전부 같을 때만 가능 — 아니면 None.

    cost가 균일하면 완전그래프의 각 쌍은 직결 링크만 쓰므로 링크 부하 = 그 쌍의 수요다.
    그 밖의 배치에서는 부하에서 수요를 유일하게 복원할 수 없다(트래픽 행렬 추정 문제).
    """
    if len(set(costs.values())) != 1:
        return None
    lm = {m["linkId"]: m for m in link_metrics}
    return {pair: lm[_DIRECT[pair]]["utilization"] * lm[_DIRECT[pair]]["capacity"] for pair in PAIRS}


def routing_matrix(costs: dict[str, int]) -> np.ndarray:
    """R[링크, 쌍] = 그 쌍의 수요 중 그 링크를 지나는 비율. 링크 부하 = R @ 수요."""
    R = np.zeros((len(LINKS), len(PAIRS)))
    for j, pair in enumerate(PAIRS):
        unit = link_loads(costs, {pair: 1.0})
        for i, lk in enumerate(LINKS):
            R[i, j] = unit[lk]
    return R


# 관측 부하와 모델 예측이 이만큼 어긋나면 수요 추정이 낡았다고 본다 (수요 단위)
DEMAND_MISMATCH_TOL = 0.05


def model_mismatch(demand: dict, costs: dict[str, int], link_metrics: list[dict]) -> float:
    """현재 수요 추정으로 예측한 링크 부하와 관측 부하의 최대 차이."""
    lm = {m["linkId"]: m for m in link_metrics}
    pred = link_loads(costs, demand)
    return max(abs(pred[lk] - lm[lk]["utilization"] * lm[lk]["capacity"]) for lk in LINKS)


def reestimate_demand(prev: dict, costs: dict[str, int], link_metrics: list[dict]) -> dict:
    """관측 부하에 맞도록 수요 추정을 고친다 — 이전 추정에서 가장 덜 움직이는 해.

    cost가 균일하면 정확히 읽힌다(estimate_demand). 그렇지 않으면 R @ d = 부하는 해가 여럿이다
    (격리된 링크의 행이 0이라 계수가 모자란다). 그중 이전 추정과 가장 가까운 것을 고른다:
    d = prev + pinv(R) @ (부하 − R @ prev), 음수는 0으로. 정확한 복원이 아니라 **일관된 추정**이며,
    cost 배치가 바뀌면 R이 달라져 새 방정식이 생기므로 사이클을 거듭하며 좁혀진다.
    """
    exact = estimate_demand(link_metrics, costs)
    if exact is not None:
        return exact
    lm = {m["linkId"]: m for m in link_metrics}
    load = np.array([lm[lk]["utilization"] * lm[lk]["capacity"] for lk in LINKS])
    d0 = np.array([prev[pair] for pair in PAIRS])
    R = routing_matrix(costs)
    d = np.clip(d0 + np.linalg.pinv(R) @ (load - R @ d0), 0.0, None)
    return {pair: float(v) for pair, v in zip(PAIRS, d)}


class Planner:
    """수요 추정치 하나에 대한 계획기. 15,625개 배치의 부하를 한 번만 계산해 둔다."""

    def __init__(self, demand: dict[tuple[str, str], float]):
        self.demand = dict(demand)
        self._loads = [
            link_loads(dict(zip(LINKS, a)), self.demand) for a in ALL_ASSIGNMENTS
        ]

    def plan(
        self,
        costs: dict[str, int],
        capacity: dict[str, float],
        faults: set[str],
    ) -> dict[str, int] | None:
        """바꿔야 할 {링크: cost}. 현재 배치가 이미 조건을 만족하면 {}. 풀이가 없으면 None."""
        current = tuple(costs[lk] for lk in LINKS)
        best, best_n = None, None
        for a, load in zip(ALL_ASSIGNMENTS, self._loads):
            if any(load[lk] > 1e-9 for lk in faults):
                continue
            if any(load[lk] > capacity[lk] + 1e-9 for lk in LINKS):
                continue
            n = sum(1 for x, y in zip(a, current) if x != y)
            if best_n is None or n < best_n:
                best, best_n = a, n
                if n == 0:
                    break
        if best is None:
            return None
        return {lk: c for lk, c, cur in zip(LINKS, best, current) if c != cur}


def next_action(changes: dict[str, int], faults: set[str]) -> tuple[str, int] | None:
    """계획에서 이번 사이클에 실행할 조치 하나. 고장 링크 격리가 먼저다."""
    if not changes:
        return None
    for lk in LINKS:
        if lk in changes and lk in faults:
            return lk, changes[lk]
    for lk in LINKS:
        if lk in changes:
            return lk, changes[lk]
    return None


if __name__ == "__main__":
    import os
    import random
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "simulation"))
    import metric_generator as mg

    uniform = {pair: 1.0 for pair in PAIRS}

    # 1) 이 모델은 시뮬레이터의 라우팅과 같다 — 계획기가 "완벽한 모델"을 가졌다는 뜻이다
    rnd = random.Random(0)
    mg.reset_state()
    for _ in range(300):
        costs = {lk: rnd.choice(OSPF_COSTS) for lk in LINKS}
        for lk, c in costs.items():
            mg.set_ospf_cost(lk, c)
        sim, mine = mg.get_link_loads(), link_loads(costs, uniform)
        assert all(abs(sim[lk] - mine[lk]) < 1e-9 for lk in LINKS), (costs, sim, mine)
    mg.reset_state()
    print("OK — 라우팅 모델이 시뮬레이터와 일치 (무작위 cost 배치 300개)")

    # 2) 수요 추정: cost가 균일하면 링크 부하에서 그대로 읽힌다
    mg.set_demand({("r1", "r2"): 1.7, ("r3", "r4"): 0.4})
    est = estimate_demand(mg.get_link_metrics(), mg.get_ospf_costs())
    assert abs(est[("r1", "r2")] - 1.7) < 1e-6 and abs(est[("r3", "r4")] - 0.4) < 1e-6, est
    mg.set_ospf_cost("r1-r2", 100)
    assert estimate_demand(mg.get_link_metrics(), mg.get_ospf_costs()) is None
    mg.reset_state()
    print("OK — 수요 추정: 균일 cost에서 정확, 그 밖에서는 None")

    planner = Planner(uniform)
    base = {lk: 10 for lk in LINKS}
    cap = {lk: 2.0 for lk in LINKS}

    # 3) 정상 상태 → 바꿀 것 없음
    assert planner.plan(base, cap, set()) == {}
    # 4) 단일 혼잡·균일 용량 → 그 링크 하나만 바꾼다
    ch = planner.plan(base, cap, {"r3-r4"})
    assert list(ch) == ["r3-r4"], ch
    print(f"OK — 단일 혼잡: {ch}")

    # 5) 약한 링크가 우회 경로에 있으면 여러 링크를 함께 바꿔 우회를 돌린다 (규칙 v1·v2가 못 푸는 경우)
    weak = {**cap, "r1-r2": 1.2}
    ch = planner.plan(base, weak, {"r1-r3"})
    assert ch is not None and len(ch) == 4 and "r1-r3" in ch, ch
    after = {**base, **ch}
    load = link_loads(after, uniform)
    assert load["r1-r3"] == 0 and all(load[lk] <= weak[lk] + 1e-9 for lk in LINKS), load
    # 한 번에 하나씩 실행해도 같은 목표로 수렴한다 (매 사이클 재계획)
    cur, steps = dict(base), 0
    while True:
        todo = planner.plan(cur, weak, {"r1-r3"})
        act = next_action(todo, {"r1-r3"})
        if act is None:
            break
        cur[act[0]] = act[1]; steps += 1
        assert steps <= 6
    assert steps == 4 and next_action(planner.plan(cur, weak, {"r1-r3"}), {"r1-r3"}) is None
    print(f"OK — 약한 링크 우회: {ch} (4개 변경, 사이클당 1개씩 4 사이클에 수렴)")

    # 7) 수요 재추정: 격리 뒤(cost 비균일) 한 쌍의 수요가 2배가 되면 낡은 모델은 어긋나고,
    #    재추정한 모델은 관측 부하를 다시 설명한다
    mg.set_capacity_mode("finite")
    try:
        mg.reset_state(); mg.inject_congestion("r3-r4"); mg.set_ospf_cost("r3-r4", 100)
        costs_now = mg.get_ospf_costs()
        assert model_mismatch(uniform, costs_now, mg.get_link_metrics()) < 1e-9
        mg.set_demand({("r1", "r3"): 2.0})
        lm = mg.get_link_metrics()
        assert abs(model_mismatch(uniform, costs_now, lm) - 1.0) < 1e-9
        est = reestimate_demand(uniform, costs_now, lm)
        assert model_mismatch(est, costs_now, lm) < 1e-6, est
        # 정확한 복원은 아니다 — 부하가 같은 해 중 이전 추정에 가장 가까운 것 (참값 2.0)
        assert 1.8 < est[("r1", "r3")] < 2.0 and est[("r3", "r4")] > 1.0, est
        print(f"OK — 수요 재추정: r1-r3 참값 2.0 → 추정 {est[('r1','r3')]:.3f} "
              f"(r3-r4 {est[('r3','r4')]:.3f}), 관측 부하와는 일치")
    finally:
        mg.set_capacity_mode("unlimited"); mg.reset_state()

    # 6) 풀이가 없으면 None — 한 노드의 링크 둘이 고장이면 남은 하나로 수요 3이 몰린다
    assert planner.plan(base, cap, {"r3-r4", "r1-r4"}) is None
    print("OK — 풀이 없는 시나리오 → None")
    print("\n모든 자가 테스트 통과")
