"""토폴로지 일반화 시뮬레이터 (cowork/ROADMAP.md §6 (가) 규모 확장, 2026-11-17).

`metric_generator.py`는 4노드 완전그래프(K4) 전용 싱글턴이다 — 경로를 전수 열거하고 상수가
모듈 전역에 박혀 있다. 규모 확장 실험은 임의 토폴로지에서, 학습 환경 안에서 수십만 스텝을 돌려야
하므로 HTTP를 거치지 않는 인스턴스형 시뮬레이터가 필요하다.

**물리는 `metric_generator`의 finite 용량 모드와 같다** (상수, 갱신식, 난수 소비 순서까지).
다른 것은 라우팅 계산 방법 하나다: 단순 경로 전수 열거 대신 최단거리 행렬과 최단경로 개수로
"그 링크를 지나는 최단경로의 비율"을 구한다. 경로 단위 ECMP 균등 분배라는 의미는 같다.
아래 자가 테스트가 K4에서 두 시뮬레이터의 링크 부하와 궤적이 일치함을 확인한다.

용량 규약: 링크 용량 = CAPACITY_FACTOR(2.0) × 공칭 부하(균일 cost·공칭 수요에서의 부하).
K4에서는 공칭 부하가 링크마다 1.0이라 용량 2.0 — metric_generator의 기본값과 같다. 약한 링크는
WEAK_FACTOR(1.2) × 공칭 부하 (K4의 1.2와 같다).
"""
import random
from dataclasses import dataclass, field

import numpy as np

# ── 물리 상수 (metric_generator와 같아야 한다 — 자가 테스트가 확인) ───────────────
DECAY         = 0.90
NOISE         = 0.015
UTIL_GAIN     = 0.02
OVERLOAD_GAIN = 0.30
CONG_GAIN     = 0.50
INIT_STRESS   = 0.02
OSPF_COSTS    = (10, 20, 50, 100, 200)
DEFAULT_COST  = 10

CAPACITY_FACTOR = 2.0
WEAK_FACTOR     = 1.2

# 노드 SLA (anomaly_detector / capacity_probe와 같다)
SLA_LATENCY_MS  = 50.0
SLA_PACKET_LOSS = 0.01


@dataclass(frozen=True)
class Topology:
    name: str
    nodes: tuple[str, ...]
    links: tuple[str, ...]          # "a-b" — 표기 순서는 자유, 끝점은 split("-")
    node_links: dict = field(default_factory=dict, compare=False)

    @staticmethod
    def build(name: str, nodes: list[str], links: list[str]) -> "Topology":
        for lk in links:
            a, b = lk.split("-")
            assert a in nodes and b in nodes and a != b, lk
        assert len(set(frozenset(lk.split("-")) for lk in links)) == len(links), "중복 링크"
        nl = {n: tuple(lk for lk in links if n in lk.split("-")) for n in nodes}
        assert all(nl[n] for n in nodes), "고립 노드"
        return Topology(name, tuple(nodes), tuple(links), nl)

    @property
    def pairs(self) -> list[tuple[int, int]]:
        n = len(self.nodes)
        return [(i, j) for i in range(n) for j in range(i + 1, n)]


# K4 — metric_generator와 같은 노드·링크 순서
K4 = Topology.build(
    "k4",
    ["r1", "r2", "r3", "r4"],
    ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"],
)

# Abilene (Internet2, 11 노드 · 14 링크로 흔히 쓰이는 판). 링크 수 14 → cost 배치 5^14 ≈ 6.1×10^9개라
# 전수 탐색이 불가능하다 — 규모 확장의 목적이 바로 이것이다.
ABILENE = Topology.build(
    "abilene",
    ["SEA", "SNV", "LAX", "DEN", "KCY", "HOU", "CHI", "IND", "ATL", "WAS", "NYC"],
    ["SEA-SNV", "SEA-DEN", "SNV-LAX", "SNV-DEN", "LAX-HOU", "DEN-KCY", "KCY-HOU",
     "KCY-IND", "HOU-ATL", "IND-CHI", "IND-ATL", "CHI-NYC", "ATL-WAS", "NYC-WAS"],
)

