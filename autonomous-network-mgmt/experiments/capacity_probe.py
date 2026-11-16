"""
Capacity Probe: 규칙 기반 Analytics는 어디서 깨지는가 (cowork/ROADMAP.md B-2 / A-6)
---------------------------------------------------------------------------------
링크 용량이 있는 시뮬레이터(SIM_CAPACITY=finite)에서 혼잡 조합 × 용량 프로파일을 전수로 돌려
시나리오마다 두 가지를 잰다.

  1. 오라클 — cost 배치 5^6 = 15,625개를 전부 대입해 SLA를 만족하는 정상상태가 **존재하는지**.
     풀이가 없는 시나리오에서의 실패는 정책의 실패가 아니므로 벤치마크에서 빼야 한다.
  2. 규칙 — 폐쇄 루프 `/auto-step?disable_maml=true`(Analytics 단독)가 실제로 복구하는지.

찾는 것은 **"풀이는 있는데 규칙이 못 푸는" 시나리오**다. 그런 시나리오가 있어야 학습 계층의
기여를 잴 공간이 생긴다 (ROADMAP §0: 규칙으로 100% 풀리는 문제에 학습 모델을 얹는 것은
기여가 아니다).

복구 판정은 두 단계다.
  resolved : 기존 실험과 같은 기준 — 2번째 사이클 이후 전 노드 SLA 충족을 처음 본 시점 (TTR)
  stable   : resolved 뒤에도 폐쇄 루프를 STABLE_CYCLES번 더 돌려 **끝까지** SLA가 유지되는지.
             용량이 있으면 격리가 우회 링크를 포화시켜 위반이 재발할 수 있어 필요하다.

실행 (서버 두 개가 떠 있어야 한다):
  cd simulation && SIM_CAPACITY=finite python3 mock_snmp_agent.py
  cd ai-engine  && python3 api_server.py
  python3 experiments/capacity_probe.py [--seeds 5] [--output capacity_probe.json]
"""
import itertools
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "simulation"))
from _resultmeta import result_meta  # noqa: E402
import metric_generator as mg  # noqa: E402  (오라클 전용 — 서버와 별개의 프로세스 내 사본)

AI   = "http://127.0.0.1:8000"
SNMP = "http://127.0.0.1:5001"
ALL_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]
OSPF_COSTS = [10, 20, 50, 100, 200]

MAX_CYCLES    = 15     # 기존 폐쇄 루프 실험과 같은 타임아웃
STABLE_CYCLES = 10     # resolved 뒤 재발 여부를 보는 추가 사이클
WEAK_CAPACITY = 1.2    # "약한 링크" 프로파일의 용량 (기본 2.0). 단일 우회 부하 1.5보다 작다


