"""학습 설계 감사 — 붕괴를 고치기 전에 "학습 자체가 올바른가"를 먼저 묻는다.

§7은 baseline과 엔트로피를 지목했고 둘 다 고쳐도 붕괴가 남았다. 그래서 더 앞으로 돌아간다:
**지금 돌리는 것이 REINFORCE이고 MAML인가?** 코드를 읽으면 둘 다 의심스럽다. 읽은 것을
주장으로 적지 않고 측정한다.

  D-A  보상이 리턴인가
       `_reinforce_loss`는 advantage를 `r_t - baseline`으로 만든다. REINFORCE의 정책 경사는
       `grad log pi(a_t|s_t) * G_t`이고 `G_t = sum_k gamma^k r_{t+k}`다. 즉시 보상만 쓰면
       1스텝 근시안 목적을 최적화하는 것이다. D4에서 올바른 행동의 효과는 t+1에 1.4σ로 시작해
       t+8에 5.4σ가 됐다 — 즉시 보상은 그 신호의 대부분을 버린다.
       측정: 같은 롤아웃에서 advantage를 두 방식으로 만들고, **정답 행동**(혼잡 링크 우회)이
       각각 몇 위로 평가되는지 비교한다.

  D-B  태스크 분포가 있는가
       MAML은 태스크 분포에서 태스크를 뽑아 inner-loop로 적응하고 적응 후 성능을 최적화한다.
       지금 `tasks_per_iter=4`는 **같은 환경의 독립 롤아웃 4개**다. `_collect_episode`는
       `env.reset()`만 하고 이상은 확률 0.03으로 무작위 주입된다. 태스크가 서로 다르지 않으면
       inner-loop가 적응할 대상이 없다.
       측정: 롤아웃 4개의 상태 분포가 서로 구별되는가. 링크별로 태스크를 정의하면 구별되는가.

  D-C  에피소드 안에 고칠 시간이 있는가
       이상이 평균 몇 스텝째에 생기고, 고치는 데 몇 틱이 걸리며, 에피소드는 몇 스텝인가.

    python3 experiments/training_design_audit.py
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT, "ai-engine"))
sys.path.insert(0, os.path.join(PROJECT, "ai-engine", "agents"))

from environment.network_env import NetworkEnv   # noqa: E402
from topology import LINKS, OSPF_COSTS           # noqa: E402
from _resultmeta import result_meta              # noqa: E402

TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]
EPISODE_STEPS = 30
N_ACTIONS = len(LINKS) * len(OSPF_COSTS)
GAMMA = 0.99


def action_name(a: int) -> str:
    li, ci = divmod(a, len(OSPF_COSTS))
    return f"{LINKS[li]}@{OSPF_COSTS[ci]}"


def correct_actions() -> set[int]:
    """혼잡 링크를 우회(cost=100)시키는 행동 — collapse_diagnosis D3이 1위로 확인한 것."""
    return {LINKS.index(lk) * len(OSPF_COSTS) + OSPF_COSTS.index(100) for lk in TRAIN_LINKS}


def rollout(env, rng, steps=EPISODE_STEPS) -> list[tuple]:
    """균일 무작위 정책 롤아웃. (obs, action, reward, 활성 이상 링크들)"""
    env.reset()
    out = []
    for _ in range(steps):
        a = rng.randrange(N_ACTIONS)
        obs_prev = env._get_obs()
        _, r, _, trunc, info = env.step(a)
        out.append((obs_prev, a, float(r), tuple(info["anomalies"])))
        if trunc:
            break
    return out


# ── D-A. 즉시 보상 vs 할인 리턴 ───────────────────────────────────────────────
def d_a_return_vs_immediate(n_episodes: int, seed: int) -> dict:
    import random as _r
    rng = _r.Random(seed)
    env = NetworkEnv(max_steps=50, fast_mode=True, local_mode=True,
                     train_links=TRAIN_LINKS, sim_seed=seed)

    correct = correct_actions()
    scores = {"immediate": {a: [] for a in range(N_ACTIONS)},
              "discounted_return": {a: [] for a in range(N_ACTIONS)}}

    for _ in range(n_episodes):
        tr = rollout(env, rng)
        rewards = [r for _, _, r, _ in tr]

        # (1) 현행: advantage = r_t - 에피소드 평균
        base = statistics.mean(rewards)
        imm = [r - base for r in rewards]

        # (2) 올바른 REINFORCE: G_t = sum gamma^k r_{t+k}, 그 다음 평균 차감
        G, acc = [], 0.0
        for r in reversed(rewards):
            acc = r + GAMMA * acc
            G.append(acc)
        G.reverse()
        gbase = statistics.mean(G)
        ret = [g - gbase for g in G]

        for (_, a, _, _), xi, xr in zip(tr, imm, ret):
            scores["immediate"][a].append(xi)
            scores["discounted_return"][a].append(xr)
    env.close()

    out = {}
    for mode, per_action in scores.items():
        means = {a: statistics.mean(v) for a, v in per_action.items() if v}
        ranked = sorted(means, key=lambda a: -means[a])
        ranks = sorted(1 + ranked.index(a) for a in correct if a in ranked)
        out[mode] = {
            "correct_action_ranks": ranks,
            "mean_rank_of_correct": round(statistics.mean(ranks), 2) if ranks else None,
            "best_ranked_correct": min(ranks) if ranks else None,
            "top5": [{"action": action_name(a), "score": round(means[a], 4)} for a in ranked[:5]],
            "n_actions": len(means),
        }
    out["gamma"] = GAMMA
    out["n_episodes"] = n_episodes
    out["note"] = ("무작위 정책이므로 '정답 행동이 상위'여야 학습 신호가 있다는 뜻이다. "
                   "30개 중 평균 15.5위면 무작위와 구별되지 않는다.")
    return out


# ── D-B. 태스크 분포 ──────────────────────────────────────────────────────────
def d_b_task_distribution(n_iters: int, seed: int) -> dict:
    """현행 설계에서 한 meta-iteration의 '태스크 4개'가 서로 다른 태스크인가."""
    import random as _r
    rng = _r.Random(seed)
    env = NetworkEnv(max_steps=50, fast_mode=True, local_mode=True,
                     train_links=TRAIN_LINKS, sim_seed=seed)

    same_count = 0        # 4개 태스크의 '문제 링크 집합'이 전부 같은 경우
    empty_count = 0       # 이상이 아예 없던 태스크
    per_task_links = []
    for _ in range(n_iters):
        sigs = []
        for _ in range(4):           # tasks_per_iter=4
            tr = rollout(env, rng)
            links = set()
            for _, _, _, anoms in tr:
                links.update(anoms)
            sigs.append(frozenset(links))
            if not links:
                empty_count += 1
        per_task_links.append([sorted(s) for s in sigs])
        if len(set(sigs)) == 1:
            same_count += 1
    env.close()

    total_tasks = n_iters * 4
    return {
        "design": "현행 — env.reset() 후 확률 0.03 무작위 주입",
        "n_meta_iterations": n_iters,
        "tasks_per_iter": 4,
        "tasks_with_no_anomaly_pct": round(100.0 * empty_count / total_tasks, 2),
        "iterations_where_all_4_tasks_identical_pct": round(100.0 * same_count / n_iters, 2),
        "example_first_3_iterations": per_task_links[:3],
        "note": ("태스크가 '어느 링크가 문제인가'로 구별되어야 inner-loop가 적응할 대상이 생긴다. "
                 "이상이 없는 태스크는 적응할 것이 아예 없다."),
    }


def d_b_proposed(n_iters: int, seed: int) -> dict:
    """제안 설계 — 태스크 = '이 링크에 혼잡이 발생한 상황'. 측정만 하고 학습에 쓰지 않는다."""
    import random as _r
    rng = _r.Random(seed + 1)
    # inject_anomalies=False — 무작위 주입을 끄고 태스크가 지정한 링크만 주입한다
    env = NetworkEnv(max_steps=50, fast_mode=True, local_mode=True,
                     inject_anomalies=False, train_links=TRAIN_LINKS,
                     sim_seed=seed + 1)

    empty = 0
    per_task_links = []
    for _ in range(n_iters):
        sigs = []
        for link in TRAIN_LINKS:          # 태스크 = TRAIN 링크 하나씩
            env.reset()
            env.inject_anomaly(link)      # reset 직후 주입 — 처음부터 고칠 것이 있다
            links = set()
            for _ in range(EPISODE_STEPS):
                a = rng.randrange(N_ACTIONS)
                _, _, _, trunc, info = env.step(a)
                links.update(info["anomalies"])
                if trunc:
                    break
            sigs.append(frozenset(links))
            if not links:
                empty += 1
        per_task_links.append([sorted(s) for s in sigs])
    env.close()

    total = n_iters * len(TRAIN_LINKS)
    return {
        "design": "제안 — 태스크 = TRAIN 링크 하나, reset 직후 주입",
        "n_meta_iterations": n_iters,
        "tasks_per_iter": len(TRAIN_LINKS),
        "tasks_with_no_anomaly_pct": round(100.0 * empty / total, 2),
        "example_first_3_iterations": per_task_links[:3],
    }


# ── D-C. 에피소드 안에 고칠 시간이 있는가 ─────────────────────────────────────
def d_c_time_budget(n_episodes: int, seed: int) -> dict:
    import random as _r
    rng = _r.Random(seed + 2)
    env = NetworkEnv(max_steps=50, fast_mode=True, local_mode=True,
                     train_links=TRAIN_LINKS, sim_seed=seed + 2)
    first_steps, no_anom = [], 0
    for _ in range(n_episodes):
        tr = rollout(env, rng)
        first = None
        for i, (_, _, _, anoms) in enumerate(tr, 1):
            if anoms:
                first = i
                break
        if first is None:
            no_anom += 1
        else:
            first_steps.append(first)
    env.close()
    mean_first = statistics.mean(first_steps) if first_steps else None
    return {
        "episode_steps": EPISODE_STEPS,
        "episodes_without_anomaly_pct": round(100.0 * no_anom / n_episodes, 2),
        "mean_first_anomaly_step": round(mean_first, 2) if mean_first else None,
        "mean_steps_left_after_onset": round(EPISODE_STEPS - mean_first, 2) if mean_first else None,
        "ticks_to_recover_1sigma": 1,     # collapse_diagnosis D4
        "ticks_to_recover_5sigma": 8,     # collapse_diagnosis D4
        "note": ("D4에 따르면 올바른 행동의 효과가 뚜렷해지는 데 8틱이 걸린다. "
                 "이상이 늦게 생기면 에피소드 안에서 결과를 볼 수 없다."),
    }


# ── D-A를 seed 여러 개로 — 한 번 재서는 방향이 뒤집힌다 ──────────────────────
def d_a_across_seeds(seeds: list[int], n_episodes: int) -> dict:
    """D-A는 표본 크기를 바꾸자 방향이 뒤집혔다(n=60에서는 즉시 보상 우세, n=300에서는 리턴 우세).

    한 번 잰 값으로 "리턴이 낫다"고 말할 수 없다. seed마다 **짝지어** 재고(같은 롤아웃에
    두 방식을 적용하므로 자연히 짝지어진다) 차이의 부호가 일관되는지 본다 — T3와 같은 규율.
    """
    rows = []
    for sd in seeds:
        r = d_a_return_vs_immediate(n_episodes, sd)
        rows.append({
            "seed": sd,
            "immediate": r["immediate"]["mean_rank_of_correct"],
            "discounted_return": r["discounted_return"]["mean_rank_of_correct"],
        })
    diffs = [x["discounted_return"] - x["immediate"] for x in rows]
    mean = statistics.mean(diffs)
    sd_ = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    same_sign = all(d > 0 for d in diffs) or all(d < 0 for d in diffs)
    return {
        "per_seed": rows,
        "paired_diffs_return_minus_immediate": [round(d, 2) for d in diffs],
        "mean_diff": round(mean, 2),
        "sd_diff": round(sd_, 2),
        "same_sign_across_seeds": same_sign,
        "random_baseline_rank": (N_ACTIONS + 1) / 2,
        "verdict": ("리턴이 일관되게 낫다" if same_sign and mean < 0 else
                    "즉시 보상이 일관되게 낫다" if same_sign and mean > 0 else
                    "방향이 일관되지 않다 — 어느 쪽이 낫다고 말할 수 없다"),
        "note": ("순위가 낮을수록 좋다(1위=정답 행동이 최고 평가). 무작위면 15.5. "
                 "균일 무작위 정책에서 잰 주변 평균이라 조잡한 대리 지표다 — "
                 "'학습이 실제로 나아진다'는 증거가 아니라 '신호가 있는가'를 볼 뿐이다."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "training_design_audit.json"))
    args = ap.parse_args()
    n_ep = 60 if args.quick else 300
    n_it = 20 if args.quick else 100

    print("학습 설계 감사 — 학습 알고리즘을 돌리지 않고 설계만 측정한다\n")

    print("── D-A. 즉시 보상 vs 할인 리턴: 정답 행동을 몇 위로 평가하는가")
    da = d_a_return_vs_immediate(n_ep, args.seed)
    for mode in ("immediate", "discounted_return"):
        v = da[mode]
        print(f"   {mode:20s} 정답 행동 순위 {v['correct_action_ranks']} / {v['n_actions']}"
              f"  (평균 {v['mean_rank_of_correct']})")

    seeds = [42, 43, 44, 45, 46]
    print(f"\n── D-A2. 같은 비교를 seed {len(seeds)}개로 (한 번 재서는 방향이 뒤집혔다)")
    da2 = d_a_across_seeds(seeds, n_ep)
    for r in da2["per_seed"]:
        print(f"   seed {r['seed']}: 즉시 {r['immediate']:>5.1f}위  리턴 {r['discounted_return']:>5.1f}위")
    print(f"   차이(리턴-즉시) {da2['paired_diffs_return_minus_immediate']}  "
          f"평균 {da2['mean_diff']}  SD {da2['sd_diff']}")
    print(f"   → {da2['verdict']}")

    print("\n── D-B. 태스크 분포: inner-loop가 적응할 대상이 있는가")
    db = d_b_task_distribution(n_it, args.seed)
    print(f"   현행: 이상 없는 태스크 {db['tasks_with_no_anomaly_pct']}%, "
          f"4개 태스크가 전부 동일한 반복 {db['iterations_where_all_4_tasks_identical_pct']}%")
    print(f"   예시 (첫 반복의 태스크 4개): {db['example_first_3_iterations'][0]}")
    dbp = d_b_proposed(n_it, args.seed)
    print(f"   제안: 이상 없는 태스크 {dbp['tasks_with_no_anomaly_pct']}%")
    print(f"   예시 (첫 반복의 태스크 4개): {dbp['example_first_3_iterations'][0]}")

    print("\n── D-C. 에피소드 안에 고칠 시간이 있는가")
    dc = d_c_time_budget(n_ep, args.seed)
    print(f"   에피소드 {dc['episode_steps']}스텝, 이상 첫 발생 평균 {dc['mean_first_anomaly_step']}스텝 "
          f"→ 남은 {dc['mean_steps_left_after_onset']}스텝")
    print(f"   이상이 아예 없는 에피소드 {dc['episodes_without_anomaly_pct']}%")

    doc = result_meta(
        seed=args.seed,
        condition=("학습 알고리즘을 돌리지 않고 설계만 측정한다. 균일 무작위 정책 — "
                   "붕괴가 학습 초반에 일어나므로 그 구간의 행동 분포다. "
                   "D-B의 '제안' 설계는 측정만 했고 학습에 쓰지 않았다."),
    )
    doc.update({"d_a_return_vs_immediate": da,
                "d_a2_across_seeds": da2,
                "d_b_task_distribution_current": db,
                "d_b_task_distribution_proposed": dbp,
                "d_c_time_budget": dc})
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
