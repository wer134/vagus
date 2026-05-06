"""
비교 실험 스크립트.

실행 예:
  python run_experiment.py --all                         # 전체 파이프라인
  python run_experiment.py --train-baseline --timesteps 30000
  python run_experiment.py --train-fewshot  --meta-iterations 200
  python run_experiment.py --evaluate       --episodes 30
  python run_experiment.py --sample-efficiency           # 샘플 효율 비교
"""
import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Literal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai-engine"))

import numpy as np

from environment.network_env import NetworkEnv, LINKS
from agents.baseline_drl import BaselineAgent, train as train_baseline
from agents.few_shot_agent import FewShotAgent, train as train_fewshot
from reward import SLA_LATENCY_MS, SLA_PACKET_LOSS

SNMP_URL   = os.environ.get("SNMP_URL", "http://localhost:5001")
RESULT_DIR = os.path.join(os.path.dirname(__file__), "results")

# ── 태스크 분리: 학습/평가 링크 분리 ────────────────────────────────────────
TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]   # 학습에 사용
TEST_LINKS  = ["r3-r4", "r1-r4"]                       # 평가 전용 (미학습 링크)
ALL_LINKS   = LINKS

# 평가 시 혼잡 주입 스텝 (0 = 에피소드 시작 즉시)
ANOMALY_INJECT_STEP = 0


@dataclass
class EpisodeResult:
    agent:          str
    episode:        int
    test_link:      str
    ttr_steps:      float
    sla_violations: int
    total_reward:   float
    avg_latency:    float
    avg_bandwidth:  float


# ── 단일 에피소드 평가 ────────────────────────────────────────────────────────

def _run_episode(
    env: NetworkEnv,
    agent,
    agent_type: str,
    ep: int,
    max_steps: int,
    fixed_link: str | None = None,
) -> EpisodeResult:
    obs, _ = env.reset()
    total_reward    = 0.0
    sla_violations  = 0
    in_anomaly      = False
    anomaly_start   = 0
    ttr_list: list[int] = []
    latencies:  list[float] = []
    bandwidths: list[float] = []
    support_buffer: list[tuple] = []
    prev_obs = obs
    injected_link = fixed_link or "r1-r2"

    for step in range(max_steps):
        if step == ANOMALY_INJECT_STEP:
            env.inject_anomaly(injected_link)

        if agent_type == "fewshot" and len(support_buffer) >= 4:
            action = agent.adapt_and_predict(support_buffer[-32:], obs, adapt_steps=3)
        else:
            action = agent.predict(obs)

        obs, reward, _, truncated, info = env.step(action)
        total_reward += reward

        raw = env._fetch_raw_metrics()
        avg_lat  = float(np.mean([m["latency"]    for m in raw]))
        avg_bw   = float(np.mean([m["bandwidth"]  for m in raw]))
        max_lat  = max(m["latency"]    for m in raw)
        max_loss = max(m["packetLoss"] for m in raw)
        latencies.append(avg_lat)
        bandwidths.append(avg_bw)

        violated = max_lat > SLA_LATENCY_MS or max_loss > SLA_PACKET_LOSS
        if violated:
            sla_violations += 1
            if not in_anomaly:
                in_anomaly    = True
                anomaly_start = step
        else:
            if in_anomaly:
                ttr_list.append(step - anomaly_start)
                in_anomaly = False

        support_buffer.append((prev_obs, action, reward))
        if len(support_buffer) > 32:
            support_buffer.pop(0)
        prev_obs = obs

        if truncated:
            break

    if in_anomaly:
        ttr_list.append(max_steps - anomaly_start)

    avg_ttr = float(np.mean(ttr_list)) if ttr_list else 0.0

    r = EpisodeResult(
        agent=agent_type, episode=ep, test_link=injected_link,
        ttr_steps=round(avg_ttr, 1), sla_violations=sla_violations,
        total_reward=round(total_reward, 4),
        avg_latency=round(float(np.mean(latencies)), 2),
        avg_bandwidth=round(float(np.mean(bandwidths)), 2),
    )
    print(
        f"[{agent_type}|{injected_link}] ep={ep+1:02d}  "
        f"TTR={avg_ttr:5.1f}  SLA={sla_violations:3d}  reward={total_reward:.2f}"
    )
    return r