def _http(method, url, data=None, timeout=8):
    body = json.dumps(data).encode() if data is not None else b""
    req = urllib.request.Request(
        url, data=body or None, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


post = lambda url, data=None: _http("POST", url, data)
get  = lambda url:            _http("GET",  url)
put  = lambda url, data=None: _http("PUT",  url, data)


# ── 시나리오 ──────────────────────────────────────────────────────────────────

def build_scenarios() -> list[dict]:
    """혼잡 조합(단일 6 + 이중 15) × 용량 프로파일(균일 1 + 약한 링크 6) = 147개."""
    congestion_sets = [[lk] for lk in ALL_LINKS] + [list(p) for p in itertools.combinations(ALL_LINKS, 2)]
    profiles = [{"name": "uniform", "weak": None}] + [{"name": f"weak:{lk}", "weak": lk} for lk in ALL_LINKS]
    return [
        {"congested": cong, "profile": prof["name"], "weak": prof["weak"]}
        for prof in profiles for cong in congestion_sets
    ]


def _sla_ok(metrics: list[dict]) -> bool:
    return all(m["latency"] < 50 and m["packetLoss"] < 0.01 for m in metrics)


# ── 오라클 ────────────────────────────────────────────────────────────────────

_ALL_ASSIGNMENTS = [dict(zip(ALL_LINKS, combo)) for combo in itertools.product(OSPF_COSTS, repeat=len(ALL_LINKS))]


def oracle(scn: dict) -> dict:
    """SLA를 만족하는 cost 배치가 존재하는가 — 전수 탐색, 노이즈 없는 정상상태 기준."""
    mg.set_capacity_mode("finite")
    mg.reset_state()
    if scn["weak"]:
        mg.set_link_capacity(scn["weak"], WEAK_CAPACITY)
    for lk in scn["congested"]:
        mg.inject_congestion(lk)
    if scn.get("shift"):     # demand_shift_probe.py: 수요가 바뀐 **뒤**의 상태에 풀이가 있는가
        a, b = scn["shift"]["pair"].split("-")
        mg.set_demand({(a, b): scn["shift"]["demand"]})

    best, n_ok = None, 0
    for costs in _ALL_ASSIGNMENTS:
        if not mg.predict_steady_state(costs)["sla_ok"]:
            continue
        n_ok += 1
        changed = sorted(lk for lk, c in costs.items() if c != 10)
        if best is None or len(changed) < len(best["changed"]):
            best = {"changed": changed, "costs": {lk: costs[lk] for lk in changed}}
    return {
        "solvable":       n_ok > 0,
        "n_solutions":    n_ok,
        "min_changes":    len(best["changed"]) if best else None,
        "example":        best["costs"] if best else None,
    }


# ── 규칙(Analytics 단독) 폐쇄 루프 ────────────────────────────────────────────

def run_rule_episode(scn: dict, seed: int, analytics_version: int = 1) -> dict:
    post(f"{SNMP}/debug/reset", {"seed": seed})
    post(f"{AI}/reset-buffer")
    if scn["weak"]:
        put(f"{SNMP}/debug/capacity/{scn['weak']}", {"capacity": WEAK_CAPACITY})
    for lk in scn["congested"]:
        post(f"{SNMP}/debug/congestion/{lk}")

    congested = set(scn["congested"])
    actions, wasted, ttr = [], 0, None

    def cycle() -> bool:
        nonlocal wasted
        d = post(f"{AI}/auto-step?disable_maml=true&analytics_version={analytics_version}")
        act = d["act"]
        if act.get("applied"):
            actions.append(f'{act["link"]}@{act["cost"]}')
            if act["link"] not in congested and act.get("changed", True):
                wasted += 1   # 혼잡 링크가 아닌 링크의 cost 변경. v3(계획기)에서는 우회 조정이라 낭비가 아니다
        return _sla_ok(get(f"{SNMP}/metrics"))

    for step in range(1, MAX_CYCLES + 1):
        ok = cycle()
        if ok and step > 1:
            ttr = step
            break

    resolved = ttr is not None
    stable = False
    if resolved:
        ok_tail = [cycle() for _ in range(STABLE_CYCLES)]
        stable = ok_tail[-1]
    return {
        "seed": seed, "resolved": resolved, "stable": stable,
        "ttr": ttr if resolved else MAX_CYCLES, "wasted": wasted, "actions": actions,
    }


# ── 본체 ──────────────────────────────────────────────────────────────────────

def run(n_seeds: int, output: str, analytics_version: int = 1, solvable_only: bool = False) -> None:
    health = get(f"{SNMP}/health")
    if health.get("capacity_mode") != "finite":
        raise SystemExit("시뮬레이터가 SIM_CAPACITY=finite로 떠 있지 않다 — 이 실험은 용량 모델 전용이다")

    scenarios = build_scenarios()
    rows = []
    for i, scn in enumerate(scenarios, 1):
        orc = oracle(scn)
        if solvable_only and not orc["solvable"]:
            continue   # 풀이 없는 시나리오는 벤치마크 밖 — seed는 전체 목록의 번호 i를 그대로 쓴다
        # 시나리오 간 독립, 시나리오 내 seed 간 독립인 노이즈 (다른 실험의 42000+i 규약과 분리)
        eps = [run_rule_episode(scn, 73_000 + i * 100 + k, analytics_version) for k in range(n_seeds)]
        n = len(eps)
        row = {
            "congested": scn["congested"], "profile": scn["profile"],
            "oracle": orc,
            "rule": {
                "resolved_rate": sum(e["resolved"] for e in eps) / n,
                "stable_rate":   sum(e["stable"] for e in eps) / n,
                "avg_ttr":       round(sum(e["ttr"] for e in eps) / n, 2),
                "wasted_per_ep": round(sum(e["wasted"] for e in eps) / n, 2),
                "episodes":      eps,
            },
        }
        rows.append(row)
        print(f"[{i:3d}/{len(scenarios)}] {scn['profile']:<13} cong={','.join(scn['congested']):<12} "
              f"oracle={'Y' if orc['solvable'] else 'n'}(min {orc['min_changes']})  "
              f"rule stable={row['rule']['stable_rate']:.1f} ttr={row['rule']['avg_ttr']:.1f} "
              f"act={eps[0]['actions']}", flush=True)

    def bucket(r: dict) -> str:
        if not r["oracle"]["solvable"]:
            return "unsolvable"
        s = r["rule"]["stable_rate"]
        return "rule_solves" if s == 1.0 else ("rule_fails" if s == 0.0 else "rule_partial")

    summary: dict = {}
    for kind, sel in (
        ("single", lambda r: len(r["congested"]) == 1),
        ("double", lambda r: len(r["congested"]) == 2),
    ):
        for prof_kind, psel in (
            ("uniform", lambda r: r["profile"] == "uniform"),
            ("weak",    lambda r: r["profile"] != "uniform"),
        ):
            sub = [r for r in rows if sel(r) and psel(r)]
            counts = {b: 0 for b in ("rule_solves", "rule_partial", "rule_fails", "unsolvable")}
            for r in sub:
                counts[bucket(r)] += 1
            solvable = [r for r in sub if r["oracle"]["solvable"]]
            summary[f"{kind}/{prof_kind}"] = {
                "n_scenarios": len(sub), **counts,
                "n_solvable": len(solvable),
                "rule_stable_rate_on_solvable": (
                    round(sum(r["rule"]["stable_rate"] for r in solvable) / len(solvable), 3)
                    if solvable else None),
            }

    print("\n" + "=" * 78)
    print(f"  {'구간':<16}{'시나리오':>8}{'풀이 존재':>10}{'규칙 성공':>10}{'부분':>6}{'규칙 실패':>10}{'풀이 없음':>10}")
    for name, s in summary.items():
        print(f"  {name:<16}{s['n_scenarios']:>10}{s['n_solvable']:>12}{s['rule_solves']:>12}"
              f"{s['rule_partial']:>8}{s['rule_fails']:>12}{s['unsolvable']:>12}")
    print("=" * 78)

    out = Path(__file__).parent / "results" / output
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            **result_meta(
                seed=73_000,
                condition=(
                    f"폐쇄 루프 /auto-step?disable_maml=true (Analytics v{analytics_version} 단독), 사이클당 1틱, "
                    f"타임아웃 {MAX_CYCLES} 사이클 + 안정성 확인 {STABLE_CYCLES} 사이클. "
                    "오라클은 cost 배치 15,625개 전수, 노이즈 없는 정상상태 기준."
                ),
                sim_capacity_mode="finite",
                default_link_capacity=mg.DEFAULT_LINK_CAPACITY,
                weak_link_capacity=WEAK_CAPACITY,
                n_seeds=n_seeds,
                analytics_version=analytics_version,
                solvable_only=solvable_only,
            ),
            "summary": summary,
            "scenarios": rows,
        }, f, indent=2, ensure_ascii=False)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--seeds",  type=int, default=5, help="시나리오당 에피소드(노이즈 seed) 수")
    p.add_argument("--output", type=str, default="capacity_probe.json", help="results/ 아래 파일명")
    p.add_argument("--analytics-version", type=int, default=1, choices=(1, 2, 3),
                   help="1 = 노드 지표 RCA (기본), 2 = 링크 단위 관측 RCA, 3 = 2 + 라우팅 모델 계획기")
    p.add_argument("--solvable-only", action="store_true",
                   help="오라클이 풀이를 찾은 시나리오만 폐쇄 루프로 돌린다")
    a = p.parse_args()
    run(n_seeds=a.seeds, output=a.output, analytics_version=a.analytics_version,
        solvable_only=a.solvable_only)
