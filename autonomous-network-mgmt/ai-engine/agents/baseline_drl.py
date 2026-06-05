"""
Baseline DRL 에이전트 — PPO (stable-baselines3).

학습:  python baseline_drl.py --train
추론:  from agents.baseline_drl import BaselineAgent
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env

from environment.network_env import NetworkEnv

MODEL_PATH = os.path.join(os.path.dirname(__file__), "ppo_network.zip")


class BaselineAgent:
    def __init__(self, model_path: str = MODEL_PATH):
        self._model = PPO.load(model_path) if os.path.exists(model_path) else None

    def predict(self, obs) -> int:
        if self._model is None:
            raise RuntimeError("모델이 학습되지 않았습니다. --train 먼저 실행하세요.")
        action, _ = self._model.predict(obs, deterministic=True)
        return int(action)

    def is_ready(self) -> bool:
        return self._model is not None


def train(
    total_timesteps: int = 50_000,
    snmp_url: str = "http://localhost:5001",
    train_links: list[str] | None = None,
):
    env = NetworkEnv(snmp_base_url=snmp_url, fast_mode=True, local_mode=True, train_links=train_links)
    check_env(env, warn=True)

    model = PPO(
        policy="MlpPolicy",
        env=env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        policy_kwargs={"net_arch": [128, 64]},
        verbose=1,
    )
    model.learn(total_timesteps=total_timesteps)
    model.save(MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")
    env.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--timesteps", type=int, default=50_000)
    parser.add_argument("--snmp-url", default="http://localhost:5001")
    args = parser.parse_args()

    if args.train:
        train(args.timesteps, args.snmp_url)
    else:
        parser.print_help()
