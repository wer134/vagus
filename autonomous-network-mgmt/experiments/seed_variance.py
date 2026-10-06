"""T3 — seed 분산 (cowork/VISUALIZATION_PLAN.md §3, ROADMAP A-7 후속).

이 리포가 보고하는 수치는 거의 전부 **seed 1개**에서 나온 것이다. 그래서 "절제 실험에서
combined 7.00이 analytics_only 7.08보다 낮다" 같은 서술이 실제 차이인지 seed 노이즈인지
말할 수 없다. 이 스크립트는 그 질문 하나에 답한다:

    지금 보고하는 차이 중 어떤 것이 seed 노이즈보다 큰가?

**짝지어 비교한다.** 같은 seed 안에서 세 모드가 같은 장애 시퀀스를 같은 노이즈로 겪으므로,
seed마다 (combined - analytics_only)를 구해 그 분포를 본다. 조건별 평균을 따로 내서 비교하면
seed 노이즈가 양쪽에 그대로 남아 검정력이 떨어진다.

두 종류의 seed를 구분한다:
  평가 seed  — 체크포인트는 고정, 시뮬레이터 노이즈·링크 시퀀스만 바꾼다 (기본)
  학습 seed  — 처음부터 다시 학습한다 (--train-seeds, 변종당 약 10분)

    python3 experiments/seed_variance.py --local            # 오프라인 평가만 (서버 불필요)
    python3 experiments/seed_variance.py                    # + 폐쇄 루프 (서버 필요)
    python3 experiments/seed_variance.py --seeds 42,43,44,45,46
    python3 experiments/seed_variance.py --train-seeds      # 학습 seed까지 (오래 걸림)

n=5 정도로는 유의성 검정을 말할 수 없다. 평균과 표준편차, 그리고 **차이가 모든 seed에서
같은 방향인가**를 보고할 뿐이다. 그 이상을 주장하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT, "ai-engine"))
sys.path.insert(0, os.path.join(PROJECT, "ai-engine", "agents"))

RESULTS = os.path.join(HERE, "results")
SNMP, AI = "http://localhost:5001", "http://localhost:8000"


def servers_up() -> bool:
    try:
        import httpx
        return (httpx.get(f"{SNMP}/health", timeout=2).status_code == 200
                and httpx.get(f"{AI}/health", timeout=2).status_code == 200)
    except Exception:
        return False


# ── 오프라인 평가 (서버 불필요) ───────────────────────────────────────────────
def offline_for_seed(seed: int, episodes: int) -> dict:
    from run_experiment import evaluate_agent
    out = {}
    for agent_type in ("baseline", "fewshot"):
        eps = evaluate_agent(agent_type, n_episodes=episodes, max_steps=200, sim_seed=seed)
        ttrs = [e.ttr_steps for e in eps]
        solved = [t for t in ttrs if t < 200]
        out[agent_type] = {
            "avg_ttr": round(sum(ttrs) / len(ttrs), 3),
            "success_rate": round(100.0 * len(solved) / len(ttrs), 2),
            "n": len(ttrs),
        }
    return out


# ── 폐쇄 루프 (서버 필요) — 기존 스크립트를 seed만 바꿔 호출 ──────────────────
def closed_loop_for_seed(seed: int, episodes: int) -> dict:
    out = {}
    abl_name = f"t3_ablation_s{seed}.json"
    st_name = f"results/t3_stress_s{seed}.json"
    runs = [
        ([sys.executable, "ablation_study.py", "--episodes", str(episodes),
          "--seed", str(seed), "--output", abl_name], os.path.join(RESULTS, abl_name), "ablation"),
        ([sys.executable, "stress_test.py", "--episodes", str(episodes),
          "--seed", str(seed), "--output", st_name], os.path.join(HERE, st_name), "stress"),
    ]
    for cmd, path, key in runs:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(path):
            print(f"   [{key}] 실패: {r.stderr.strip()[-200:]}", flush=True)
            continue
        with open(path, encoding="utf-8") as f:
            out[key] = json.load(f)
    return out


# ── 짝지어 비교 ───────────────────────────────────────────────────────────────
def paired(name: str, diffs: list[float], a_label: str, b_label: str,
           a_vals: list[float], b_vals: list[float], ascii_name: str = "") -> dict:
    """seed별 차이의 분포. 부호가 모든 seed에서 같은지가 핵심이다."""
    n = len(diffs)
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    same_sign = all(d > 0 for d in diffs) or all(d < 0 for d in diffs)
    return {
        "comparison": name,
        # 도판 라벨용 — 이미지 안에는 ASCII만 들어간다 (VISUALIZATION_PLAN §2.3)
        "comparison_ascii": ascii_name or name,
        "a": a_label, "b": b_label,
        "a_mean": round(statistics.mean(a_vals), 3),
        "b_mean": round(statistics.mean(b_vals), 3),
        "a_sd": round(statistics.stdev(a_vals), 3) if n > 1 else 0.0,
        "b_sd": round(statistics.stdev(b_vals), 3) if n > 1 else 0.0,
        "paired_diffs": [round(d, 3) for d in diffs],
        "mean_diff": round(mean, 3),
        "sd_diff": round(sd, 3),
        "abs_mean_over_sd": round(abs(mean) / sd, 2) if sd > 1e-9 else None,
        "same_sign_across_seeds": same_sign,
        "n_seeds": n,
    }


def build_comparisons(rows: dict) -> list[dict]:
    seeds = sorted(rows)
    cmps = []

    def get(seed, *path):
        cur = rows[seed]
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        return cur

    # 1) 절제: MAML을 더하면 나아지는가 (헤드라인 주장)
    pairs = [(get(s, "ablation", "results", "combined", "avg_ttr"),
              get(s, "ablation", "results", "analytics_only", "avg_ttr")) for s in seeds]
    if all(a is not None and b is not None for a, b in pairs):
        cmps.append(paired("절제: combined - analytics_only (평균 TTR)",
                           [a - b for a, b in pairs], "combined", "analytics_only",
                           [a for a, _ in pairs], [b for _, b in pairs],
                           "Ablation: combined - analytics only (TTR)"))

    # 2) 절제: MAML 단독은 분석 단독보다 나쁜가
    pairs = [(get(s, "ablation", "results", "maml_only", "avg_ttr"),
              get(s, "ablation", "results", "analytics_only", "avg_ttr")) for s in seeds]
    if all(a is not None and b is not None for a, b in pairs):
        cmps.append(paired("절제: maml_only - analytics_only (평균 TTR)",
                           [a - b for a, b in pairs], "maml_only", "analytics_only",
                           [a for a, _ in pairs], [b for _, b in pairs],
                           "Ablation: MAML only - analytics only (TTR)"))

    # 3) 폐쇄 루프: 미학습 링크가 학습 링크보다 나쁜가 (일반화 주장)
    pairs = [(get(s, "stress", "test_avg_ttr"), get(s, "stress", "train_avg_ttr")) for s in seeds]
    if all(a is not None and b is not None for a, b in pairs):
        cmps.append(paired("폐쇄 루프: TEST - TRAIN (평균 TTR)",
                           [a - b for a, b in pairs], "TEST 링크", "TRAIN 링크",
                           [a for a, _ in pairs], [b for _, b in pairs],
                           "Closed loop: held-out - trained links (TTR)"))

    # 4) 오프라인: MAML이 PPO보다 나은가
    pairs = [(get(s, "offline", "fewshot", "avg_ttr"),
              get(s, "offline", "baseline", "avg_ttr")) for s in seeds]
    if all(a is not None and b is not None for a, b in pairs):
        cmps.append(paired("오프라인: MAML - PPO (평균 TTR)",
                           [a - b for a, b in pairs], "MAML", "PPO",
                           [a for a, _ in pairs], [b for _, b in pairs],
                           "Offline: MAML - PPO (TTR)"))
    return cmps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="42,43,44,45,46")
    ap.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--local", action="store_true", help="오프라인 평가만 (서버 불필요)")
    ap.add_argument("--out", default=os.path.join(RESULTS, "seed_variance.json"))
    args = ap.parse_args()

    seeds = [int(x) for x in args.seeds.split(",")]
    do_closed = not args.local
    if do_closed and not servers_up():
        print("서버(5001/8000)가 떠 있지 않습니다. --local로 오프라인 평가만 돌리거나,\n"
              "  (cd simulation && python3 mock_snmp_agent.py &) ; (cd ai-engine && python3 api_server.py &)\n"
              "로 띄운 뒤 다시 실행하세요.", file=sys.stderr)
        return 1

    rows = {}
    for seed in seeds:
        print(f"\n── seed {seed}", flush=True)
        rows[seed] = {"offline": offline_for_seed(seed, args.episodes)}
        o = rows[seed]["offline"]
        print(f"   오프라인  PPO TTR {o['baseline']['avg_ttr']:.2f} / "
              f"MAML TTR {o['fewshot']['avg_ttr']:.2f}", flush=True)
        if do_closed:
            rows[seed].update(closed_loop_for_seed(seed, args.episodes))
            st = rows[seed].get("stress")
            if st:
                print(f"   폐쇄 루프 TTR {st['avg_ttr']:.2f} "
                      f"(TEST {st['test_avg_ttr']:.2f} / TRAIN {st['train_avg_ttr']:.2f})", flush=True)

    cmps = build_comparisons(rows)

    from _resultmeta import result_meta
    doc = result_meta(
        seed=None,
        condition=(
            f"T3 seed 분산. 평가 seed {seeds}, 조건당 {args.episodes} 에피소드. "
            "체크포인트는 고정이고 시뮬레이터 노이즈·링크 시퀀스만 바뀐다(=평가 분산, 학습 분산 아님). "
            "같은 seed 안에서 조건을 짝지어 차이를 낸다 — 모드 간 노이즈 시퀀스가 동일하기 때문. "
            f"n={len(seeds)}는 유의성 검정에 쓸 수 없고, 평균·표준편차와 부호 일관성만 보고한다."
        ),
        seeds=seeds, episodes=args.episodes, closed_loop=do_closed,
    )
    doc["per_seed"] = {str(k): v for k, v in rows.items()}
    doc["comparisons"] = cmps
    os.makedirs(RESULTS, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 86)
    print(f"{'비교':44s} {'차이':>8s} {'SD':>7s} {'|평균|/SD':>9s} {'부호일치':>8s}")
    print("=" * 86)
    for c in cmps:
        r = c["abs_mean_over_sd"]
        print(f"{c['comparison']:44s} {c['mean_diff']:>8.3f} {c['sd_diff']:>7.3f} "
              f"{(f'{r:.2f}' if r is not None else '—'):>9s} "
              f"{('예' if c['same_sign_across_seeds'] else '아니오'):>8s}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
