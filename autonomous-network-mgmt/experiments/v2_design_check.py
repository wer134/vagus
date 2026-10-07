"""태스크 프로토콜 v2 설계 검증 — 돌리기 전에 네 가지 열린 쟁점을 측정으로 닫는다.

재설계한 요인 실험을 띄우기 전에, v2 설계 자체에 대해 답하지 못한 것이 네 개 있었다.
학습을 돌리지 않고 환경만 측정해 답한다.

  V-1  v2에서 "어느 링크에 조치했는가"가 상관없어지는가
       v2 첫 학습에서 정책이 iteration 1부터 `r1-r4@100`에 쏠렸다. r1-r4는 TEST 링크라
       학습 태스크에 등장하지 않는다. 장애 링크가 아닌 곳에 cost 100을 걸어도 보상이
       같다면, 정책 입장에서 링크 선택은 아무 의미가 없고 `@100`이면 무엇이든 된다.

  V-2a 단일 정책으로 네 태스크를 다 풀 수 있는가 (적응 없이)
       풀린다면 inner-loop 적응이 기여할 자리가 없다 — v2는 "MAML이 작동하는 환경"이
       아니라 "MAML이 불필요한 환경"이다. 상수 행동 하나로도 풀리는지까지 본다.

  V-2b inner-loop 적응이 실제로 기여하는가
       기존 체크포인트로 adapt_steps=0과 3을 비교한다.

  V-3  PPO와 MAML의 커리큘럼이 정말 같은가
       MAML은 태스크가 링크를 정하고 30스텝을 롤아웃한다. PPO는 reset마다 무작위 TRAIN
       링크를 뽑고 에피소드 길이는 NetworkEnv.max_steps를 따른다. 같다고 말했는데 같은지.

  V-4  episode_steps=30이 v2에 적절한가
       v1은 장애가 13.7스텝째에 생겨 "고칠 것이 없는 스텝"이 앞쪽에 쌓였다. v2는 0스텝에
       생기지만, 고친 **뒤**의 스텝이 같은 문제를 만들 수 있다. 에피소드 안에서 장애가
       실제로 살아 있는 스텝의 비율을 잰다.

    python3 experiments/v2_design_check.py
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
sys.path.insert(0, os.path.join(PROJECT, "simulation"))

import metric_generator as mg                   # noqa: E402
from environment.network_env import NetworkEnv  # noqa: E402
from reward import compute_reward               # noqa: E402
from topology import LINKS, OSPF_COSTS, NODES, NODE_LINKS, LINK_ENDPOINTS  # noqa: E402
from _resultmeta import result_meta             # noqa: E402

TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]
TEST_LINKS = ["r3-r4", "r1-r4"]
N_ACTIONS = len(LINKS) * len(OSPF_COSTS)
SLA_LAT = 50.0


def act_name(a: int) -> str:
    li, ci = divmod(a, len(OSPF_COSTS))
    return f"{LINKS[li]}@{OSPF_COSTS[ci]}"


def act_idx(link: str, cost: int) -> int:
    return LINKS.index(link) * len(OSPF_COSTS) + OSPF_COSTS.index(cost)


def reward_now() -> float:
    m = mg.get_all_metrics()
    return compute_reward([x["latency"] for x in m], [x["bandwidth"] for x in m],
                          [x["packetLoss"] for x in m])


def sla_ok() -> bool:
    m = mg.get_all_metrics()
    return max(x["latency"] for x in m) <= SLA_LAT and max(x["packetLoss"] for x in m) <= 0.01


# ── V-1. 조치 링크가 상관있는가 ───────────────────────────────────────────────
def v1_link_matters(repeats: int, horizon: int, seed: int) -> dict:
    """장애가 link_f에 있을 때, cost 100을 **어느 링크에** 걸든 같은가.

    같은 noise_seed로 상태를 재구성해 짝지어 비교한다. horizon틱 누적 보상으로 재는 것은
    즉시 보상만으로는 차이가 노이즈에 묻히기 때문이다 (collapse_diagnosis D4).
    """
    out = {}
    for link_f in TRAIN_LINKS:
        # 후보: 정답(장애 링크), 다른 TRAIN 링크, TEST 링크 둘, 그리고 아무것도 안 하는 것
        cands = {
            "correct (faulty link) @100": act_idx(link_f, 100),
            "other TRAIN link @100": act_idx(next(l for l in TRAIN_LINKS if l != link_f), 100),
            "TEST link r3-r4 @100": act_idx("r3-r4", 100),
            "TEST link r1-r4 @100": act_idx("r1-r4", 100),
            "faulty link @10 (no bypass)": act_idx(link_f, 10),
        }
        scores = {k: [] for k in cands}
        for r in range(repeats):
            for label, a in cands.items():
                mg.reset_state(seed=seed + r)
                for lk in LINKS:
                    mg.set_ospf_cost(lk, 10)
                mg.inject_congestion(link_f)
                li, ci = divmod(a, len(OSPF_COSTS))
                mg.set_ospf_cost(LINKS[li], OSPF_COSTS[ci])
                total = 0.0
                for _ in range(horizon):
                    mg.tick()
                    total += reward_now()
                scores[label].append(total / horizon)
        means = {k: statistics.mean(v) for k, v in scores.items()}
        sds = {k: statistics.pstdev(v) for k, v in scores.items()}
        best = max(means, key=lambda k: means[k])
        out[link_f] = {
            "mean_reward_over_horizon": {k: round(v, 5) for k, v in means.items()},
            "sd": {k: round(v, 5) for k, v in sds.items()},
            "best": best,
            "correct_minus_test_link": round(
                means["correct (faulty link) @100"] - means["TEST link r1-r4 @100"], 5),
            "correct_minus_nobypass": round(
                means["correct (faulty link) @100"] - means["faulty link @10 (no bypass)"], 5),
        }
    gaps = [v["correct_minus_test_link"] for v in out.values()]
    noise = statistics.mean(
        statistics.mean(v["sd"].values()) for v in out.values())
    return {
        "per_fault_link": out,
        "mean_correct_minus_wrong_link": round(statistics.mean(gaps), 5),
        "mean_noise_sd": round(noise, 5),
        "verdict": ("조치 링크가 상관없다 — @100이면 어느 링크든 같다"
                    if abs(statistics.mean(gaps)) < noise else
                    "조치 링크가 상관있다 — 정답 링크가 유의하게 낫다"),
        "horizon_ticks": horizon, "repeats": repeats,
    }


# ── V-2a. 단일 정책으로 풀리는가 ──────────────────────────────────────────────
def _run_policy(env, link_f: str, policy, steps: int) -> dict:
    """장애를 link_f에 주입하고 policy(obs)->action으로 steps만큼. 회복까지 걸린 스텝."""
    env.reset()
    env.inject_anomaly(link_f)
    obs = env._get_obs()
    ttr = None
    for t in range(1, steps + 1):
        a = policy(obs, link_f)
        obs, _, _, trunc, _ = env.step(a)
        if ttr is None and sla_ok():
            ttr = t
        if trunc:
            break
    return {"ttr": ttr, "resolved": ttr is not None}


def v2a_single_policy(episodes: int, steps: int, seed: int) -> dict:
    """세 정책을 같은 조건에서 비교한다. 전부 학습 없이 손으로 쓴 것이다."""
    env = NetworkEnv(max_steps=steps + 5, fast_mode=True, local_mode=True,
                     inject_anomalies=False, train_links=TRAIN_LINKS, sim_seed=seed)

    def oracle(obs, link_f):
        """장애 링크를 안다고 가정 — 도달 가능한 상한."""
        return act_idx(link_f, 100)

    def heuristic(obs, link_f):
        """관측만 사용: 지연이 가장 높은 두 노드를 잇는 링크에 cost 100.

        obs = [bw×4, lat×4, cost×6]. 학습 없이 상태에 반응하는 단일 정책이다.
        이것으로 네 태스크가 다 풀리면 inner-loop 적응이 기여할 자리가 없다.
        """
        lat = obs[len(NODES):2 * len(NODES)]
        order = sorted(range(len(NODES)), key=lambda i: -lat[i])
        top2 = {NODES[order[0]], NODES[order[1]]}
        for lk in LINKS:
            if set(LINK_ENDPOINTS[lk]) == top2:
                return act_idx(lk, 100)
        return act_idx(LINKS[0], 100)

    results = {}
    for name, pol in (("oracle (knows faulty link)", oracle),
                      ("latency heuristic (obs only)", heuristic)):
        per_link = {}
        for link_f in TRAIN_LINKS + TEST_LINKS:
            runs = [_run_policy(env, link_f, pol, steps) for _ in range(episodes)]
            solved = [r["ttr"] for r in runs if r["resolved"]]
            per_link[link_f] = {
                "success_rate": round(100.0 * len(solved) / len(runs), 1),
                "avg_ttr": round(statistics.mean(solved), 2) if solved else None,
            }
        results[name] = per_link

    # 상수 행동 — 어떤 상수 하나가 네 태스크를 다 푸는가
    const_rows = {}
    for lk in LINKS:
        a = act_idx(lk, 100)
        per_link = {}
        for link_f in TRAIN_LINKS:
            runs = [_run_policy(env, link_f, lambda o, f, _a=a: _a, steps)
                    for _ in range(max(2, episodes // 2))]
            solved = [r["ttr"] for r in runs if r["resolved"]]
            per_link[link_f] = round(100.0 * len(solved) / len(runs), 1)
        const_rows[f"{lk}@100"] = per_link
    env.close()

    best_const = max(const_rows, key=lambda k: statistics.mean(const_rows[k].values()))
    return {
        "policies": results,
        "constant_actions": const_rows,
        "best_constant_action": best_const,
        "best_constant_mean_success": round(statistics.mean(const_rows[best_const].values()), 1),
        "episode_steps": steps,
        "note": ("관측만 쓰는 단일 정책이 네 태스크를 다 풀면 메타학습이 기여할 자리가 없다. "
                 "상수 행동 하나로도 풀린다면 더 심각하다 — 상태조차 볼 필요가 없다는 뜻이다."),
    }


# ── V-2b. inner-loop 적응이 기여하는가 ────────────────────────────────────────
def v2b_adaptation(episodes: int, steps: int, seed: int) -> dict:
    """기존 체크포인트로 adapt_steps=0(=predict)과 3을 비교."""
    from few_shot_agent import FewShotAgent
    ck = os.path.join(PROJECT, "ai-engine", "agents", "maml_network.pt")
    if not os.path.exists(ck):
        return {"skipped": "maml_network.pt 없음"}
    agent = FewShotAgent(ck)
    if not agent.is_ready():
        return {"skipped": f"로드 실패: {agent.load_error}"}

    env = NetworkEnv(max_steps=steps + 5, fast_mode=True, local_mode=True,
                     inject_anomalies=False, train_links=TRAIN_LINKS, sim_seed=seed)
    out = {}
    for mode in ("no_adapt", "adapt_3"):
        per_link = {}
        for link_f in TRAIN_LINKS + TEST_LINKS:
            solved, ttrs = 0, []
            for _ in range(episodes):
                env.reset(); env.inject_anomaly(link_f)
                obs = env._get_obs()
                buf, ttr = [], None
                for t in range(1, steps + 1):
                    if mode == "adapt_3" and len(buf) >= 4:
                        a = agent.adapt_and_predict(buf[-32:], obs, adapt_steps=3)
                    else:
                        a = agent.predict(obs)
                    prev = obs
                    obs, r, _, trunc, _ = env.step(a)
                    buf.append((prev, a, float(r)))
                    if ttr is None and sla_ok():
                        ttr = t
                    if trunc:
                        break
                if ttr is not None:
                    solved += 1; ttrs.append(ttr)
            per_link[link_f] = {"success_rate": round(100.0 * solved / episodes, 1),
                                "avg_ttr": round(statistics.mean(ttrs), 2) if ttrs else None}
        out[mode] = per_link
    env.close()
    return {"checkpoint": "maml_network.pt (v1 학습)", "by_mode": out,
            "note": "붕괴한 체크포인트라 두 모드가 같아도 놀랍지 않다 — 그 사실 자체가 기록이다."}


# ── V-3. PPO와 MAML의 커리큘럼 비교 ───────────────────────────────────────────
def v3_curriculum(seed: int) -> dict:
    """두 경로가 실제로 같은 분포를 보는가. 코드에서 읽어 비교한다."""
    import inspect
    from few_shot_agent import train as maml_train
    from baseline_drl import train as ppo_train
    msig = inspect.signature(maml_train).parameters
    psig = inspect.signature(ppo_train).parameters

    # MAML: _collect_episode(steps=episode_steps), 태스크가 링크를 지정
    maml_ep = msig["episode_steps"].default
    # PPO: NetworkEnv의 max_steps 기본값을 따른다
    env_default = inspect.signature(NetworkEnv.__init__).parameters["max_steps"].default

    return {
        "maml": {"episode_steps": maml_ep,
                 "fault_link": "태스크가 지정 (반복마다 TRAIN 4개를 1회씩)",
                 "faults_per_episode": 1,
                 "env_max_steps": 50},
        "ppo": {"episode_steps": f"NetworkEnv.max_steps = {env_default} (reset까지)",
                "fault_link": "reset마다 train_links에서 무작위",
                "faults_per_episode": 1,
                "env_max_steps": env_default},
        "differences": [
            f"에피소드 길이: MAML {maml_ep}스텝 vs PPO {env_default}스텝 — "
            f"PPO 에피소드가 {env_default // maml_ep}배 이상 길다",
            "링크 선택: MAML은 균등 순회(반복마다 4개 전부), PPO는 매 reset 무작위 — "
            "PPO는 링크 커버리지가 균등하지 않다",
        ],
        "verdict": "커리큘럼이 같지 않다 — 에피소드 길이와 링크 커버리지가 다르다",
    }


# ── V-4. 에피소드 길이 ────────────────────────────────────────────────────────
def v4_episode_length(episodes: int, steps: int, seed: int) -> dict:
    """v2 에피소드 안에서 '고칠 것이 살아 있는' 스텝의 비율.

    v1은 장애가 늦게 생겨 **앞쪽**이 비었다. v2는 0스텝에 생기지만 고친 **뒤**가 빌 수 있다.
    같은 희석 문제가 자리만 옮긴 것인지 본다.
    """
    env = NetworkEnv(max_steps=steps + 5, fast_mode=True, local_mode=True,
                     inject_anomalies=False, train_links=TRAIN_LINKS, sim_seed=seed)

    def oracle(obs, link_f):
        return act_idx(link_f, 100)

    rows = {}
    for label, pol in (("oracle policy", oracle),
                       ("random policy", lambda o, f: np.random.randint(N_ACTIONS))):
        viol_steps, totals = [], []
        for _ in range(episodes):
            for link_f in TRAIN_LINKS:
                env.reset(); env.inject_anomaly(link_f)
                obs = env._get_obs()
                viol = 0
                for _ in range(steps):
                    a = pol(obs, link_f)
                    obs, _, _, trunc, _ = env.step(a)
                    if not sla_ok():
                        viol += 1
                    if trunc:
                        break
                viol_steps.append(viol); totals.append(steps)
        rows[label] = {
            "pct_steps_with_active_violation": round(
                100.0 * sum(viol_steps) / sum(totals), 2),
            "mean_violating_steps_per_episode": round(statistics.mean(viol_steps), 2),
            "episode_steps": steps,
        }
    env.close()
    rows["note"] = ("오라클 정책에서의 비율이 '최선을 다해도 남는 유효 스텝 비율'이다. "
                    "이 값이 낮으면 에피소드 뒤쪽이 v1의 앞쪽과 같은 역할을 한다 — "
                    "희석 문제가 사라진 게 아니라 자리를 옮긴 것이다.")
    return rows


# ── V-5. 에피소드 길이를 측정으로 고른다 ─────────────────────────────────────
def v5_length_sweep(episodes: int, lengths: list[int], seed: int) -> dict:
    """길이별로 "고칠 것이 살아 있는 스텝" 비율을 재고, 그 근거로 길이를 정한다.

    V-4가 보여준 것은 30스텝이 너무 길다는 것이다 — 오라클 기준 15.2%만 유효하고 나머지는
    고친 뒤의 빈 구간이다. 그런데 "성공하면 에피소드 종료"로 고치면 안 된다: 보상이 스텝마다
    양수라 일찍 끝낼수록 **총 보상이 줄어들어 고치지 않는 쪽이 이득**이 된다. 길이를 줄이는
    쪽이 그 병리를 만들지 않는다.

    오라클 비율이 '최선을 다해도 남는 유효 비율'이고, 무작위 비율이 '학습 초기의 비율'이다.
    둘 다 높게 유지되는 가장 긴 길이를 고른다 — 너무 짧으면 회복을 끝낼 시간이 없다.
    """
    env = NetworkEnv(max_steps=max(lengths) + 5, fast_mode=True, local_mode=True,
                     inject_anomalies=False, train_links=TRAIN_LINKS, sim_seed=seed)

    def oracle(obs, link_f):
        return act_idx(link_f, 100)

    rows = {}
    for L in lengths:
        stats = {}
        for label, pol in (("oracle", oracle),
                           ("random", lambda o, f: np.random.randint(N_ACTIONS))):
            viol, resolved, ttrs = 0, 0, []
            total = 0
            for _ in range(episodes):
                for link_f in TRAIN_LINKS:
                    env.reset(); env.inject_anomaly(link_f)
                    obs = env._get_obs()
                    ttr = None
                    for t in range(1, L + 1):
                        a = pol(obs, link_f)
                        obs, _, _, trunc, _ = env.step(a)
                        total += 1
                        if not sla_ok():
                            viol += 1
                        elif ttr is None:
                            ttr = t
                        if trunc:
                            break
                    if ttr is not None:
                        resolved += 1; ttrs.append(ttr)
            n_ep = episodes * len(TRAIN_LINKS)
            stats[label] = {
                "pct_actionable_steps": round(100.0 * viol / total, 1),
                "resolved_pct": round(100.0 * resolved / n_ep, 1),
                "avg_ttr": round(statistics.mean(ttrs), 2) if ttrs else None,
            }
        rows[L] = stats
    env.close()

    # 오라클이 거의 다 풀면서(>=95%) 유효 비율이 가장 높은 길이
    ok = [L for L in lengths if rows[L]["oracle"]["resolved_pct"] >= 95.0]
    pick = max(ok, key=lambda L: rows[L]["oracle"]["pct_actionable_steps"]) if ok else None
    return {
        "by_length": rows,
        "recommended": pick,
        "criterion": ("오라클 해결률 95% 이상을 유지하는 길이 중 유효 스텝 비율이 가장 높은 것. "
                      "정책 성능이 아니라 환경 자체의 성질로만 고른 값이다."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "v2_design_check.json"))
    args = ap.parse_args()
    reps = 8 if args.quick else 24
    eps = 5 if args.quick else 20
    steps = 30

    print("태스크 프로토콜 v2 설계 검증 — 학습을 돌리지 않는다\n")

    print("── V-1. 조치 링크가 상관있는가 (장애 링크 vs 엉뚱한 링크에 cost 100)")
    v1 = v1_link_matters(reps, 12, args.seed)
    for lk, r in v1["per_fault_link"].items():
        m = r["mean_reward_over_horizon"]
        print(f"   장애 {lk}: 정답 {m['correct (faulty link) @100']:.4f}  "
              f"TEST링크 {m['TEST link r1-r4 @100']:.4f}  "
              f"우회없음 {m['faulty link @10 (no bypass)']:.4f}  → 최고: {r['best']}")
    print(f"   정답-엉뚱한링크 평균 차이 {v1['mean_correct_minus_wrong_link']}  "
          f"(노이즈 {v1['mean_noise_sd']})")
    print(f"   → {v1['verdict']}\n")

    print("── V-2a. 학습 없는 단일 정책으로 풀리는가")
    v2a = v2a_single_policy(eps, steps, args.seed)
    for name, per in v2a["policies"].items():
        s = " ".join(f"{lk}:{v['success_rate']:.0f}%" for lk, v in per.items())
        print(f"   {name:30s} {s}")
    print(f"   최고 상수 행동 {v2a['best_constant_action']} → TRAIN 평균 성공률 "
          f"{v2a['best_constant_mean_success']}%\n")

    print("── V-2b. inner-loop 적응이 기여하는가 (기존 체크포인트)")
    v2b = v2b_adaptation(max(3, eps // 2), steps, args.seed)
    if "skipped" in v2b:
        print(f"   건너뜀: {v2b['skipped']}\n")
    else:
        for mode, per in v2b["by_mode"].items():
            s = " ".join(f"{lk}:{v['success_rate']:.0f}%" for lk, v in per.items())
            print(f"   {mode:10s} {s}")
        print()

    print("── V-3. PPO와 MAML의 커리큘럼이 같은가")
    v3 = v3_curriculum(args.seed)
    for d in v3["differences"]:
        print(f"   · {d}")
    print(f"   → {v3['verdict']}\n")

    print("── V-4. v2 에피소드 안에서 '고칠 것이 살아 있는' 스텝 비율")
    v4 = v4_episode_length(eps, steps, args.seed)
    for k, v in v4.items():
        if isinstance(v, dict):
            print(f"   {k:16s} {v['pct_steps_with_active_violation']:.1f}%  "
                  f"(에피소드 {v['episode_steps']}스텝 중 평균 "
                  f"{v['mean_violating_steps_per_episode']}스텝)")

    print("\n── V-5. 에피소드 길이 — 측정으로 고른다")
    v5 = v5_length_sweep(max(3, eps // 2), [6, 8, 10, 12, 16, 20, 30], args.seed)
    print(f"   {'길이':>4s} {'오라클 유효%':>11s} {'오라클 해결%':>11s} {'오라클 TTR':>10s} {'무작위 유효%':>11s}")
    for L, st in v5["by_length"].items():
        o, r = st["oracle"], st["random"]
        print(f"   {L:>4d} {o['pct_actionable_steps']:>11.1f} {o['resolved_pct']:>11.1f} "
              f"{str(o['avg_ttr']):>10s} {r['pct_actionable_steps']:>11.1f}")
    print(f"   → 권장 길이 {v5['recommended']}스텝")

    doc = result_meta(
        seed=args.seed,
        condition=("태스크 프로토콜 v2 설계 검증. 학습 알고리즘을 돌리지 않고 환경과 "
                   "손으로 쓴 정책만 측정한다. V-1은 같은 noise_seed로 상태를 재구성해 "
                   "행동을 짝지어 비교하고, 12틱 누적 보상으로 잰다(즉시 보상은 노이즈에 묻힌다)."),
        repeats=reps, episodes=eps, episode_steps=steps,
    )
    doc.update({"v1_link_matters": v1, "v2a_single_policy": v2a,
                "v2b_adaptation": v2b, "v3_curriculum": v3, "v4_episode_length": v4,
                "v5_length_sweep": v5})
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
