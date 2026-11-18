"""
Scale Benchmark: 전수 탐색이 불가능한 규모에서 규칙 · 계획기 · 학습 정책 비교
--------------------------------------------------------------------------
(cowork/ROADMAP.md §6 (가) 규모 확장, 2026-11-17)

K4에서는 적응형 계획기가 오라클과 18/450 에피소드 차이였다 — 학습이 들어설 자리가 없었다.
이유는 (1) cost 배치 15,625개를 전수 탐색할 수 있었고 (2) 링크 부하가 정확히 보였기 때문이다.
Abilene(11노드·14링크)에서는 배치가 5^14 ≈ 6.1×10^9개라 (1)이 깨진다.

프로토콜 (측정 전에 고정 — ai-engine/environment/scale_env.py의 상수와 같다)
  시나리오  : scale_env.sample_scenario의 TEST 분할에서 EVAL_EPISODES개, rng seed EVAL_SEED
  사이클    : tick → 노드 관측 → 링크 관측 → 결정 → 실행 → 노드 관측(판정) → (3사이클 뒤 수요 변화)
  길이      : 30 사이클 고정
  stable    : 마지막 5 사이클 중 4번 이상 전 노드 SLA 충족 (K4 demand_shift_probe와 같다)
  오라클    : 최종 상태(수요 변화 후, 혼잡 링크 = 고장)에 SLA 풀이가 있는가 — 탐색 기반
              (scale_control.oracle_solvable, 예산 30,000). 벤치마크의 주 지표는 풀이 있는 에피소드만 센다
  제어기    : noop / rule_v2 / planner_estimated / planner_true / ppo (체크포인트별)
  같은 에피소드 = 같은 시나리오 + 같은 시뮬레이터 seed. 관측·판정의 난수 소비가 제어기와 무관하므로
  모든 제어기가 같은 노이즈를 본다.

검증 (--validate-k4)
  이 하네스가 HTTP 폐쇄 루프(`/auto-step?analytics_version=2`)와 같은 것을 재는지 확인한다:
  results/capacity_probe_v2.json의 255 에피소드를 같은 seed로 재생해 복구 여부·TTR·조치 열이
  전부 같아야 한다.

실행:
  python3 experiments/scale_benchmark.py --validate-k4
  python3 experiments/scale_benchmark.py --controllers noop,rule_v2,planner_estimated,planner_true \\
      [--ppo ai-engine/agents/scale/ppo_abilene_s0.zip ...] --output scale_benchmark.json
"""
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ai-engine"))
sys.path.insert(0, str(ROOT / "ai-engine" / "environment"))
sys.path.append(str(ROOT / "simulation"))

import random  # noqa: E402

from _resultmeta import result_meta  # noqa: E402
from netsim import NetSim, TOPOLOGIES, K4, OSPF_COSTS  # noqa: E402
from scale_control import NoOp, RuleV2, Planner, oracle_solvable, PLANNER_BUDGET, ORACLE_BUDGET  # noqa: E402
import scale_env as se  # noqa: E402

EVAL_EPISODES = 400
EVAL_SEED     = 20261117
SIM_SEED_BASE = 60_000_000


# ── 제어기 ────────────────────────────────────────────────────────────────────

class PPOController:
    def __init__(self, path: str):
        from stable_baselines3 import PPO
        self.path = path
        self.name = f"ppo:{Path(path).stem}"
        self.model = PPO.load(path, device="cpu")

    def reset(self, sim: NetSim, seed: int = 0):
        self.sim = sim

    def decide(self, nm, lm, costs):
        a, _ = self.model.predict(se.build_obs(self.sim, nm, lm), deterministic=True)
        return se.decode_action(self.sim, int(a))


def make_controller(spec: str):
    if spec == "noop":
        return NoOp()
    if spec == "rule_v2":
        return RuleV2()
    if spec in ("planner_estimated", "planner_true"):
        return Planner(demand=spec.split("_")[1])
    if spec.startswith("ppo:"):
        return PPOController(spec[4:])
    raise ValueError(spec)


# ── 하네스 ────────────────────────────────────────────────────────────────────

