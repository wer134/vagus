"""규모 확장 실험의 제어기들 (cowork/ROADMAP.md §6 (가), 2026-11-17).

전부 같은 인터페이스: `reset(sim)` → 에피소드 시작, `decide(node_metrics, link_metrics, costs)` →
(링크, cost) 또는 None. 시뮬레이터는 `simulation/netsim.py`의 NetSim이다.

  RuleV2      : 링크 관측 규칙 (api_server analytics_version=2와 같은 판단). 모델 없음
  Planner     : 라우팅 모델 + 국소 탐색 계획기. K4의 v3/v4를 일반화한 것
                  demand="true"      — 참 수요를 안다 (모델 완벽, 탐색만 제한)
                  demand="estimated" — 공칭 수요에서 시작해 관측 부하와 어긋나면 재추정 (K4 v4)
  search()    : 국소 탐색 엔진. Planner와 오라클이 같이 쓴다

K4에서는 cost 배치 15,625개를 전수 탐색했다. Abilene(14링크)은 5^14 ≈ 6.1×10^9개라 불가능하다 —
계획기는 국소 탐색으로 물러나고, 그 탐색이 놓치는 것이 학습 정책이 들어설 자리 후보다.

탐색 엔진 (측정 전에 고정한 설계):
  이웃       = 링크 하나의 cost를 다른 값으로 (L × 4개)
  등반       = 최량 개선 (위반량, 최대 이용률) 사전식. 개선이 없으면 멈춤
  가지치기   = 실행 가능해지면, 현재 배치에서 바꾼 링크를 하나씩 되돌려 보고 여전히 가능하면 되돌린다
  재시작     = 막히면 현재 배치에서 링크 1~3개를 무작위로 바꿔 다시 등반. 평가 예산을 다 쓸 때까지
  결과       = 실행 가능한 배치 중 변경 수 최소 → 최대 이용률 최소. 없으면 (best_effort일 때) 위반량 최소
"""
import os
import random
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# simulation/은 뒤에 붙인다 — simulation/topology.py(Mininet)가 ai-engine/topology.py를 가리면 안 된다
sys.path.append(os.path.join(_HERE, "..", "simulation"))

from anomaly_detector import classify_links, root_cause_analysis_v2  # noqa: E402
from netsim import NetSim, OSPF_COSTS, SLA_LATENCY_MS, SLA_PACKET_LOSS  # noqa: E402

PLANNER_BUDGET = 3_000      # 계획 1회당 cost 배치 평가 수 상한
ORACLE_BUDGET  = 30_000     # 오라클 (풀이 존재 판정) — 같은 엔진, 예산 10배
DEMAND_MISMATCH_TOL_UTIL = 0.025   # K4 v4의 0.05 수요 단위 / 용량 2.0과 같은 비율
NODE_STRESS_SLA = 0.25      # 노드 스트레스가 이 값 미만이어야 손실 < 1 % (0.04 × 0.25)


def node_violation(node_metrics: list[dict]) -> bool:
    """api_server diagnose()의 이상 판정과 같다 (> 기준)."""
    return any(m["latency"] > SLA_LATENCY_MS or m["packetLoss"] > SLA_PACKET_LOSS for m in node_metrics)


# ── 목적 함수 (배치: C[B, 링크] → (위반량[B], 최대값[B])) ─────────────────────

def strict_objective(sim: NetSim, demand: np.ndarray, faults: set[int]):
    """운영자 규칙: 고장 링크에 트래픽 0, 모든 링크 이용률 ≤ 1. (위반량, 최대 이용률)."""
    cap = sim.capacity
    fidx = sorted(faults)

    def f(C: np.ndarray):
        load = sim.router.loads_batch(C, demand)
        util = load / cap
        viol = np.maximum(0.0, util - 1.0).sum(axis=1)
        if fidx:
            viol = viol + (load[:, fidx] / cap[fidx]).sum(axis=1)
        return np.round(viol, 9), util.max(axis=1)
    return f


def sla_objective(sim: NetSim, demand: np.ndarray):
    """참 SLA: 노이즈 없는 정상상태에서 모든 노드 스트레스 < 0.25. (위반량, 최대 노드 스트레스)."""
    def f(C: np.ndarray):
        s = sim.steady_node_stress_batch(C, demand)
        return np.round(np.maximum(0.0, s - (NODE_STRESS_SLA - 1e-6)).sum(axis=1), 9), s.max(axis=1)
    return f


# ── 탐색 엔진 ─────────────────────────────────────────────────────────────────

EXTRA_RESTARTS = 5     # 첫 풀이를 찾은 뒤 변경 수를 줄여 보려고 더 해 보는 재시작 횟수
_OTHERS = {c: [o for o in OSPF_COSTS if o != c] for c in OSPF_COSTS}