TOPOLOGIES = {t.name: t for t in (K4, ABILENE)}


class Router:
    """cost 벡터 → 라우팅 행렬 R[링크, 쌍] (그 쌍의 수요 중 그 링크를 지나는 비율).

    쌍마다 단순 경로를 전부 열거해 두고(metric_generator와 같은 정의), cost가 주어지면 최소 비용
    경로들에 수요를 균등 분배한다 — 경로 단위 ECMP. Abilene은 단순 경로가 448개뿐이라 행렬 연산
    한 번이면 되고, 국소 탐색의 이웃 수십 개를 한 번에 평가할 수 있다(`loads_batch`).
    `dp_matrix`는 최단거리·최단경로 개수로 같은 값을 구하는 독립 구현이다 — 자가 테스트의 대조용.
    """

    def __init__(self, topo: Topology, cache_size: int = 200_000):
        self.topo = topo
        self.n = len(topo.nodes)
        idx = {v: i for i, v in enumerate(topo.nodes)}
        self.ends = [(idx[a], idx[b]) for a, b in (lk.split("-") for lk in topo.links)]
        self.pairs = topo.pairs
        L = len(topo.links)
        rows, owner = [], []
        for j, (s, d) in enumerate(self.pairs):
            for path in self._simple_paths(s, d):
                r = np.zeros(L, dtype=np.int64)
                r[path] = 1
                rows.append(r); owner.append(j)
        self.inc = np.array(rows)                       # [경로, 링크]
        self.path_pair = np.array(owner)                # 경로 → 쌍 (쌍별로 연속)
        self.pair_start = np.searchsorted(self.path_pair, np.arange(len(self.pairs)))
        self.pair_onehot = np.zeros((len(rows), len(self.pairs)))
        self.pair_onehot[np.arange(len(rows)), self.path_pair] = 1.0
        self._incf = self.inc.astype(float)
        self._cache: dict[tuple, np.ndarray] = {}
        self._cache_size = cache_size

    def _simple_paths(self, s: int, d: int) -> list[list[int]]:
        adj: list[list[tuple[int, int]]] = [[] for _ in range(self.n)]
        for e, (u, v) in enumerate(self.ends):
            adj[u].append((v, e)); adj[v].append((u, e))
        out: list[list[int]] = []

        def walk(node: int, seen: frozenset, acc: list[int]) -> None:
            if node == d:
                out.append(list(acc)); return
            for nxt, e in adj[node]:
                if nxt not in seen:
                    acc.append(e); walk(nxt, seen | {nxt}, acc); acc.pop()

        walk(s, frozenset({s}), [])
        return out

    def shares(self, C: np.ndarray) -> np.ndarray:
        """C[B, 링크] (정수 cost) → 경로별 수요 몫 [B, 경로]."""
        pc = C @ self.inc.T
        mn = np.minimum.reduceat(pc, self.pair_start, axis=1)
        mask = pc == mn[:, self.path_pair]
        cnt = np.add.reduceat(mask, self.pair_start, axis=1)
        return mask / cnt[:, self.path_pair]

    def loads_batch(self, C: np.ndarray, demand: np.ndarray) -> np.ndarray:
        """여러 cost 배치의 링크 부하를 한 번에 [B, 링크]."""
        return (self.shares(C) * demand[self.path_pair]) @ self._incf

    def matrix(self, costs) -> np.ndarray:
        key = tuple(int(c) for c in costs)
        R = self._cache.get(key)
        if R is None:
            sh = self.shares(np.array([key], dtype=np.int64))[0]
            R = (self._incf * sh[:, None]).T @ self.pair_onehot
            if len(self._cache) >= self._cache_size:
                self._cache.clear()
            self._cache[key] = R
        return R

    def dp_matrix(self, costs) -> np.ndarray:
        """독립 구현 (대조용): Floyd–Warshall + 최단경로 개수."""
        n = self.n
        D = np.full((n, n), 10 ** 9, dtype=np.int64)
        np.fill_diagonal(D, 0)
        adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
        for (u, v), w in zip(self.ends, costs):
            D[u, v] = min(D[u, v], w); D[v, u] = min(D[v, u], w)
            adj[u].append((v, w)); adj[v].append((u, w))
        for k in range(n):
            D = np.minimum(D, D[:, k:k + 1] + D[k:k + 1, :])
        S = np.zeros((n, n))
        for s in range(n):
            S[s, s] = 1.0
            for v in sorted(range(n), key=lambda x: D[s, x]):
                if v != s:
                    S[s, v] = sum(S[s, u] for u, w in adj[v] if D[s, u] + w == D[s, v])
        ps = np.array([p[0] for p in self.pairs]); pt = np.array([p[1] for p in self.pairs])
        R = np.zeros((len(costs), len(self.pairs)))
        for e, ((u, v), w) in enumerate(zip(self.ends, costs)):
            for a, b in ((u, v), (v, u)):
                on = D[ps, a] + w + D[b, pt] == D[ps, pt]
                R[e] += np.where(on, S[ps, a] * S[b, pt] / S[ps, pt], 0.0)
        return R