def run_episode(sim: NetSim, scn: se.Scenario, ctrl, seed: int) -> dict:
    se.setup(sim, scn, seed)
    ctrl.reset(sim, seed)
    ok, actions, dts = [], [], []
    links = sim.topo.links
    for step in range(1, se.HORIZON + 1):
        sim.tick()
        nm = sim.node_metrics()
        lm = sim.link_metrics()
        costs = dict(zip(links, sim.costs))
        t0 = time.perf_counter()
        act = ctrl.decide(nm, lm, costs)
        dts.append(time.perf_counter() - t0)
        if act and sim.set_cost(*act):
            actions.append(f"{step}:{act[0]}@{act[1]}")
        ok.append(se.node_ok_count(sim.node_metrics()) == sim.N)
        if step == se.SHIFT_AT and scn.shift:
            sim.scale_node_demand(scn.shift, se.SHIFT_FACTOR)
    stable = sum(ok[-5:]) >= 4
    last_bad = max((i for i, v in enumerate(ok, 1) if not v), default=0)
    return {
        "stable": stable,
        "settle": last_bad + 1 if stable else None,
        "n_changes": len(actions),
        "actions": actions,
        "ok_last5": sum(ok[-5:]),
        "decide_ms_mean": round(1000 * sum(dts) / len(dts), 3),
        "decide_ms_max": round(1000 * max(dts), 3),
    }


def final_state_oracle(sim: NetSim, scn: se.Scenario, seed: int) -> dict:
    """수요 변화까지 반영한 최종 상태에 SLA 풀이가 있는가."""
    se.setup(sim, scn, 0)
    if scn.shift:
        sim.scale_node_demand(scn.shift, se.SHIFT_FACTOR)
    r = oracle_solvable(sim, sim.demand.copy(), set(sim.congested), seed=seed)
    return {"solvable": r["solvable"], "via": r["via"],
            "changes": None if r["costs"] is None else sum(c != 10 for c in r["costs"])}


# ── 병렬 작업자 ───────────────────────────────────────────────────────────────

_W: dict = {}


def _worker_init(topo_name: str):
    import torch
    torch.set_num_threads(1)
    _W["sim"] = NetSim(TOPOLOGIES[topo_name])
    _W["ctrl"] = {}


def _job(args):
    i, scn, seed, spec = args
    sim = _W["sim"]
    if spec == "__oracle__":
        return i, spec, final_state_oracle(sim, scn, seed)
    if spec not in _W["ctrl"]:
        _W["ctrl"][spec] = make_controller(spec)
    return i, spec, run_episode(sim, scn, _W["ctrl"][spec], seed)


def eval_set(topo: str, n: int) -> list[tuple[se.Scenario, int]]:
    rng = random.Random(EVAL_SEED)
    return [(se.sample_scenario(topo, rng, "test"), SIM_SEED_BASE + i) for i in range(n)]


def summarize(rows: list[dict], specs: list[str]) -> dict:
    out = {}
    groups = {
        "all": lambda r: True,
        "solvable": lambda r: r["oracle"]["solvable"],
        "solvable/no_shift": lambda r: r["oracle"]["solvable"] and r["scenario"]["shift"] is None,
        "solvable/shift": lambda r: r["oracle"]["solvable"] and r["scenario"]["shift"] is not None,
        "solvable/single": lambda r: r["oracle"]["solvable"] and len(r["scenario"]["congested"]) == 1,
        "solvable/double": lambda r: r["oracle"]["solvable"] and len(r["scenario"]["congested"]) == 2,
    }
    for g, sel in groups.items():
        sub = [r for r in rows if sel(r)]
        out[g] = {"n": len(sub)}
        for spec in specs:
            eps = [r["results"][spec] for r in sub]
            st = [e for e in eps if e["stable"]]
            out[g][spec] = {
                "stable": len(st),
                "stable_rate": round(len(st) / len(eps), 4) if eps else None,
                "avg_settle": round(sum(e["settle"] for e in st) / len(st), 2) if st else None,
                "avg_changes": round(sum(e["n_changes"] for e in eps) / len(eps), 2) if eps else None,
                "decide_ms_mean": round(sum(e["decide_ms_mean"] for e in eps) / len(eps), 3) if eps else None,
            }
    return out


