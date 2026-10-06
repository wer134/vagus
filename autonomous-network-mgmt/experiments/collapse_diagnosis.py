"""정책 붕괴 원인 진단 (cowork/ROADMAP.md 후속 — T1이 "언제"를 답했고 이건 "왜"를 묻는다).

T1 곡선은 MAML이 학습의 24%, PPO가 16% 지점에서 상수 정책으로 붕괴한다는 것을 보여줬다.
붕괴가 학습 후반의 과적합이 아니라 **초반**에 일어난다는 사실은 알고리즘보다 환경·보상 쪽을
가리킨다. 이 스크립트는 알고리즘을 전혀 돌리지 않고 **환경만** 측정한다 — 학습이 쓸 수 있는
신호가 애초에 있는지를 본다.

측정 (각각 독립적으로 반증 가능한 가설):

  D1 상태 분포    학습 에피소드에서 "고칠 것이 있는 상태"의 비율.
                  대부분이 정상 상태라면 대부분의 스텝에서 행동은 무의미하다.
  D2 보상 지형    한 상태에서 30개 행동이 받는 보상의 퍼짐 vs 같은 행동을 반복했을 때의
                  노이즈. 신호 대 잡음비 < 1이면 gradient는 노이즈를 따라간다.
  D3 최적 행동    상태가 달라지면 최적 행동도 달라지는가. 어느 상태에서나 같은 행동이
                  최적이면 **상수 정책이 정답이고 붕괴는 올바른 수렴이다.**
  D4 지연 보상    올바른 행동 후 보상이 노이즈를 넘어서기까지 몇 틱 걸리는가.
                  즉시 보상이 움직이지 않으면 REINFORCE의 advantage는 그 행동을 칭찬하지 못한다.

    python3 experiments/collapse_diagnosis.py                 # 전체 (약 2~4분)
    python3 experiments/collapse_diagnosis.py --quick         # 반복 축소
    python3 experiments/collapse_diagnosis.py --out <경로>

결과는 experiments/results/collapse_diagnosis.json (E-4 규약).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROJECT, "simulation"))
sys.path.insert(0, os.path.join(PROJECT, "ai-engine"))

import metric_generator as mg          # noqa: E402
from reward import compute_reward      # noqa: E402
from topology import LINKS, OSPF_COSTS # noqa: E402
from _resultmeta import result_meta    # noqa: E402

N_ACTIONS = len(LINKS) * len(OSPF_COSTS)
TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]

# network_env의 학습 설정 — 여기서 바꾸지 않는다, 읽기만 한다
ANOMALY_PROB = 0.03
EPISODE_STEPS = 30
WARMUP_TICKS = 5      # 혼잡 주입 후 스트레스가 쌓인 "사고 진행 중" 상태를 만든다


def action_name(a: int) -> str:
    li, ci = divmod(a, len(OSPF_COSTS))
    return f"{LINKS[li]}@{OSPF_COSTS[ci]}"


def apply_action(a: int) -> None:
    li, ci = divmod(a, len(OSPF_COSTS))
    mg.set_ospf_cost(LINKS[li], OSPF_COSTS[ci])


def reward_now() -> float:
    m = mg.get_all_metrics()
    return compute_reward(
        latencies=[x["latency"] for x in m],
        bandwidths=[x["bandwidth"] for x in m],
        packet_losses=[x["packetLoss"] for x in m],
    )


def build_state(congested: str | None, noise_seed: int) -> None:
    """지정한 상태를 처음부터 재구성한다. 같은 noise_seed면 노이즈 추첨까지 동일하다."""
    mg.reset_state(seed=noise_seed)
    for lk in LINKS:
        mg.set_ospf_cost(lk, 10)
    if congested:
        mg.inject_congestion(congested)
        mg.tick(WARMUP_TICKS)
    else:
        mg.tick(WARMUP_TICKS)


# ── D1. 학습 에피소드에서 "고칠 것이 있는" 스텝의 비율 ────────────────────────
def d1_state_distribution(n_episodes: int, seed: int) -> dict:
    """network_env의 이상 주입 규칙을 그대로 재현해 상태 분포를 센다.

    reset()은 이상을 주입하지 않는다 — 에피소드는 정상 상태에서 시작하고, 스텝마다
    ANOMALY_PROB로 주입된다. 주입된 혼잡은 120스텝 타이머라 30스텝 에피소드 안에서는
    저절로 사라지지 않는다.
    """
    rng = random.Random(seed)
    steps_total = 0
    steps_with_anomaly = 0
    eps_without_any = 0
    first_anomaly_steps = []

    for _ in range(n_episodes):
        active: set[str] = set()
        first = None
        for t in range(EPISODE_STEPS):
            # network_env.step(): 행동 적용 → _maybe_inject_anomaly() → tick
            # 행동 시점의 상태는 "직전 스텝까지 쌓인 이상"이다
            steps_total += 1
            if active:
                steps_with_anomaly += 1
            if rng.random() < ANOMALY_PROB and len(active) < 2:
                lk = rng.choice(TRAIN_LINKS)
                active.add(lk)
                if first is None:
                    first = t + 1
        if not active:
            eps_without_any += 1
        if first is not None:
            first_anomaly_steps.append(first)

    return {
        "n_episodes": n_episodes,
        "episode_steps": EPISODE_STEPS,
        "anomaly_prob_per_step": ANOMALY_PROB,
        "steps_total": steps_total,
        "steps_with_active_anomaly": steps_with_anomaly,
        "pct_steps_actionable": round(100.0 * steps_with_anomaly / steps_total, 2),
        "pct_episodes_with_no_anomaly_at_all": round(100.0 * eps_without_any / n_episodes, 2),
        "mean_first_anomaly_step": round(statistics.mean(first_anomaly_steps), 2) if first_anomaly_steps else None,
    }


# ── D2/D3. 보상 지형: 행동 간 퍼짐 vs 노이즈 ──────────────────────────────────
def d2_reward_landscape(states: list[str | None], repeats: int, base_seed: int) -> dict:
    """각 상태에서 30개 행동의 즉시 보상을 재고, 행동 간 퍼짐과 노이즈를 분리한다.

    같은 noise_seed로 상태를 재구성하므로 행동끼리는 **짝지어 비교**된다 — 두 행동의 보상 차이가
    노이즈 때문인지 행동 때문인지 구분할 수 있다.
    """
    out = {}
    for st in states:
        key = st or "healthy"
        per_action_means = []
        per_action_sds = []
        paired_diffs = []        # 같은 noise_seed 안에서 (최고 행동 - 최저 행동)

        by_seed: list[list[float]] = []
        for r in range(repeats):
            seed = base_seed + r
            row = []
            for a in range(N_ACTIONS):
                build_state(st, seed)
                apply_action(a)
                mg.tick()
                row.append(reward_now())
            by_seed.append(row)
            paired_diffs.append(max(row) - min(row))

        for a in range(N_ACTIONS):
            col = [by_seed[r][a] for r in range(repeats)]
            per_action_means.append(statistics.mean(col))
            per_action_sds.append(statistics.pstdev(col) if repeats > 1 else 0.0)

        spread = max(per_action_means) - min(per_action_means)
        noise_sd = statistics.mean(per_action_sds)
        best_i = max(range(N_ACTIONS), key=lambda i: per_action_means[i])
        worst_i = min(range(N_ACTIONS), key=lambda i: per_action_means[i])

        ranked = sorted(range(N_ACTIONS), key=lambda i: -per_action_means[i])
        out[key] = {
            "mean_reward": round(statistics.mean(per_action_means), 5),
            "spread_across_actions": round(spread, 5),
            "noise_sd_within_action": round(noise_sd, 5),
            "snr": round(spread / noise_sd, 3) if noise_sd > 1e-9 else None,
            "paired_max_minus_min_mean": round(statistics.mean(paired_diffs), 5),
            "best_action": action_name(best_i),
            "best_action_reward": round(per_action_means[best_i], 5),
            "worst_action": action_name(worst_i),
            "worst_action_reward": round(per_action_means[worst_i], 5),
            "top5": [{"action": action_name(i), "reward": round(per_action_means[i], 5)} for i in ranked[:5]],
            # 혼잡 링크를 우회시키는 "정답" 행동의 순위
            "correct_action": f"{st}@100" if st else None,
            "correct_action_rank": (
                1 + ranked.index(LINKS.index(st) * len(OSPF_COSTS) + OSPF_COSTS.index(100))
                if st else None
            ),
        }
    return out


# ── D4. 올바른 행동의 효과가 보상에 나타나기까지 ──────────────────────────────
def d4_delayed_credit(link: str, horizon: int, repeats: int, base_seed: int) -> dict:
    """혼잡 링크를 우회(cost=100)시킨 뒤 보상이 노이즈를 넘어서는 데 몇 틱 걸리는지."""
    correct = [[] for _ in range(horizon)]
    wrong = [[] for _ in range(horizon)]

    other = next(lk for lk in LINKS if lk != link)
    for r in range(repeats):
        seed = base_seed + r
        for label, (target, cost) in (("c", (link, 100)), ("w", (other, 10))):
            build_state(link, seed)
            mg.set_ospf_cost(target, cost)
            for t in range(horizon):
                mg.tick()
                (correct if label == "c" else wrong)[t].append(reward_now())

    rows = []
    first_separating_tick = None
    for t in range(horizon):
        mc, mw = statistics.mean(correct[t]), statistics.mean(wrong[t])
        sd = (statistics.pstdev(correct[t]) + statistics.pstdev(wrong[t])) / 2 if repeats > 1 else 0.0
        sep = (mc - mw) / sd if sd > 1e-9 else None
        rows.append({
            "tick": t + 1,
            "reward_correct": round(mc, 5),
            "reward_wrong": round(mw, 5),
            "diff": round(mc - mw, 5),
            "noise_sd": round(sd, 5),
            "separation_sigma": round(sep, 2) if sep is not None else None,
        })
        if first_separating_tick is None and sep is not None and sep >= 1.0:
            first_separating_tick = t + 1

    return {
        "congested_link": link,
        "correct_action": f"{link}@100",
        "wrong_action": f"{other}@10",
        "horizon_ticks": horizon,
        "repeats": repeats,
        "first_tick_separated_1sigma": first_separating_tick,
        "curve": rows,
    }


# ── D5. 실제 학습 신호 분해: advantage는 무엇을 반영하는가 ────────────────────
def d5_advantage_decomposition(n_episodes: int, seed: int) -> dict:
    """NetworkEnv를 그대로 돌려 REINFORCE가 쓰는 advantage를 만들고, 그 분산을 쪼갠다.

    `_reinforce_loss`의 baseline은 **에피소드 보상의 평균이라는 스칼라 하나**다. 상태에 따라
    보상이 크게 달라지면(정상 0.68 / 혼잡 0.32) 스칼라 baseline은 그 차이를 제거하지 못하고,
    advantage는 "무슨 행동을 했는가"가 아니라 "어떤 상태에 있었는가"를 반영하게 된다.
    그러면 정상 상태에서 우연히 뽑힌 행동이 큰 양의 advantage로 강화된다.

    eta^2(결정계수)로 분해한다:
      eta2_state  : advantage 분산 중 '이상 유무'가 설명하는 비율
      eta2_action : 같은 상태 안에서 '어떤 행동이었나'가 설명하는 비율
    둘을 비교하면 gradient가 무엇을 따라가는지 바로 보인다.

    같은 데이터에 상태별 baseline(V(s) 대용)을 적용했을 때 행동 신호 비율이 어떻게 변하는지도
    함께 잰다 — 수정 방향의 근거가 된다.
    """
    sys.path.insert(0, os.path.join(PROJECT, "ai-engine"))
    from environment.network_env import NetworkEnv

    rng = random.Random(seed)
    env = NetworkEnv(max_steps=50, fast_mode=True, local_mode=True,
                     train_links=TRAIN_LINKS, sim_seed=seed)

    rows = []   # (ep, has_anomaly, action, reward)
    for ep in range(n_episodes):
        env.reset()
        for _ in range(EPISODE_STEPS):
            a = rng.randrange(N_ACTIONS)
            _, r, _, trunc, info = env.step(a)
            rows.append((ep, bool(info["anomalies"]), a, float(r)))
            if trunc:
                break
    env.close()

    def eta2(groups: dict) -> float:
        """집단 간 분산 / 전체 분산."""
        vals = [v for g in groups.values() for v in g]
        if len(vals) < 2:
            return 0.0
        gm = statistics.mean(vals)
        ss_total = sum((v - gm) ** 2 for v in vals)
        ss_between = sum(len(g) * (statistics.mean(g) - gm) ** 2 for g in groups.values() if g)
        return ss_between / ss_total if ss_total > 1e-12 else 0.0

    def advantages(baseline_mode: str) -> list[tuple[bool, int, float]]:
        """baseline_mode='episode_mean'(현행) | 'state_mean'(상태별 baseline)"""
        out = []
        by_ep: dict[int, list] = {}
        for ep, anom, a, r in rows:
            by_ep.setdefault(ep, []).append((anom, a, r))
        for ep, items in by_ep.items():
            rs = [r for _, _, r in items]
            if baseline_mode == "episode_mean":
                base = {True: statistics.mean(rs), False: statistics.mean(rs)}
            else:
                per = {}
                for flag in (True, False):
                    sub = [r for anom, _, r in items if anom == flag]
                    per[flag] = statistics.mean(sub) if sub else statistics.mean(rs)
                base = per
            adv = [r - base[anom] for anom, _, r in items]
            # _reinforce_loss와 동일한 분산 정규화
            sd = statistics.pstdev(adv)
            if sd > 1e-8:
                m = statistics.mean(adv)
                adv = [(x - m) / (sd + 1e-8) for x in adv]
            for (anom, a, _), x in zip(items, adv):
                out.append((anom, a, x))
        return out

    report = {}
    for mode in ("episode_mean", "state_mean"):
        adv = advantages(mode)
        by_state = {True: [], False: []}
        for anom, _, x in adv:
            by_state[anom].append(x)
        # 행동 효과는 상태를 고정한 뒤에 본다 (혼잡 상태 = 고칠 것이 있는 상태)
        by_action_anom: dict[int, list] = {}
        for anom, a, x in adv:
            if anom:
                by_action_anom.setdefault(a, []).append(x)

        e_state = eta2({k: v for k, v in by_state.items() if v})
        e_action = eta2(by_action_anom)
        report[mode] = {
            "eta2_state": round(e_state, 5),
            "eta2_action_within_anomaly": round(e_action, 5),
            "ratio_state_over_action": round(e_state / e_action, 1) if e_action > 1e-9 else None,
            "mean_adv_when_healthy": round(statistics.mean(by_state[False]), 4) if by_state[False] else None,
            "mean_adv_when_anomaly": round(statistics.mean(by_state[True]), 4) if by_state[True] else None,
        }

    # 현행 baseline에서 어떤 행동이 가장 강화되는가 — 정답 행동인가, 아무 행동인가
    adv = advantages("episode_mean")
    totals: dict[int, float] = {}
    for _, a, x in adv:
        totals[a] = totals.get(a, 0.0) + x
    ranked = sorted(totals, key=lambda a: -totals[a])
    correct = {LINKS.index(lk) * len(OSPF_COSTS) + OSPF_COSTS.index(100) for lk in TRAIN_LINKS}
    report["most_reinforced"] = {
        "top5": [{"action": action_name(a), "total_advantage": round(totals[a], 2)} for a in ranked[:5]],
        "correct_actions": sorted(action_name(a) for a in correct),
        "correct_action_ranks": sorted(1 + ranked.index(a) for a in correct if a in ranked),
        "n_actions": N_ACTIONS,
    }
    # 이상이 한 번도 없는 에피소드는 gradient에 무엇을 기여하는가.
    # `_reinforce_loss`는 advantage를 표준편차로 나눈다. 보상이 노이즈뿐인 에피소드에서도
    # 그 노이즈가 단위 분산으로 **증폭**되어, 신호가 있는 에피소드와 같은 크기의 gradient를
    # 만든다. 정규화가 "신호 없음"을 "신호 있음"과 구별할 수 없게 만든다.
    by_ep: dict[int, list] = {}
    for ep, anom, a, r in rows:
        by_ep.setdefault(ep, []).append((anom, a, r))
    quiet_mag, active_mag, quiet_n = [], [], 0
    for ep, items in by_ep.items():
        rs = [r for _, _, r in items]
        adv = [r - statistics.mean(rs) for r in rs]
        sd = statistics.pstdev(adv)
        if sd > 1e-8:
            m = statistics.mean(adv)
            adv = [(x - m) / (sd + 1e-8) for x in adv]
        mag = statistics.mean(abs(x) for x in adv)
        if any(anom for anom, _, _ in items):
            active_mag.append(mag)
        else:
            quiet_mag.append(mag)
            quiet_n += 1
        raw_spread = max(rs) - min(rs)
        (active_mag if any(anom for anom, _, _ in items) else quiet_mag)
    report["normalization_effect"] = {
        "episodes_without_any_anomaly": quiet_n,
        "episodes_with_anomaly": len(by_ep) - quiet_n,
        "mean_abs_advantage_quiet_episodes": round(statistics.mean(quiet_mag), 4) if quiet_mag else None,
        "mean_abs_advantage_active_episodes": round(statistics.mean(active_mag), 4) if active_mag else None,
        "note": ("정규화 후 두 값이 같으면, 고칠 것이 없어 보상이 노이즈뿐인 에피소드도 "
                 "신호가 있는 에피소드와 같은 크기의 gradient를 만든다는 뜻이다."),
    }
    report["n_episodes"] = n_episodes
    report["policy"] = "uniform random (학습 초기의 탐색 분포)"
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "collapse_diagnosis.json"))
    args = ap.parse_args()

    repeats = 8 if args.quick else 24
    episodes = 2000 if args.quick else 20000

    print(f"정책 붕괴 원인 진단 (repeats={repeats}, episodes={episodes}, seed={args.seed})\n")

    print("── D1. 학습 에피소드의 상태 분포")
    d1 = d1_state_distribution(episodes, args.seed)
    print(f"   고칠 것이 있는 스텝: {d1['pct_steps_actionable']}%")
    print(f"   이상이 한 번도 없는 에피소드: {d1['pct_episodes_with_no_anomaly_at_all']}%\n")

    print("── D2/D3. 보상 지형 (상태별 30개 행동)")
    states: list[str | None] = [None] + TRAIN_LINKS
    d2 = d2_reward_landscape(states, repeats, args.seed * 1000)
    for k, v in d2.items():
        snr = v["snr"]
        print(f"   {k:8s}: 행동 간 퍼짐 {v['spread_across_actions']:.4f}  "
              f"노이즈 {v['noise_sd_within_action']:.4f}  SNR {snr if snr is not None else '—'}")
        print(f"             최적 {v['best_action']:12s} "
              + (f"정답({v['correct_action']}) 순위 {v['correct_action_rank']}/30" if v["correct_action"] else ""))
    best_actions = {v["best_action"] for v in d2.values()}
    print(f"   → 상태별 최적 행동이 {len(best_actions)}종류: {sorted(best_actions)}\n")

    print("── D4. 올바른 행동 후 보상이 분리되기까지")
    d4 = d4_delayed_credit(TRAIN_LINKS[0], 15, repeats, args.seed * 2000)
    print(f"   1σ 분리 시점: {d4['first_tick_separated_1sigma']}틱")
    for row in d4["curve"][:8]:
        print(f"     t+{row['tick']:<2d} 정답 {row['reward_correct']:.4f}  오답 {row['reward_wrong']:.4f}  "
              f"차이 {row['diff']:+.4f}  ({row['separation_sigma']}σ)")

    print("\n── D5. 실제 학습 신호(advantage)는 무엇을 반영하는가")
    d5 = d5_advantage_decomposition(60 if args.quick else 300, args.seed)
    for mode in ("episode_mean", "state_mean"):
        v = d5[mode]
        print(f"   baseline={mode:13s} 상태 설명력 {v['eta2_state']:.4f}  "
              f"행동 설명력 {v['eta2_action_within_anomaly']:.4f}  "
              f"비율 {v['ratio_state_over_action']}:1")
    print(f"   현행 baseline에서 가장 강화되는 행동: "
          f"{', '.join(x['action'] for x in d5['most_reinforced']['top5'][:3])}")
    print(f"   정답 행동들의 순위: {d5['most_reinforced']['correct_action_ranks']} / 30")
    ne = d5["normalization_effect"]
    print(f"   정규화 후 |advantage| 평균 — 이상 없는 에피소드 {ne['mean_abs_advantage_quiet_episodes']} "
          f"vs 이상 있는 에피소드 {ne['mean_abs_advantage_active_episodes']} "
          f"({ne['episodes_without_any_anomaly']}/{len(d5['most_reinforced']['correct_actions']) and d5['n_episodes']} 에피소드가 전자)")

    doc = result_meta(
        seed=args.seed,
        condition=(
            "환경만 측정 — 학습 알고리즘을 돌리지 않는다. 각 상태를 같은 noise_seed로 재구성해 "
            "행동끼리 짝지어 비교한다. D1은 network_env의 이상 주입 규칙(ANOMALY_PROB=0.03, "
            f"episode_steps={EPISODE_STEPS}, reset 시 이상 없음)을 그대로 재현한 것이다. "
            f"혼잡 상태는 주입 후 {WARMUP_TICKS}틱 경과 시점."
        ),
        repeats=repeats,
    )
    doc.update({
        "d1_state_distribution": d1,
        "d2_reward_landscape": d2,
        "d3_optimal_action_distinct_count": len(best_actions),
        "d3_optimal_actions": sorted(best_actions),
        "d4_delayed_credit": d4,
        "d5_advantage_decomposition": d5,
    })
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n→ {args.out}")


if __name__ == "__main__":
    main()