def search(objective, current: tuple, budget: int, rng: random.Random,
           best_effort: bool = False) -> tuple | None:
    """current에서 출발해 objective 위반 0인 배치를 찾는다. 반환: 목표 배치(tuple) 또는 None."""
    cur = np.array(current, dtype=np.int64)
    L = len(cur)
    evals = 0

    def ev(X: np.ndarray):
        nonlocal evals
        evals += len(X)
        return objective(X)

    def neighbors(x: np.ndarray) -> np.ndarray:
        rows = []
        for i in range(L):
            for c in _OTHERS[int(x[i])]:
                y = x.copy(); y[i] = c; rows.append(y)
        return np.array(rows)

    v0, m0 = ev(cur[None])
    if v0[0] == 0:
        return tuple(int(c) for c in cur)
    best_feasible, best_feasible_key = None, None
    best_any, best_any_key = cur, (float(v0[0]), float(m0[0]))

    def prune(x: np.ndarray) -> np.ndarray:
        for i in range(L):
            if x[i] != cur[i] and evals < budget:
                y = x.copy(); y[i] = cur[i]
                if ev(y[None])[0][0] == 0:
                    x = y
        return x

    def climb(x: np.ndarray) -> None:
        nonlocal best_feasible, best_feasible_key, best_any, best_any_key
        v, m = ev(x[None]); kx = (float(v[0]), float(m[0]))
        while kx[0] > 0 and evals < budget:
            N = neighbors(x)
            v, m = ev(N)
            j = int(np.lexsort((m, v))[0])
            kj = (float(v[j]), float(m[j]))
            if kj >= kx:
                break
            x, kx = N[j], kj
        if kx[0] == 0:
            x = prune(x)
            kx = (0.0, float(ev(x[None])[1][0]))
            k = (int(np.sum(x != cur)), kx[1])
            if best_feasible_key is None or k < best_feasible_key:
                best_feasible, best_feasible_key = x, k
        if kx < best_any_key:
            best_any, best_any_key = x, kx

    climb(cur)
    extra = 0
    while evals < budget and extra < EXTRA_RESTARTS:
        y = cur.copy()
        for i in rng.sample(range(L), rng.choice((1, 2, 3))):
            y[i] = rng.choice(_OTHERS[int(y[i])])
        climb(y)
        if best_feasible is not None:
            extra += 1

    pick = best_feasible if best_feasible is not None else (best_any if best_effort else None)
    return None if pick is None else tuple(int(c) for c in pick)