# ── 평가 루프 ─────────────────────────────────────────────────────────────────

def evaluate_agent(
    agent_type: Literal["baseline", "fewshot"],
    n_episodes: int = 30,
    max_steps:  int = 200,
    test_links: list[str] | None = None,
) -> list[EpisodeResult]:
    links = test_links or TEST_LINKS

    env = NetworkEnv(max_steps=max_steps, fast_mode=True,
                     inject_anomalies=False, local_mode=True)

    if agent_type == "baseline":
        agent = BaselineAgent()
        if not agent.is_ready():
            raise RuntimeError("Baseline PPO 모델 없음. --train-baseline 먼저.")
    else:
        agent = FewShotAgent()
        if not agent.is_ready():
            raise RuntimeError("MAML 모델 없음. --train-fewshot 먼저.")

    results = []
    for ep in range(n_episodes):
        link = links[ep % len(links)]
        results.append(_run_episode(env, agent, agent_type, ep, max_steps, link))

    env.close()
    return results


# ── 샘플 효율 실험 ─────────────────────────────────────────────────────────────

def sample_efficiency_experiment(
    ppo_steps_list: list[int] = [0, 2000, 5000, 10000, 20000, 30000],
    maml_iters_list: list[int] = [0, 20, 50, 100, 150, 200],
    eval_episodes: int = 20,
    max_steps: int = 200,
) -> dict:
    """
    학습 샘플 수에 따른 성능(평균 TTR) 변화 측정.
    PPO: ppo_steps_list 각 스텝 수로 학습 후 TEST_LINKS 평가
    MAML: maml_iters_list 각 반복 수로 학습 후 TEST_LINKS 평가
    """
    print("\n[샘플 효율 실험] PPO 학습 단계별 성능 측정...")
    ppo_curve = []
    for steps in ppo_steps_list:
        print(f"  PPO {steps} steps 학습 중...")
        if steps == 0:
            # 미학습: 랜덤 초기화 모델
            model_path = os.path.join(
                os.path.dirname(__file__), "..", "ai-engine", "agents", "ppo_scratch.zip"
            )
            _train_ppo_scratch(steps=1, save_path=model_path)
            agent = BaselineAgent(model_path)
        else:
            model_path = os.path.join(
                os.path.dirname(__file__), "..", "ai-engine", "agents", "ppo_eff_tmp.zip"
            )
            _train_ppo_scratch(steps=steps, save_path=model_path,
                               train_links=TRAIN_LINKS)
            agent = BaselineAgent(model_path)

        avg_ttr = _quick_eval(agent, "baseline", eval_episodes, max_steps)
        ppo_curve.append({"samples": steps, "avg_ttr": round(avg_ttr, 1)})
        print(f"    → avg_TTR={avg_ttr:.1f}")

    print("\n[샘플 효율 실험] MAML 학습 단계별 성능 측정...")
    maml_curve = []
    for iters in maml_iters_list:
        print(f"  MAML {iters} iterations 학습 중...")
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "ai-engine", "agents", "maml_eff_tmp.pt"
        )
        if iters == 0:
            _train_maml_scratch(meta_iterations=1, save_path=model_path,
                                train_links=TRAIN_LINKS)
        else:
            _train_maml_scratch(meta_iterations=iters, save_path=model_path,
                                train_links=TRAIN_LINKS)
        agent = FewShotAgent(model_path)
        # MAML 샘플 수: iters × tasks_per_iter(4) × adapt(3) × steps(20) × 2(support+query)
        samples = iters * 4 * 3 * 20 * 2
        avg_ttr = _quick_eval(agent, "fewshot", eval_episodes, max_steps)
        maml_curve.append({"samples": samples, "iterations": iters, "avg_ttr": round(avg_ttr, 1)})
        print(f"    → avg_TTR={avg_ttr:.1f} (총 {samples} 샘플)")

    return {"ppo": ppo_curve, "maml": maml_curve}


