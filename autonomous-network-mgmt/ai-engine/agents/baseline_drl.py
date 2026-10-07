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
        self._model = None
        self.load_error: str | None = None
        if os.path.exists(model_path):
            try:
                self._model = PPO.load(model_path)
            except Exception as e:  # 버전 불일치(numpy/sb3/cloudpickle) 등
                # 베이스라인 체크포인트를 못 읽어도 AI 엔진 전체가 죽으면 안 된다 —
                # 폐쇄 루프는 MAML만 필요하다. is_ready()=False로 보고하고 원인을 남긴다.
                self.load_error = f"{type(e).__name__}: {e}"
                print(f"[BaselineAgent] 체크포인트 로드 실패 ({model_path}): {self.load_error}", flush=True)

    def predict(self, obs) -> int:
        if self._model is None:
            raise RuntimeError("모델이 학습되지 않았습니다. --train 먼저 실행하세요.")
        action, _ = self._model.predict(obs, deterministic=True)
        return int(action)

    def is_ready(self) -> bool:
        return self._model is not None


def _make_probe_callback(probe, model_ref):
    """rollout이 끝날 때마다 정책 행동 분포를 잰다 (VISUALIZATION_PLAN T1).

    rollout 경계에서만 재고 metric_generator를 snapshot/restore로 감싸므로 학습 롤아웃을
    건드리지 않는다.
    """
    from stable_baselines3.common.callbacks import BaseCallback

    class ProbeCallback(BaseCallback):
        def _on_step(self) -> bool:      # 매 스텝 호출 — 아무것도 하지 않는다
            return True

        def _on_rollout_end(self) -> None:
            def predict(obs):
                action, _ = model_ref[0].predict(obs, deterministic=True)
                return int(action)
            sm = probe.record(int(self.num_timesteps), predict)
            if sm:
                print(f"[PPO] {self.num_timesteps:6d} steps  "
                      f"entropy={sm['action_entropy_bits']:.2f}bit "
                      f"top={sm['top_action']}({sm['top_action_share']:.2f})", flush=True)

    return ProbeCallback()


def train(
    total_timesteps: int = 50_000,
    snmp_url: str = "http://localhost:5001",
    train_links: list[str] | None = None,
    save_path: str = MODEL_PATH,
    seed: int | None = None,
    probe_every: int = 1,          # rollout 몇 번마다 잴지 (0=끔)
    curve_path: str | None = None,
    # 붕괴 원인 수정 2 (cowork/ROADMAP.md §7): SB3의 ent_coef 기본값은 0.0이라 엔트로피
    # 보너스가 전혀 없었다 — 조기 수렴을 막는 힘이 없는 상태로 학습해 온 것이다. 여기 기본값도
    # 이전 동작 그대로 두고, 켜고 끄며 기여를 따로 잰다.
    # (수정 1(상태가치 baseline)은 PPO에 해당 없음 — PPO는 이미 V(s)를 학습한다. 기전이
    #  맞다면 그것이 PPO는 붕괴에서 회복하고 MAML은 갇히는 이유다.)
    ent_coef: float = 0.0,
    # 태스크 프로토콜 (experiments/training_design_audit.py D-B).
    # PPO에는 MAML 같은 태스크 구조가 없지만, **커리큘럼은 같아야 비교가 성립한다** —
    # v2는 에피소드마다 TRAIN 링크 하나에 장애를 주입해 평가 프로토콜과 맞춘다.
    task_protocol: str = "v1",
    # v2에서 에피소드 길이. MAML의 episode_steps와 같아야 커리큘럼이 같다 — PPO는 그동안
    # NetworkEnv.max_steps(200)를 썼고 MAML은 30스텝을 모았다 (v2_design_check.py V-3).
    episode_steps: int = 8,
):
    if task_protocol not in ("v1", "v2"):
        raise ValueError(f"task_protocol은 'v1' 또는 'v2' (받은 값: {task_protocol})")
    env_kw = {}
    if task_protocol == "v2":
        env_kw["max_steps"] = episode_steps      # 에피소드 길이를 MAML과 일치
    env = NetworkEnv(snmp_base_url=snmp_url, fast_mode=True, local_mode=True,
                     train_links=train_links, sim_seed=seed,
                     inject_anomalies=(task_protocol == "v1"),
                     inject_on_reset=(task_protocol == "v2"), **env_kw)
    print(f"[PPO] task_protocol={task_protocol}"
          + (f" episode_steps={episode_steps}" if task_protocol == "v2" else ""), flush=True)
    check_env(env, warn=True)

    model = PPO(
        policy="MlpPolicy",
        env=env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        ent_coef=ent_coef,
        policy_kwargs={"net_arch": [128, 64]},
        verbose=1,
        seed=seed,
    )
    print(f"[PPO] ent_coef={ent_coef}", flush=True)
    probe, callback = None, None
    if probe_every:
        try:
            from policy_check import TrainingProbe
            probe = TrainingProbe("ppo", total_timesteps, 2048 * probe_every, seed=seed or 0)
            callback = _make_probe_callback(probe, [model])
        except Exception as e:
            print(f"[probe] 비활성화: {type(e).__name__}: {e}", flush=True)

    model.learn(total_timesteps=total_timesteps, callback=callback)

    if probe is not None and probe.samples:
        probe.save(
            curve_path or os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "..",
                "experiments", "results", "train_curve_ppo.json")),
            train_info={"algo": "ppo", "total_timesteps": total_timesteps,
                        "train_links": train_links, "seed": seed,
                        "ent_coef": ent_coef, "task_protocol": task_protocol,
                        "episode_steps": episode_steps},
        )

    model.save(save_path)
    print(f"Model saved to {save_path}")
    env.close()

    # ROADMAP A-5: 학습 직후 정책 붕괴 검사 → <name>.meta.json
    try:
        from policy_check import write_checkpoint_meta
        write_checkpoint_meta(
            BaselineAgent(save_path), save_path,
            train_info={"algo": "ppo", "total_timesteps": total_timesteps,
                        "train_links": train_links, "seed": seed,
                        "net_arch": [128, 64], "learning_rate": 3e-4,
                        "ent_coef": ent_coef, "task_protocol": task_protocol,
                        "episode_steps": episode_steps},
        )
    except Exception as e:
        print(f"[policy-check] 건너뜀: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--timesteps", type=int, default=50_000)
    parser.add_argument("--snmp-url", default="http://localhost:5001")
    parser.add_argument("--seed",      type=int, default=None)
    parser.add_argument("--save-path", default=MODEL_PATH)
    parser.add_argument("--probe-every", type=int, default=1,
                        help="rollout 몇 번마다 정책 프로브 (0=끔) — VISUALIZATION_PLAN T1")
    args = parser.parse_args()

    if args.train:
        train(args.timesteps, args.snmp_url, save_path=args.save_path, seed=args.seed,
              probe_every=args.probe_every)
    else:
        parser.print_help()
