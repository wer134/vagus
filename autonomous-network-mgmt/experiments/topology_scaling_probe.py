"""토폴로지를 키우면 학습의 자리가 생기는가 — 짓기 전에 재는 탐침.

v2_design_check V-2a가 보인 것: K4에서는 관측만 쓰는 한 줄 규칙("지연 상위 2개 노드를 잇는
링크에 cost 100")이 여섯 링크 전부 100% 푼다. 학습도 적응도 필요 없다.

**그 규칙이 통하는 이유는 K4의 구조에 있다**는 가설이 있다:
  - 모든 쌍이 직결이라 혼잡 링크가 정확히 2개 노드의 지연만 올린다 → 상위 2개 = 정답
  - 장애가 한 번에 하나라 모호함이 없다
  - 우회로가 항상 존재한다 (어느 링크를 끊어도 2홉이 남는다)

노드를 늘리고 장애를 동시에 여러 개 주면 이 셋이 전부 깨질 수 있다. 그러면 학습이 기여할
자리가 생긴다. **생길 것 같다가 아니라 생기는지 재고 나서 짓는다** — 토폴로지 변경은 OBS/
ACTION 계약을 깨고(관측 14차원 = 노드 4×2 + 링크 6, 행동 30 = 링크 6 × cost 5), Java 수집
계층과 대시보드까지 따라와야 하는 큰 변경이다.

**이 스크립트는 운영 시뮬레이터가 아니다.** `metric_generator`의 식(감쇠 0.90, 혼잡 이득,
우회 임계 100, 노드 스트레스 = 인접 링크 평균)을 그대로 옮긴 축소 모형이고, 토폴로지 구조가
휴리스틱에 미치는 영향만 본다. 여기서 나온 수치는 재측정이 아니라 **설계 판단 근거**다.

    python3 experiments/topology_scaling_probe.py
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _resultmeta import result_meta  # noqa: E402

# metric_generator와 같은 상수
DECAY, CONG_GAIN, NOISE = 0.90, 0.5, 0.02
BYPASS = 100
SLA_LAT = 50.0


def ring_with_chords(n: int, extra: int, rng: random.Random) -> list[tuple[str, str]]:
    """링 + 무작위 현(chord). 평균 차수를 조절해 희소/조밀 토폴로지를 만든다."""
    nodes = [f"r{i+1}" for i in range(n)]
    edges = {tuple(sorted((nodes[i], nodes[(i + 1) % n]))) for i in range(n)}
    allp = [tuple(sorted(p)) for p in itertools.combinations(nodes, 2)]
    cand = [e for e in allp if e not in edges]
    rng.shuffle(cand)
    edges.update(cand[:extra])
    return nodes, sorted(edges)


def complete(n: int) -> tuple[list[str], list[tuple[str, str]]]:
    nodes = [f"r{i+1}" for i in range(n)]
    return nodes, [tuple(sorted(p)) for p in itertools.combinations(nodes, 2)]


class Sim:
    """metric_generator의 식을 임의 토폴로지로 옮긴 축소 모형."""

    def __init__(self, nodes, edges, seed=0):
        self.nodes, self.edges = nodes, edges
        self.adj = {v: [e for e in edges if v in e] for v in nodes}
        self.stress = {e: 0.0 for e in edges}
        self.cost = {e: 10 for e in edges}
        self.cong: set = set()
        self.rng = random.Random(seed)

    def tick(self):
        for e in self.edges:
            s = self.stress[e]
            cg = CONG_GAIN if (e in self.cong and self.cost[e] < BYPASS) else 0.0
            self.stress[e] = max(0.0, min(1.0, s * DECAY + cg + self.rng.gauss(0, NOISE)))

    def node_stress(self, v):
        a = self.adj[v]
        return sum(self.stress[e] for e in a) / len(a) if a else 0.0

    def latency(self, v):
        return max(0.5, 3.0 + 177.0 * self.node_stress(v) + self.rng.gauss(0, 1.5))

    def sla_ok(self):
        return all(self.latency(v) <= SLA_LAT for v in self.nodes)

    def has_alternate_path(self, e) -> bool:
        """그 링크를 빼도 양 끝이 연결되는가 — 우회가 가능한가."""
        a, b = e
        rest = [x for x in self.edges if x != e]
        seen, stack = {a}, [a]
        while stack:
            v = stack.pop()
            if v == b:
                return True
            for x in rest:
                if v in x:
                    w = x[0] if x[1] == v else x[1]
                    if w not in seen:
                        seen.add(w); stack.append(w)
        return False


def heuristic_pick(sim: Sim) -> tuple[str, str] | None:
    """지연 상위 2개 노드를 잇는 링크. K4에서 전부 풀던 바로 그 규칙."""
    lat = {v: sim.latency(v) for v in sim.nodes}
    order = sorted(sim.nodes, key=lambda v: -lat[v])
    top2 = tuple(sorted((order[0], order[1])))
    return top2 if top2 in sim.edges else None


def trial(nodes, edges, n_faults, steps, seed) -> dict:
    sim = Sim(nodes, edges, seed)
    rng = random.Random(seed + 7)
    faulty = rng.sample(edges, min(n_faults, len(edges)))
    for e in faulty:
        sim.cong.add(e)
    for _ in range(5):          # 스트레스가 쌓인 "사고 진행 중" 상태
        sim.tick()

    hits, picks = 0, 0
    for _ in range(steps):
        pick = heuristic_pick(sim)
        if pick is not None:
            picks += 1
            if pick in faulty:
                hits += 1
            sim.cost[pick] = BYPASS
        sim.tick()
    resolved = sim.sla_ok()
    bypassable = sum(1 for e in faulty if sim.has_alternate_path(e))
    return {
        "heuristic_hit_rate": hits / picks if picks else 0.0,
        "picked_an_edge_rate": picks / steps,
        "resolved": resolved,
        "faults_bypassable": bypassable / len(faulty),
    }


def sweep(trials: int, steps: int, seed: int) -> dict:
    rng = random.Random(seed)
    configs = [
        ("K4 (현행)", *complete(4), 1),
        ("K4 (현행), 동시 2건", *complete(4), 2),
        ("6노드 완전그래프", *complete(6), 1),
        ("6노드 링+현3", *ring_with_chords(6, 3, rng), 1),
        ("6노드 링+현3, 동시 2건", *ring_with_chords(6, 3, rng), 2),
        ("8노드 링+현4", *ring_with_chords(8, 4, rng), 1),
        ("8노드 링+현4, 동시 2건", *ring_with_chords(8, 4, rng), 2),
        ("8노드 링+현4, 동시 3건", *ring_with_chords(8, 4, rng), 3),
        ("10노드 링+현5, 동시 2건", *ring_with_chords(10, 5, rng), 2),
        ("12노드 링+현6, 동시 3건", *ring_with_chords(12, 6, rng), 3),
    ]
    rows = {}
    for label, nodes, edges, nf in configs:
        rs = [trial(nodes, edges, nf, steps, seed + 100 * i) for i in range(trials)]
        deg = 2 * len(edges) / len(nodes)
        rows[label] = {
            "nodes": len(nodes), "links": len(edges), "avg_degree": round(deg, 2),
            "concurrent_faults": nf,
            "obs_dim": 2 * len(nodes) + len(edges),
            "action_dim": len(edges) * 5,
            "heuristic_hit_rate": round(statistics.mean(r["heuristic_hit_rate"] for r in rs), 3),
            "resolved_pct": round(100.0 * statistics.mean(r["resolved"] for r in rs), 1),
            "faults_bypassable": round(statistics.mean(r["faults_bypassable"] for r in rs), 2),
        }
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "topology_scaling_probe.json"))
    args = ap.parse_args()

    print("토폴로지 확장 탐침 — 운영 시뮬레이터가 아니라 축소 모형이다\n")
    rows = sweep(args.trials, args.steps, args.seed)
    print(f"{'구성':26s} {'노드':>4s} {'링크':>4s} {'차수':>5s} {'장애':>4s} "
          f"{'휴리스틱 적중':>12s} {'해결%':>7s} {'우회가능':>8s} {'관측':>5s} {'행동':>5s}")
    print("=" * 104)
    for k, v in rows.items():
        print(f"{k:26s} {v['nodes']:>4d} {v['links']:>4d} {v['avg_degree']:>5.1f} "
              f"{v['concurrent_faults']:>4d} {v['heuristic_hit_rate']:>12.3f} "
              f"{v['resolved_pct']:>7.1f} {v['faults_bypassable']:>8.2f} "
              f"{v['obs_dim']:>5d} {v['action_dim']:>5d}")

    base = rows["K4 (현행)"]["heuristic_hit_rate"]
    worst = min(rows, key=lambda k: rows[k]["heuristic_hit_rate"])
    print(f"\n  K4 적중률 {base:.3f} → 최저 {rows[worst]['heuristic_hit_rate']:.3f} ({worst})")
    print("  적중률이 떨어지는 구성에서만 학습이 기여할 자리가 생긴다 — "
          "떨어지지 않으면 키워도 소용없다.")

    doc = result_meta(
        seed=args.seed,
        condition=("축소 모형. metric_generator의 식(감쇠 0.90, 혼잡 이득 0.5, 우회 임계 100, "
                   "노드 스트레스=인접 링크 평균)을 임의 토폴로지로 옮긴 것이며 운영 시뮬레이터가 "
                   "아니다. ECMP 라우팅/트래픽 수요는 모형에 없다 — 토폴로지 구조가 '지연 상위 2개' "
                   "휴리스틱에 미치는 영향만 본다. 재측정이 아니라 설계 판단 근거."),
        trials=args.trials, steps=args.steps,
    )
    doc["configs"] = rows
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