def nominal_loads(topo: Topology, router: Router | None = None) -> np.ndarray:
    """균일 cost · 공칭 수요(쌍마다 1.0)에서의 링크 부하."""
    r = router or Router(topo)
    return r.matrix(tuple([DEFAULT_COST] * len(topo.links))) @ np.ones(len(topo.pairs))


class NetSim:
    """인스턴스형 시뮬레이터. metric_generator(finite 모드)와 같은 물리, 임의 토폴로지."""

    def __init__(self, topo: Topology, router: Router | None = None):
        self.topo = topo
        self.router = router or Router(topo)
        self.L = len(topo.links)
        self.N = len(topo.nodes)
        self.link_index = {lk: i for i, lk in enumerate(topo.links)}
        for lk in topo.links:                                # 역표기("b-a")도 받는다
            a, b = lk.split("-")
            self.link_index[f"{b}-{a}"] = self.link_index[lk]
        self.node_index = {v: i for i, v in enumerate(topo.nodes)}
        self.pair_index = {}
        for j, (s, t) in enumerate(topo.pairs):
            a, b = topo.nodes[s], topo.nodes[t]
            self.pair_index[(a, b)] = self.pair_index[(b, a)] = j
        self.node_link_idx = [[self.link_index[lk] for lk in topo.node_links[n]] for n in topo.nodes]
        self._node_avg = np.zeros((self.L, self.N))      # 링크 스트레스 → 노드 평균
        for k, idx in enumerate(self.node_link_idx):
            self._node_avg[idx, k] = 1.0 / len(idx)
        self.nominal_load = nominal_loads(topo, self.router)
        self.default_capacity = CAPACITY_FACTOR * self.nominal_load
        self.rng = random.Random()
        self.reset()

    # ── 상태 ──────────────────────────────────────────────────────────────────
    def reset(self, seed: int | None = None) -> None:
        self.stress = [INIT_STRESS] * self.L
        self.costs = [DEFAULT_COST] * self.L
        self.capacity = self.default_capacity.copy()
        self.demand = np.ones(len(self.topo.pairs))
        self.congested: set[int] = set()
        self.tick_count = 0
        if seed is not None:
            self.rng.seed(seed)

    def set_cost(self, link: str | int, cost: int) -> bool:
        """cost를 바꾸고, 실제로 바뀌었는지 돌려준다."""
        i = link if isinstance(link, int) else self.link_index[link]
        if cost not in OSPF_COSTS:
            raise ValueError(cost)
        changed = self.costs[i] != cost
        self.costs[i] = cost
        return changed

    def inject_congestion(self, link: str | int) -> None:
        i = link if isinstance(link, int) else self.link_index[link]
        self.congested.add(i)
        self.stress[i] = 0.95

    def set_weak(self, link: str | int, factor: float = WEAK_FACTOR) -> None:
        i = link if isinstance(link, int) else self.link_index[link]
        self.capacity[i] = factor * self.nominal_load[i]

    def scale_node_demand(self, node: str, factor: float) -> None:
        """한 노드가 끝점인 쌍 전부의 수요를 factor배 (핫스팟)."""
        k = self.node_index[node]
        for j, (s, t) in enumerate(self.topo.pairs):
            if s == k or t == k:
                self.demand[j] *= factor

    # ── 물리 ──────────────────────────────────────────────────────────────────
    def loads(self, costs=None, demand=None) -> np.ndarray:
        c = tuple(self.costs if costs is None else costs)
        d = self.demand if demand is None else demand
        return self.router.matrix(c) @ d

    def _drive(self, costs=None, demand=None) -> list[float]:
        load = self.loads(costs, demand)
        out = []
        for i in range(self.L):
            util = load[i] / self.capacity[i]
            d = UTIL_GAIN * min(1.0, util) + OVERLOAD_GAIN * min(1.0, max(0.0, util - 1.0))
            if i in self.congested:
                d += CONG_GAIN * min(1.0, load[i])
            out.append(d)
        return out

    def tick(self, n: int = 1) -> int:
        for _ in range(n):
            drive = self._drive()
            for i in range(self.L):
                noise = self.rng.gauss(0, NOISE)
                self.stress[i] = max(0.0, min(1.0, self.stress[i] * DECAY + drive[i] + noise))
            self.tick_count += 1
        return self.tick_count

    def _node_stress(self, k: int) -> float:
        idx = self.node_link_idx[k]
        return sum(self.stress[i] for i in idx) / len(idx)

    # ── 관측 (난수 소비 순서가 metric_generator와 같다) ───────────────────────────
    def node_metrics(self) -> list[dict]:
        out = []
        for k, node in enumerate(self.topo.nodes):
            s = self._node_stress(k)
            bw = max(1.0, min(1000.0, 950.0 * (1 - s) + 10.0 * s + self.rng.gauss(0, 10)))
            lat = max(0.5, 3.0 + 177.0 * s + self.rng.gauss(0, 1.5))
            loss = max(0.0, min(0.5, 0.04 * s + self.rng.gauss(0, 0.001)))
            out.append({"nodeId": node, "bandwidth": round(bw, 2), "latency": round(lat, 2),
                        "packetLoss": round(loss, 4), "tick": self.tick_count})
        return out

    def link_metrics(self) -> list[dict]:
        load = self.loads()
        return [
            {"linkId": lk,
             "utilization": round(load[i] / self.capacity[i], 4),
             "packetLoss": round(max(0.0, min(0.5, 0.04 * self.stress[i] + self.rng.gauss(0, 0.001))), 4),
             "capacity": float(self.capacity[i]),
             "tick": self.tick_count}
            for i, lk in enumerate(self.topo.links)
        ]

    # ── 노이즈 없는 정상상태 (오라클용) ────────────────────────────────────────────
    def steady_node_stress(self, costs=None, demand=None) -> np.ndarray:
        drive = self._drive(costs, demand)
        stress = [min(1.0, d / (1.0 - DECAY)) for d in drive]
        return np.array([sum(stress[i] for i in idx) / len(idx) for idx in self.node_link_idx])

    def steady_node_stress_batch(self, C: np.ndarray, demand: np.ndarray) -> np.ndarray:
        """여러 cost 배치의 정상상태 노드 스트레스 [B, 노드] (탐색용 벡터 구현)."""
        load = self.router.loads_batch(C, demand)
        util = load / self.capacity
        drive = UTIL_GAIN * np.minimum(1.0, util) + OVERLOAD_GAIN * np.clip(util - 1.0, 0.0, 1.0)
        if self.congested:
            cg = sorted(self.congested)
            drive[:, cg] += CONG_GAIN * np.minimum(1.0, load[:, cg])
        stress = np.minimum(1.0, drive / (1.0 - DECAY))
        return stress @ self._node_avg

    def steady_sla_ok(self, costs=None, demand=None) -> bool:
        s = self.steady_node_stress(costs, demand)
        return bool(np.all(3.0 + 177.0 * s < SLA_LATENCY_MS) and np.all(0.04 * s < SLA_PACKET_LOSS))


