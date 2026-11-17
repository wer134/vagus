"""
규모 확장 실험의 학습 정책 — PPO on Abilene (cowork/ROADMAP.md §6 (가), A-7)
---------------------------------------------------------------------------
측정 전에 고정한 설정 (결과를 보고 바꾸지 않는다):
  환경        : ai-engine/environment/scale_env.ScaleEnv("abilene", "train") — TRAIN 분할 시나리오만
  관측·행동·보상 : 관측 64차원(링크 이용률·손실 포함), 행동 71개(무행동 포함), 보상 v2
  알고리즘    : stable-baselines3 PPO, MlpPolicy, 정책·가치망 [128, 128]
                ent_coef 0.01 (K4에서 정책이 상수 행동으로 붕괴한 전례), 나머지는 SB3 기본값
  예산        : seed당 300,000 스텝 (= 10,000 에피소드)
  seed        : 0, 1, 2, 3, 4 — 보고는 5개 평균 ± 표준편차 (ROADMAP A-7: 단일 seed 수치 금지)

학습이 끝나면 TRAIN 시나리오 200 에피소드에서 결정적 정책의 행동 분포를 재 붕괴 여부를 기록한다
(ROADMAP A-5). 체크포인트 옆 <이름>.meta.json에 설정·커밋·학습 곡선·붕괴 검사를 남긴다.

실행:
  python3 experiments/train_scale_ppo.py --seed 0
"""
import json
import math
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ai-engine" / "environment"))

from _resultmeta import result_meta  # noqa: E402
import scale_env as se  # noqa: E402

OUT_DIR = ROOT / "ai-engine" / "agents" / "scale"
TIMESTEPS = 300_000
CONFIG = {
    "policy": "MlpPolicy",
    "net_arch": {"pi": [128, 128], "vf": [128, 128]},
    "ent_coef": 0.01,
    "learning_rate": 3e-4, "n_steps": 2048, "batch_size": 64, "n_epochs": 10,
    "gamma": 0.99, "gae_lambda": 0.95, "clip_range": 0.2,
}


def collapse_check(model, n_episodes: int = 200, seed: int = 999) -> dict:
    env = se.ScaleEnv("abilene", "train", seed=seed)
    acts = Counter()
    for _ in range(n_episodes):
        obs, _ = env.reset()
        while True:
            a, _ = model.predict(obs, deterministic=True)
            acts[int(a)] += 1
            obs, _, _, trunc, _ = env.step(int(a))
            if trunc:
                break
    total = sum(acts.values())
    p = [c / total for c in acts.values()]
    noop = env.action_space.n - 1
    top_a, top_c = acts.most_common(1)[0]
    return {
        "distinct_actions": len(acts),
        "top_action": int(top_a),
        "top_action_share": round(top_c / total, 4),
        "noop_share": round(acts.get(noop, 0) / total, 4),
        "entropy_bits": round(-sum(x * math.log2(x) for x in p), 3),
        "collapsed": top_c / total > 0.8 and top_a != noop,
    }


def train(seed: int, timesteps: int) -> None:
    import numpy as np
    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.callbacks import BaseCallback

    torch.set_num_threads(1)
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    env = Monitor(se.ScaleEnv("abilene", "train", seed=seed))

    curve: list[dict] = []

    class Curve(BaseCallback):
        def _on_step(self) -> bool:
            return True

        def _on_rollout_end(self) -> None:
            eps = list(self.model.ep_info_buffer)[-50:]
            if eps:
                curve.append({"t": int(self.num_timesteps),
                              "ep_return_mean_50": round(float(np.mean([e["r"] for e in eps])), 4)})

    cfg = dict(CONFIG)
    policy = cfg.pop("policy"); net_arch = cfg.pop("net_arch")
    model = PPO(policy, env, policy_kwargs={"net_arch": net_arch}, seed=seed, device="cpu", verbose=0, **cfg)
    t0 = time.time()
    model.learn(total_timesteps=timesteps, callback=Curve())
    elapsed = time.time() - t0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ppo_abilene_s{seed}"
    model.save(OUT_DIR / name)
    cc = collapse_check(model)
    meta = {
        **result_meta(seed=seed, condition="PPO 학습, scale_env TRAIN 분할, Abilene"),
        "config": CONFIG, "timesteps": timesteps, "elapsed_s": round(elapsed, 1),
        "reward_version": se.REWARD_VERSION, "obs_dim": int(env.observation_space.shape[0]),
        "n_actions": int(env.action_space.n),
        "learning_curve": curve, "collapse_check": cc,
    }
    with open(OUT_DIR / f"{name}.meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    last = curve[-1]["ep_return_mean_50"] if curve else None
    print(f"seed {seed}: {elapsed:.0f}s, 마지막 평균 에피소드 보상 {last}, 붕괴 검사 {cc}", flush=True)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--timesteps", type=int, default=TIMESTEPS)
    a = p.parse_args()
    train(a.seed, a.timesteps)
