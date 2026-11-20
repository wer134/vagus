"""
Demand Shift Probe: 계획기의 모델이 틀리면 어떻게 되는가 (cowork/ROADMAP.md §6, 2026-11-16)
---------------------------------------------------------------------------------------
capacity_probe.py에서 계획기(Analytics v3)는 풀이가 있는 시나리오를 전부 풀었다. 그러나 그
계획기는 시뮬레이터와 같은 라우팅 모델에 **정확한 수요**까지 가지고 있었다 — 에피소드가 균일
cost에서 시작하므로 첫 사이클에 수요가 그대로 읽힌다.

여기서는 그 가정을 깬다. 계획기가 수요를 읽고 조치를 시작한 뒤(cost가 더는 균일하지 않아 수요를
다시 읽을 수 없는 시점)에 한 노드 쌍의 수요를 SHIFT_DEMAND로 올린다.

  기반 시나리오 : capacity_probe의 풀이 있는 51개 (혼잡 조합 × 용량 프로파일)
  수요 변화     : SHIFT_AT 사이클이 끝난 직후, 6개 쌍 중 하나의 수요 1.0 → SHIFT_DEMAND
  벤치마크      : 변화 **뒤**의 상태에도 풀이가 있는 시나리오만 (오라클, 전수 탐색)

비교 대상 (전부 disable_maml=true):
  v2 : 링크 관측 규칙 — 모델 없음
  v3 : 계획기, 수요 추정 고정 — 모델이 낡는다
  v4 : 계획기, 관측 부하와 모델이 어긋나면 수요를 다시 추정 — 모델을 고친다

판정 (측정 전에 고정): HORIZON 사이클을 끝까지 돌리고, 마지막 5 사이클 중 4번 이상 전 노드 SLA
충족이면 `stable`. capacity_probe의 "마지막 한 사이클" 기준은 이용률 1.0 배치에서 관측 노이즈
한 번에 뒤집혔다(255 에피소드 중 3건) — 새 실험이라 처음부터 한 번의 튐을 허용한다.
`settle`은 SLA 위반이 마지막으로 관측된 사이클 + 1 (수요 변화 시점 기준 상대값은 settle − SHIFT_AT).

실행 (서버 두 개가 떠 있어야 한다):
  cd simulation && SIM_CAPACITY=finite python3 mock_snmp_agent.py
  cd ai-engine  && python3 api_server.py
  python3 experiments/demand_shift_probe.py --analytics-version 4 [--seeds 3]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _resultmeta import result_meta  # noqa: E402
import capacity_probe as cp  # noqa: E402
from capacity_probe import AI, SNMP, post, get, put, mg  # noqa: E402

SHIFT_AT     = 3      # 이 사이클이 끝난 직후 수요가 바뀐다 (계획기는 이미 1~3개 조치를 실행한 뒤)
SHIFT_DEMAND = 2.0    # 한 쌍의 수요 1.0 → 2.0. 결과를 보고 고른 값이 아니다
HORIZON      = 30     # 에피소드 길이 (사이클). 변화 뒤 27 사이클 = 재계획 최대 6 + 감쇠 여유
PAIRS = ["r1-r2", "r1-r3", "r1-r4", "r2-r3", "r2-r4", "r3-r4"]


def build_scenarios() -> list[dict]:
    """풀이 있는 기반 시나리오 × 수요가 오르는 쌍 6개, 그중 변화 뒤에도 풀이가 있는 것."""
    out = []
    for base_idx, base in enumerate(cp.build_scenarios(), 1):
        if not cp.oracle(base)["solvable"]:
            continue
        for pair in PAIRS:
            scn = {**base, "shift": {"pair": pair, "demand": SHIFT_DEMAND}, "base_idx": base_idx}
            orc = cp.oracle(scn)
            if orc["solvable"]:
                scn["oracle"] = orc
                out.append(scn)
    return out


def stale_plan_survives(scn: dict) -> bool:
    """수요 변화 **전**의 최소 변경 풀이가 변화 뒤에도 SLA를 지키는가 (노이즈 없는 정상상태).

    참이면 수요 변화가 계획을 깨지 않는 시나리오다 — 모델이 낡아도 결과가 같아야 한다.
    """
    pre = cp.oracle({k: v for k, v in scn.items() if k != "shift"})["example"]
    mg.set_capacity_mode("finite")
    mg.reset_state()
    if scn["weak"]:
        mg.set_link_capacity(scn["weak"], cp.WEAK_CAPACITY)
    for lk in scn["congested"]:
        mg.inject_congestion(lk)
    a, b = scn["shift"]["pair"].split("-")
    mg.set_demand({(a, b): scn["shift"]["demand"]})
    return mg.predict_steady_state({**mg.get_ospf_costs(), **pre})["sla_ok"]


def run_episode(scn: dict, seed: int, analytics_version: int) -> dict:
    post(f"{SNMP}/debug/reset", {"seed": seed})
    post(f"{AI}/reset-buffer")
    if scn["weak"]:
        put(f"{SNMP}/debug/capacity/{scn['weak']}", {"capacity": cp.WEAK_CAPACITY})
    for lk in scn["congested"]:
        post(f"{SNMP}/debug/congestion/{lk}")

    actions, ok = [], []
    for step in range(1, HORIZON + 1):
        d = post(f"{AI}/auto-step?disable_maml=true&analytics_version={analytics_version}")
        act = d["act"]
        if act.get("applied") and act.get("changed", True):
            actions.append(f'{step}:{act["link"]}@{act["cost"]}')
        ok.append(cp._sla_ok(get(f"{SNMP}/metrics")))
        if step == SHIFT_AT:
            put(f"{SNMP}/debug/demand/{scn['shift']['pair']}", {"demand": scn["shift"]["demand"]})

    stable = sum(ok[-5:]) >= 4
    last_bad = max((i for i, v in enumerate(ok, 1) if not v), default=0)
    return {
        "seed": seed, "stable": stable,
        "settle": last_bad + 1 if stable else None,
        "n_actions": len(actions),
        "n_actions_after_shift": sum(1 for a in actions if int(a.split(":")[0]) > SHIFT_AT),
        "actions": actions,
    }


def run(n_seeds: int, analytics_version: int, output: str) -> None:
    health = get(f"{SNMP}/health")
    if health.get("capacity_mode") != "finite":
        raise SystemExit("시뮬레이터가 SIM_CAPACITY=finite로 떠 있지 않다")

    scenarios = build_scenarios()
    print(f"벤치마크: 수요 변화 뒤에도 풀이가 있는 시나리오 {len(scenarios)}개 "
          f"(기반 51 × 쌍 6 = 306 중)", flush=True)

    rows = []
    for i, scn in enumerate(scenarios, 1):
        survives = stale_plan_survives(scn)
        # seed는 (기반 시나리오 번호, 쌍, k)로 정한다 — 버전 간에 같은 노이즈로 비교된다
        pair_idx = PAIRS.index(scn["shift"]["pair"])
        eps = [run_episode(scn, 91_000_000 + scn["base_idx"] * 1000 + pair_idx * 10 + k, analytics_version)
               for k in range(n_seeds)]
        n = len(eps)
        row = {
            "congested": scn["congested"], "profile": scn["profile"], "shift": scn["shift"],
            "oracle_min_changes": scn["oracle"]["min_changes"],
            "stale_plan_survives": survives,
            "stable_rate": sum(e["stable"] for e in eps) / n,
            "episodes": eps,
        }
        rows.append(row)
        print(f"[{i:3d}/{len(scenarios)}] {scn['profile']:<11} cong={','.join(scn['congested']):<12} "
              f"shift={scn['shift']['pair']} stale_ok={'Y' if survives else 'n'} "
              f"stable={row['stable_rate']:.2f} act={eps[0]['actions']}", flush=True)

    def summarize(sub: list[dict]) -> dict:
        eps = [e for r in sub for e in r["episodes"]]
        st = [e for e in eps if e["stable"]]
        return {
            "n_scenarios": len(sub),
            "scenarios_all_stable": sum(1 for r in sub if r["stable_rate"] == 1.0),
            "scenarios_none_stable": sum(1 for r in sub if r["stable_rate"] == 0.0),
            "episodes": len(eps), "episodes_stable": len(st),
            "avg_settle_after_shift": round(sum(e["settle"] - SHIFT_AT for e in st) / len(st), 2) if st else None,
            "avg_actions_after_shift": round(sum(e["n_actions_after_shift"] for e in eps) / len(eps), 2) if eps else None,
        }

    summary = {
        "all":                 summarize(rows),
        "stale_plan_survives": summarize([r for r in rows if r["stale_plan_survives"]]),
        "stale_plan_breaks":   summarize([r for r in rows if not r["stale_plan_survives"]]),
    }
    print("\n" + "=" * 78)
    for name, s in summary.items():
        print(f"  {name:<22} 시나리오 {s['n_scenarios']:>3} (전부 안정 {s['scenarios_all_stable']:>3}, "
              f"전부 실패 {s['scenarios_none_stable']:>3})  에피소드 {s['episodes_stable']}/{s['episodes']}  "
              f"정착 {s['avg_settle_after_shift']}  변화 후 조치 {s['avg_actions_after_shift']}")
    print("=" * 78)

    out = Path(__file__).parent / "results" / output
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            **result_meta(
                seed=91_000_000,
                condition=(
                    f"폐쇄 루프 /auto-step?disable_maml=true&analytics_version={analytics_version}, "
                    f"사이클당 1틱, {HORIZON} 사이클 고정. {SHIFT_AT}번째 사이클 직후 한 쌍의 수요 "
                    f"1.0 → {SHIFT_DEMAND}. stable = 마지막 5 사이클 중 4번 이상 SLA 충족."
                ),
                sim_capacity_mode="finite",
                default_link_capacity=mg.DEFAULT_LINK_CAPACITY,
                weak_link_capacity=cp.WEAK_CAPACITY,
                analytics_version=analytics_version,
                shift_at=SHIFT_AT, shift_demand=SHIFT_DEMAND, horizon=HORIZON,
                n_seeds=n_seeds,
            ),
            "summary": summary,
            "scenarios": rows,
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--analytics-version", type=int, required=True, choices=(2, 3, 4))
    p.add_argument("--output", type=str, default=None)
    a = p.parse_args()
    run(a.seeds, a.analytics_version, a.output or f"demand_shift_v{a.analytics_version}.json")