def _train_ppo_scratch(steps: int, save_path: str, train_links=None):
    from stable_baselines3 import PPO
    from stable_baselines3.common.env_checker import check_env
    env = NetworkEnv(fast_mode=True, local_mode=True, train_links=train_links)
    model = PPO("MlpPolicy", env, learning_rate=3e-4, n_steps=2048,
                batch_size=64, n_epochs=10, gamma=0.99,
                policy_kwargs={"net_arch": [128, 64]}, verbose=0)
    if steps > 0:
        model.learn(total_timesteps=steps)
    model.save(save_path)
    env.close()


def _train_maml_scratch(meta_iterations: int, save_path: str, train_links=None):
    train_fewshot(
        meta_iterations=meta_iterations,
        snmp_url=SNMP_URL,
        train_links=train_links,
    )
    # 기존 저장 경로에서 임시 경로로 복사
    import shutil
    src = os.path.join(os.path.dirname(__file__), "..", "ai-engine", "agents", "maml_network.pt")
    if os.path.exists(src):
        shutil.copy(src, save_path)


def _quick_eval(agent, agent_type: str, n_episodes: int, max_steps: int) -> float:
    env = NetworkEnv(max_steps=max_steps, fast_mode=True,
                     inject_anomalies=False, local_mode=True)
    ttr_list = []
    for ep in range(n_episodes):
        link = TEST_LINKS[ep % len(TEST_LINKS)]
        r = _run_episode(env, agent, agent_type, ep, max_steps, link)
        ttr_list.append(r.ttr_steps)
    env.close()
    return float(np.mean(ttr_list))


# ── 결과 저장 & 출력 ─────────────────────────────────────────────────────────

def save_results(results: list[EpisodeResult], filename: str):
    os.makedirs(RESULT_DIR, exist_ok=True)
    path = os.path.join(RESULT_DIR, filename)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=asdict(results[0]).keys())
        writer.writeheader()
        writer.writerows(asdict(r) for r in results)
    print(f"Saved → {path}")