def run(specs: list[str], n: int, output: str, workers: int, topo: str = "abilene") -> None:
    episodes = eval_set(topo, n)
    jobs = [(i, scn, seed, "__oracle__") for i, (scn, seed) in enumerate(episodes)]
    jobs += [(i, scn, seed, spec) for spec in specs for i, (scn, seed) in enumerate(episodes)]
    res: dict[int, dict] = {i: {"results": {}} for i in range(n)}
    t0 = time.time()
    with Pool(workers, initializer=_worker_init, initargs=(topo,)) as pool:
        for k, (i, spec, out) in enumerate(pool.imap_unordered(_job, jobs, chunksize=4), 1):
            if spec == "__oracle__":
                res[i]["oracle"] = out
            else:
                res[i]["results"][spec] = out
            if k % 200 == 0:
                print(f"  {k}/{len(jobs)} 작업  {time.time() - t0:.0f}s", flush=True)
    rows = []
    for i, (scn, seed) in enumerate(episodes):
        rows.append({
            "i": i, "seed": seed, "key": scn.key,
            "scenario": {"congested": list(scn.congested), "weak": scn.weak, "shift": scn.shift},
            **res[i],
        })
    summary = summarize(rows, specs)
    print("\n" + "=" * 96)
    for g, s in summary.items():
        line = f"  {g:<20} n={s['n']:>3}"
        for spec in specs:
            v = s[spec]
            label = Path(spec[4:]).stem if spec.startswith("ppo:") else spec
            line += f" | {label[:22]:<22} {v['stable']:>3} ({v['avg_changes']} ch)"
        print(line)
    print("=" * 96)
    out = HERE / "results" / output
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            **result_meta(
                seed=EVAL_SEED,
                condition=(
                    f"in-process 하네스, {topo}, TEST 분할 {n} 에피소드, {se.HORIZON} 사이클 고정, "
                    f"{se.SHIFT_AT}사이클 뒤 핫스팟 수요 ×{se.SHIFT_FACTOR}(시나리오에 있을 때), "
                    "stable = 마지막 5 사이클 중 4번 이상 전 노드 SLA. 오라클은 탐색 기반."
                ),
                topology=topo, horizon=se.HORIZON, shift_at=se.SHIFT_AT, shift_factor=se.SHIFT_FACTOR,
                planner_budget=PLANNER_BUDGET, oracle_budget=ORACLE_BUDGET,
                reward_version=se.REWARD_VERSION, controllers=specs,
                elapsed_s=round(time.time() - t0, 1),
            ),
            "summary": summary,
            "episodes": rows,
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved -> {out}")


# ── K4 검증: HTTP 폐쇄 루프 결과 재생 ──────────────────────────────────────────

def validate_k4() -> None:
    src = json.load(open(HERE / "results" / "capacity_probe_v2.json", encoding="utf-8"))
    assert src["analytics_version"] == 2
    sim = NetSim(K4)
    rule = RuleV2()
    n = mism = 0
    for row in src["scenarios"]:
        weak = row["profile"].split(":")[1] if ":" in row["profile"] else None
        cong = set(row["congested"])
        for ep in row["rule"]["episodes"]:
            sim.reset(seed=ep["seed"])
            if weak:
                sim.set_weak(weak)
            for lk in row["congested"]:
                sim.inject_congestion(lk)
            rule.reset(sim)
            actions, ttr = [], None

            def cycle():
                sim.tick()
                nm = sim.node_metrics(); lm = sim.link_metrics()
                act = rule.decide(nm, lm, dict(zip(K4.links, sim.costs)))
                if act:
                    sim.set_cost(*act)
                    actions.append(f"{act[0]}@{act[1]}")       # capacity_probe는 '적용'된 조치를 전부 적는다
                return se.node_ok_count(sim.node_metrics()) == sim.N

            for step in range(1, 16):
                if cycle() and step > 1:
                    ttr = step
                    break
            stable = False
            if ttr is not None:
                stable = [cycle() for _ in range(10)][-1]
            mine = (ttr is not None, stable, ttr if ttr is not None else 15, actions)
            ref = (ep["resolved"], ep["stable"], ep["ttr"], ep["actions"])
            n += 1
            if mine != ref:
                mism += 1
                if mism <= 3:
                    print("  불일치", row["congested"], row["profile"], ep["seed"], mine, ref)
    print(f"K4 재생: {n - mism}/{n} 에피소드가 HTTP 폐쇄 루프 결과(capacity_probe_v2.json)와 일치")
    if mism:
        raise SystemExit(1)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--validate-k4", action="store_true")
    p.add_argument("--controllers", type=str, default="noop,rule_v2,planner_estimated,planner_true")
    p.add_argument("--ppo", nargs="*", default=[], help="PPO 체크포인트 경로 (여러 개)")
    p.add_argument("--episodes", type=int, default=EVAL_EPISODES)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--output", type=str, default="scale_benchmark.json")
    a = p.parse_args()
    if a.validate_k4:
        validate_k4()
    else:
        specs = [s for s in a.controllers.split(",") if s] + [f"ppo:{x}" for x in a.ppo]
        run(specs, a.episodes, a.output, a.workers)