def sla_ok(node_metrics: list[dict]) -> bool:
    """폐쇄 루프 실험의 복구 판정 (capacity_probe._sla_ok와 같다)."""
    return all(m["latency"] < SLA_LATENCY_MS and m["packetLoss"] < SLA_PACKET_LOSS for m in node_metrics)


# ── 자가 테스트 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import metric_generator as mg

    # 1) 상수가 metric_generator와 같다
    assert (DECAY, NOISE, mg._UTIL_GAIN, mg._OVERLOAD_GAIN, mg._CONG_GAIN) == \
           (mg._DECAY, mg._NOISE, UTIL_GAIN, OVERLOAD_GAIN, CONG_GAIN)
    assert K4.nodes == tuple(mg.NODES) and K4.links == tuple(mg.LINKS_LIST)
    print("OK — 물리 상수·K4 토폴로지가 metric_generator와 같다")

    # 2) K4 라우팅: 무작위 cost 배치 500개에서 링크 부하가 경로 전수 열거와 같다
    sim = NetSim(K4)
    assert np.allclose(sim.nominal_load, 1.0) and np.allclose(sim.default_capacity, mg.DEFAULT_LINK_CAPACITY)
    rnd = random.Random(0)
    mg.set_capacity_mode("finite")
    try:
        for _ in range(500):
            c = [rnd.choice(OSPF_COSTS) for _ in K4.links]
            mg.reset_state()
            for lk, v in zip(K4.links, c):
                mg.set_ospf_cost(lk, v)
            ref = mg.get_link_loads()
            mine = sim.loads(c)
            assert all(abs(ref[lk] - mine[i]) < 1e-9 for i, lk in enumerate(K4.links)), (c, ref, mine)
        print("OK — K4 라우팅: 무작위 cost 배치 500개에서 경로 전수 열거와 일치")

        # 3) 궤적 일치: 같은 seed·같은 조작이면 스트레스·관측이 metric_generator와 같다
        for trial in range(30):
            seed = 1000 + trial
            mg.reset_state(seed=seed); sim.reset(seed=seed)
            r2 = random.Random(trial)
            weak = r2.choice(K4.links)
            mg.set_link_capacity(weak, 1.2); sim.set_weak(weak)
            for lk in r2.sample(K4.links, r2.choice((1, 2))):
                mg.inject_congestion(lk); sim.inject_congestion(lk)
            for step in range(25):
                if r2.random() < 0.3:
                    lk, v = r2.choice(K4.links), r2.choice(OSPF_COSTS)
                    mg.set_ospf_cost(lk, v); sim.set_cost(lk, v)
                mg.tick(); sim.tick()
                a, b = mg.get_all_metrics(), sim.node_metrics()
                la, lb = mg.get_link_metrics(), sim.link_metrics()
                assert [(m["bandwidth"], m["latency"], m["packetLoss"]) for m in a] == \
                       [(m["bandwidth"], m["latency"], m["packetLoss"]) for m in b], (trial, step)
                assert [(m["utilization"], m["packetLoss"]) for m in la] == \
                       [(m["utilization"], m["packetLoss"]) for m in lb], (trial, step)
            assert max(abs(mg._link_stress[lk] - sim.stress[i]) for i, lk in enumerate(K4.links)) < 1e-12
        print("OK — K4 궤적: 30 에피소드 × 25 틱, 노드·링크 관측이 metric_generator와 완전히 같다")

        # 4) 정상상태 판정이 metric_generator.predict_steady_state와 같다
        for trial in range(200):
            r2 = random.Random(5000 + trial)
            mg.reset_state(); sim.reset()
            weak = r2.choice(K4.links); mg.set_link_capacity(weak, 1.2); sim.set_weak(weak)
            for lk in r2.sample(K4.links, r2.choice((1, 2))):
                mg.inject_congestion(lk); sim.inject_congestion(lk)
            c = {lk: r2.choice(OSPF_COSTS) for lk in K4.links}
            assert mg.predict_steady_state(c)["sla_ok"] == sim.steady_sla_ok([c[lk] for lk in K4.links])
        print("OK — 정상상태 SLA 판정이 predict_steady_state와 200건 일치")
    finally:
        mg.set_capacity_mode("unlimited"); mg.reset_state()

    # 4b) 라우팅 독립 구현 대조 (Abilene) + 배치 정상상태가 스칼라 구현과 같다
    abr = Router(ABILENE)
    for _ in range(300):
        c = tuple(rnd.choice(OSPF_COSTS) for _ in ABILENE.links)
        assert np.allclose(abr.matrix(c), abr.dp_matrix(c), atol=1e-12), c
    ab = NetSim(ABILENE)
    for t_ in range(50):
        r2 = random.Random(t_)
        ab.reset(); ab.set_weak(r2.choice(ABILENE.links))
        for lk in r2.sample(ABILENE.links, 2):
            ab.inject_congestion(lk)
        C = np.array([[r2.choice(OSPF_COSTS) for _ in ABILENE.links] for _ in range(20)])
        sb = ab.steady_node_stress_batch(C, ab.demand)
        for b in range(20):
            assert np.allclose(sb[b], ab.steady_node_stress(tuple(C[b]))), (t_, b)
    print("OK — Abilene 라우팅: 경로 열거와 최단경로 개수(DP) 두 구현이 300건 일치, 배치 정상상태 일치")

    # 5) Abilene: 공칭 부하와 용량, 평시 정상
    ab = NetSim(ABILENE)
    assert len(ABILENE.links) == 14 and len(ABILENE.pairs) == 55
    assert abs(ab.nominal_load.sum() - (ab.router.matrix(tuple([10] * 14)) @ np.ones(55)).sum()) < 1e-9
    util0 = ab.loads() / ab.capacity
    assert np.allclose(util0, 0.5), util0
    ab.reset(seed=1); ab.tick(80)
    assert sla_ok(ab.node_metrics()) and ab.steady_sla_ok()
    # 수요 보존: 각 쌍의 수요는 경로 길이(홉)만큼 링크 부하에 실린다
    R = ab.router.matrix(tuple([10] * 14))
    for j in range(55):
        col = R[:, j]
        assert abs(col.sum() - round(col.sum())) < 1e-9, (j, col.sum())
    print(f"OK — Abilene: 11노드·14링크·55쌍, 공칭 부하 {ab.nominal_load.min():.2f}~{ab.nominal_load.max():.2f}, "
          f"평시 이용률 0.5·SLA 정상")

    # 6) Abilene 단일 혼잡: cost 200으로 격리하면 부하 0, 미대응이면 SLA 위반
    ab.reset(seed=2); ab.inject_congestion("KCY-IND")
    assert not ab.steady_sla_ok()
    ab.set_cost("KCY-IND", 200)
    assert ab.loads()[ab.link_index["KCY-IND"]] == 0.0
    print(f"OK — Abilene KCY-IND 격리: 부하 0, 정상상태 SLA {'충족' if ab.steady_sla_ok() else '미충족'}")
    print("\n모든 자가 테스트 통과")