def print_comparison(baseline: list[EpisodeResult], fewshot: list[EpisodeResult]):
    def stats(rs):
        return {
            "avg_ttr":    np.mean([r.ttr_steps      for r in rs]),
            "avg_sla":    np.mean([r.sla_violations  for r in rs]),
            "avg_reward": np.mean([r.total_reward    for r in rs]),
            "success_rate": np.mean([1 if r.ttr_steps < 30 else 0 for r in rs]) * 100,
        }
    b, f = stats(baseline), stats(fewshot)

    print("\n" + "=" * 66)
    print(f"{'지표':<24} {'Baseline (PPO)':>18} {'Few-shot (MAML)':>18}")
    print("-" * 66)
    print(f"{'평균 TTR (steps)':<24} {b['avg_ttr']:>18.1f} {f['avg_ttr']:>18.1f}")
    print(f"{'평균 SLA 위반 횟수':<24} {b['avg_sla']:>18.1f} {f['avg_sla']:>18.1f}")
    print(f"{'평균 보상':<24} {b['avg_reward']:>18.3f} {f['avg_reward']:>18.3f}")
    print(f"{'성공률 (TTR<30)':<24} {b['success_rate']:>17.1f}% {f['success_rate']:>17.1f}%")
    print("-" * 66)
    if b["avg_ttr"] > 0 and f["avg_ttr"] > 0:
        ratio = b["avg_ttr"] / max(f["avg_ttr"], 1e-6)
        if ratio > 1:
            print(f"MAML TTR 개선: {ratio:.1f}x  (목표 >10x)")
        else:
            print(f"PPO TTR 개선: {1/ratio:.1f}x  (MAML이 더 느림)")
    print("=" * 66)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-baseline",    action="store_true")
    parser.add_argument("--train-fewshot",     action="store_true")
    parser.add_argument("--evaluate",          action="store_true")
    parser.add_argument("--sample-efficiency", action="store_true")
    parser.add_argument("--all",               action="store_true")
    parser.add_argument("--timesteps",         type=int, default=30_000)
    parser.add_argument("--meta-iterations",   type=int, default=200)
    parser.add_argument("--episodes",          type=int, default=30)
    parser.add_argument("--snmp-url",          default=SNMP_URL)
    # 평가 링크: "train"=TRAIN_LINKS, "test"=TEST_LINKS(기본), "all"=전체
    parser.add_argument("--eval-links",        default="test",
                        choices=["train", "test", "all"])
    args = parser.parse_args()

    if args.all:
        args.train_baseline    = True
        args.train_fewshot     = True
        args.evaluate          = True
        args.sample_efficiency = True

    eval_link_map = {
        "train": TRAIN_LINKS,
        "test":  TEST_LINKS,
        "all":   ALL_LINKS,
    }
    eval_links = eval_link_map[args.eval_links]

    if args.train_baseline:
        print(f"\n[1/4] Training Baseline PPO ({args.timesteps} steps, "
              f"train_links={TRAIN_LINKS})...")
        train_baseline(total_timesteps=args.timesteps,
                       snmp_url=args.snmp_url,
                       train_links=TRAIN_LINKS)

    if args.train_fewshot:
        print(f"\n[2/4] Training MAML ({args.meta_iterations} iters, "
              f"train_links={TRAIN_LINKS})...")
        train_fewshot(meta_iterations=args.meta_iterations,
                      snmp_url=args.snmp_url,
                      train_links=TRAIN_LINKS)

    if args.evaluate:
        print(f"\n[3/4] Evaluating on {args.eval_links} links: {eval_links}  "
              f"({args.episodes} ep each)...")

        baseline_results = evaluate_agent("baseline", args.episodes,
                                          test_links=eval_links)
        save_results(baseline_results, "baseline_results.csv")

        fewshot_results = evaluate_agent("fewshot", args.episodes,
                                         test_links=eval_links)
        save_results(fewshot_results, "fewshot_results.csv")

        print_comparison(baseline_results, fewshot_results)

        def _stats(rs):
            ttrs = [r.ttr_steps for r in rs]
            return {
                "avg_ttr":      float(np.mean(ttrs)),
                "avg_sla":      float(np.mean([r.sla_violations for r in rs])),
                "avg_reward":   float(np.mean([r.total_reward   for r in rs])),
                "success_rate": float(np.mean([1 if t < 30 else 0 for t in ttrs])) * 100,
                "ttr_list":     ttrs,
            }

        summary = {"baseline": _stats(baseline_results),
                   "fewshot":  _stats(fewshot_results),
                   "eval_links": eval_links,
                   "train_links": TRAIN_LINKS}
        os.makedirs(RESULT_DIR, exist_ok=True)
        with open(os.path.join(RESULT_DIR, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)

    if args.sample_efficiency:
        print("\n[4/4] Sample Efficiency 실험...")
        eff_data = sample_efficiency_experiment()
        os.makedirs(RESULT_DIR, exist_ok=True)
        with open(os.path.join(RESULT_DIR, "sample_efficiency.json"), "w") as f:
            json.dump(eff_data, f, indent=2)
        print("\n[PPO 학습 곡선]")
        for p in eff_data["ppo"]:
            print(f"  {p['samples']:6d} samples → TTR {p['avg_ttr']:.1f}")
        print("[MAML 적응 곡선]")
        for m in eff_data["maml"]:
            print(f"  {m['samples']:6d} samples ({m['iterations']:3d} iter) → TTR {m['avg_ttr']:.1f}")


if __name__ == "__main__":
    main()