def oracle_solvable(sim: NetSim, demand: np.ndarray, faults: set[int], seed: int = 0) -> dict:
    """이 상태에 SLA를 만족하는 cost 배치가 있는가 — 탐색 기반 (전수 탐색이 불가능한 규모용).

    운영자 규칙(strict)으로 먼저 찾고(그것을 만족하면 SLA도 만족한다 — 이용률 ≤ 1이면 링크
    스트레스 ≤ 0.2, 고장 링크는 부하 0), 없으면 SLA 목적으로 직접 찾는다. "없음"은 이 예산의
    탐색이 못 찾았다는 뜻이다 — K4에서는 전수 탐색과의 일치를 따로 확인한다.
    """
    rng = random.Random(seed)
    base = tuple([10] * sim.L)
    x = search(strict_objective(sim, demand, faults), base, ORACLE_BUDGET // 2, rng)
    if x is not None:
        assert sim.steady_sla_ok(x, demand), "strict 풀이는 SLA 풀이여야 한다"
        return {"solvable": True, "via": "strict", "costs": x}
    x = search(sla_objective(sim, demand), base, ORACLE_BUDGET // 2, rng)
    if x is not None and sim.steady_sla_ok(x, demand):
        return {"solvable": True, "via": "sla", "costs": x}
    return {"solvable": False, "via": None, "costs": None}


# ── 제어기 ────────────────────────────────────────────────────────────────────

class NoOp:
    name = "noop"

    def reset(self, sim: NetSim, seed: int = 0):
        pass

    def decide(self, nm, lm, costs):
        return None


class RuleV2:
    """링크 관측 규칙 — 고장 링크를 cost 100으로 격리. api_server analytics_version=2와 같다."""
    name = "rule_v2"

    def reset(self, sim: NetSim, seed: int = 0):
        pass

    def decide(self, nm, lm, costs):
        if not node_violation(nm):
            return None
        rc = root_cause_analysis_v2(lm, costs)
        return (rc, 100) if rc else None


class Planner:
    """라우팅 모델 + 국소 탐색. 이상이 있는 사이클에만 계획·실행한다 (K4 v3/v4와 같은 규약)."""

    def __init__(self, demand: str = "estimated", budget: int = PLANNER_BUDGET):
        assert demand in ("true", "estimated")
        self.demand_mode = demand
        self.budget = budget
        self.name = f"planner_{demand}"

    def reset(self, sim: NetSim, seed: int = 0):
        self.sim = sim
        self.rng = random.Random(seed)
        self.faults: set[int] = set()
        self.target: tuple | None = None
        self.d_est = np.ones(len(sim.topo.pairs))        # 공칭 수요 — 운영자가 아는 장기 트래픽 행렬
        self.replans = 0
        self.reestimates = 0

    def _demand(self, lm, costs_t):
        if self.demand_mode == "true":
            return self.sim.demand
        R = self.sim.router.matrix(costs_t)
        cap = self.sim.capacity
        obs = np.array([m["utilization"] for m in lm]) * cap
        if np.max(np.abs(R @ self.d_est - obs) / cap) > DEMAND_MISMATCH_TOL_UTIL:
            self.d_est = np.clip(self.d_est + np.linalg.pinv(R) @ (obs - R @ self.d_est), 0.0, None)
            self.reestimates += 1
            self.target = None                            # 모델이 바뀌었다 → 다시 계획
        return self.d_est

    def decide(self, nm, lm, costs):
        if not node_violation(nm):
            return None
        sim = self.sim
        state = classify_links(lm)
        self.faults |= {sim.link_index[lk] for lk, s in state.items() if s == "fault"}
        cur = tuple(costs[lk] for lk in sim.topo.links)
        d = self._demand(lm, cur)
        obj = strict_objective(sim, d, self.faults)
        if self.target is None or obj(np.array([self.target]))[0][0] > 0:
            # 두 계획기 모두 best_effort — 차이는 수요를 아느냐 하나뿐이어야 한다. 운영자 규칙(strict)에
            # 정확한 풀이가 없어도 과부하가 가장 적은 배치는 SLA를 만족할 수 있다 (TRAIN 파일럿에서
            # 참 수요 계획기가 이 경우 규칙으로 물러나 추정 계획기보다 나쁘게 나온 것을 보고 맞췄다)
            self.target = search(obj, cur, self.budget, self.rng, best_effort=True)
            self.replans += 1
            if self.target is None:
                # 풀이를 못 찾았다 — 고장 링크 격리만이라도 (RuleV2와 같은 폴백)
                rc = root_cause_analysis_v2(lm, costs)
                return (rc, 100) if rc else None
        diff = [i for i in range(sim.L) if self.target[i] != cur[i]]
        if not diff:
            return None
        diff.sort(key=lambda i: (i not in self.faults, i))   # 고장 링크 격리가 먼저
        i = diff[0]
        return sim.topo.links[i], self.target[i]


# ── 자가 테스트 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import itertools
    from netsim import K4, ABILENE

    # 1) K4에서 탐색 엔진 vs 전수 탐색: 무작위 상태 120개에서 풀이 존재 판정이 일치
    sim = NetSim(K4)
    rnd = random.Random(7)
    allc = list(itertools.product(OSPF_COSTS, repeat=6))
    agree = 0
    for t in range(120):
        sim.reset()
        weak = rnd.choice(K4.links); sim.set_weak(weak)
        for lk in rnd.sample(K4.links, rnd.choice((1, 2))):
            sim.inject_congestion(lk)
        if rnd.random() < 0.5:
            sim.scale_node_demand(rnd.choice(K4.nodes), 2.0)
        exhaustive = any(sim.steady_sla_ok(c) for c in allc)
        found = oracle_solvable(sim, sim.demand, set(sim.congested), seed=t)["solvable"]
        assert not (found and not exhaustive), "탐색이 존재하지 않는 풀이를 찾았다"
        agree += found == exhaustive
    print(f"OK — K4 탐색 오라클 vs 전수 탐색: {agree}/120 일치 (탐색은 거짓 양성이 없다)")
    assert agree == 120

    # 2) 계획기(참 수요)는 K4 약한 링크 우회 문제를 푼다 (전수 탐색 계획기의 4개 변경 풀이와 같은 규모)
    sim.reset(seed=3); sim.set_weak("r1-r2"); sim.inject_congestion("r1-r3")
    f = strict_objective(sim, sim.demand, {sim.link_index["r1-r3"]})
    x = search(f, tuple([10] * 6), PLANNER_BUDGET, random.Random(0))
    assert x is not None and f(np.array([x]))[0][0] == 0 and sum(c != 10 for c in x) == 4, x
    print(f"OK — K4 약한 링크 우회: 국소 탐색이 4개 변경 풀이를 찾음 {dict(zip(K4.links, x))}")

    # 3) Abilene: 단일 혼잡의 풀이를 예산 안에서 찾고, 실제 시뮬레이터에서 SLA를 회복한다.
    #    KCY-IND는 쓰지 않는다 — 서-동 절단 링크 둘(KCY-IND, HOU-ATL) 중 하나라 격리하면 서-동
    #    30쌍이 HOU-ATL 하나에 몰려 풀이가 없다(오라클이 실제로 그렇게 답한다)
    ab = NetSim(ABILENE)
    ab.reset(seed=2); ab.inject_congestion("KCY-IND")
    assert not oracle_solvable(ab, ab.demand, set(ab.congested), seed=0)["solvable"]
    ab.reset(seed=1); ab.inject_congestion("IND-ATL")
    res = oracle_solvable(ab, ab.demand, set(ab.congested), seed=0)
    assert res["solvable"], res
    for i, c in enumerate(res["costs"]):
        ab.set_cost(i, c)
    ab.tick(60)
    from netsim import sla_ok
    assert sla_ok(ab.node_metrics())
    print(f"OK — Abilene IND-ATL: 오라클 풀이({res['via']}) "
          f"{ {ABILENE.links[i]: c for i, c in enumerate(res['costs']) if c != 10} } → 60틱 뒤 SLA 정상")
    print("\n모든 자가 테스트 통과")
