"""
Few-shot 에이전트 — MAML (learn2learn 없이 순수 PyTorch 구현).

학습:  python few_shot_agent.py --train
추론:  from agents.few_shot_agent import FewShotAgent
"""
import argparse
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from environment.network_env import NetworkEnv, N_LINKS, N_NODES, OSPF_COSTS

MODEL_PATH = os.path.join(os.path.dirname(__file__), "maml_network.pt")

OBS_DIM    = 2 * N_NODES + N_LINKS   # 8 + 6 = 14  (수정: 이전 12 → 14)
ACTION_DIM = N_LINKS * len(OSPF_COSTS)  # 6 * 5 = 30


# ── 정책 네트워크 ─────────────────────────────────────────────────────────────

class PolicyNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(OBS_DIM, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, ACTION_DIM)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return self.fc3(x)

    def act(self, obs: np.ndarray) -> int:
        with torch.no_grad():
            t = torch.FloatTensor(obs).unsqueeze(0)
            logits = self.forward(t)
            return int(torch.argmax(logits, dim=-1).item())

    def forward_with_params(self, x: torch.Tensor, params: dict) -> torch.Tensor:
        """지정된 파라미터로 순전파 (inner-loop 적응용)."""
        x = F.relu(F.linear(x, params["fc1.weight"], params["fc1.bias"]))
        x = F.relu(F.linear(x, params["fc2.weight"], params["fc2.bias"]))
        return F.linear(x, params["fc3.weight"], params["fc3.bias"])


# ── MAML 헬퍼 ─────────────────────────────────────────────────────────────────

def _named_params_copy(model: nn.Module) -> dict:
    return {k: v.clone() for k, v in model.named_parameters()}


def _inner_update(model: "PolicyNet", params: dict, loss: torch.Tensor, lr: float) -> dict:
    """단일 gradient step으로 파라미터 업데이트."""
    grads = torch.autograd.grad(loss, params.values(), create_graph=True, allow_unused=True)
    return {
        k: v - lr * (g if g is not None else torch.zeros_like(v))
        for (k, v), g in zip(params.items(), grads)
    }


def _collect_episode(
    env: NetworkEnv, model: "PolicyNet", params: dict | None, steps: int = 20,
    sample: bool = True,
):
    """에피소드 롤아웃.

    sample=True (학습 기본값): 정책 분포에서 행동을 샘플링한다. REINFORCE의 gradient 추정은
    행동이 π(a|s)에서 뽑혔을 때만 유효하다 — 이전 구현은 argmax(결정론)로만 골라 같은 상태에서
    늘 같은 행동을 보았고, advantage가 행동의 좋고 나쁨이 아니라 상태 차이를 반영했다
    (cowork/AUDIT_2026-09-09.md P5). 추론(FewShotAgent.act/predict)은 여전히 argmax.
    """
    obs, _ = env.reset()
    transitions = []
    for _ in range(steps):
        with torch.no_grad():
            t = torch.FloatTensor(obs).unsqueeze(0)
            logits = model.forward_with_params(t, params) if params is not None else model(t)
            if sample:
                action = int(torch.distributions.Categorical(logits=logits).sample().item())
            else:
                action = int(torch.argmax(logits, dim=-1).item())
        obs_next, reward, terminated, truncated, _ = env.step(action)
        transitions.append((obs, action, float(reward)))
        obs = obs_next
        if terminated or truncated:
            break
    return transitions


def _reinforce_loss(
    model: "PolicyNet",
    transitions: list,
    params: dict | None,
) -> torch.Tensor:
    """REINFORCE + baseline: log-prob × advantage (분산 감소)."""
    if not transitions:
        return torch.tensor(0.0, requires_grad=True)

    rewards = [r for _, _, r in transitions]
    baseline = float(np.mean(rewards))

    log_probs  = []
    advantages = []
    for obs, action, r in transitions:
        t = torch.FloatTensor(obs).unsqueeze(0)
        logits = model.forward_with_params(t, params) if params is not None else model(t)
        log_prob = F.log_softmax(logits, dim=-1)[0, action]
        log_probs.append(log_prob)
        advantages.append(r - baseline)

    adv_tensor  = torch.tensor(advantages, dtype=torch.float32)
    # 분산 정규화 (분산이 0이면 skip)
    if adv_tensor.std() > 1e-8:
        adv_tensor = (adv_tensor - adv_tensor.mean()) / (adv_tensor.std() + 1e-8)

    log_prob_stack = torch.stack(log_probs)
    return -(log_prob_stack * adv_tensor).mean()


# ── 학습 ─────────────────────────────────────────────────────────────────────

def train(
    meta_lr: float = 3e-4,
    fast_lr: float = 0.02,      # 0.05 과적응 확인 → 0.02로 조정
    meta_iterations: int = 500,  # 200 → 500 유지
    tasks_per_iter: int = 4,
    adapt_steps: int = 3,        # 5 → 3: 안정적 inner-loop
    episode_steps: int = 30,     # 30 유지
    snmp_url: str = "http://localhost:5001",
    train_links: list[str] | None = None,
    save_path: str = MODEL_PATH,  # 실험용 임시 학습은 운영 체크포인트를 덮어쓰지 않도록 별도 경로 지정
    seed: int | None = None,
):
    if seed is not None:
        torch.manual_seed(seed)
        np.random.seed(seed)
        import random as _r; _r.seed(seed)
    model    = PolicyNet()
    meta_opt = torch.optim.Adam(model.parameters(), lr=meta_lr)
    env      = NetworkEnv(snmp_base_url=snmp_url, max_steps=50, fast_mode=True, local_mode=True, train_links=train_links)

    for iteration in range(meta_iterations):
        meta_opt.zero_grad()
        task_losses = []

        for _ in range(tasks_per_iter):
            params = _named_params_copy(model)

            # inner-loop: adapt_steps번 적응
            for _ in range(adapt_steps):
                support    = _collect_episode(env, model, params, episode_steps)
                inner_loss = _reinforce_loss(model, support, params)
                params     = _inner_update(model, params, inner_loss, fast_lr)

            # outer-loop: 적응 후 query set 평가
            query     = _collect_episode(env, model, params, episode_steps)
            task_loss = _reinforce_loss(model, query, params)
            task_losses.append(task_loss)

        meta_loss = torch.stack(task_losses).mean()
        meta_loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        meta_opt.step()

        if (iteration + 1) % 20 == 0:
            print(f"[MAML] iter {iteration+1:3d}/{meta_iterations}  meta_loss={meta_loss.item():.4f}")

    torch.save(model.state_dict(), save_path)
    print(f"MAML model saved → {save_path}")
    env.close()

    # ROADMAP A-5: 학습 직후 정책 붕괴 검사 → <name>.meta.json
    try:
        from policy_check import write_checkpoint_meta
        write_checkpoint_meta(
            FewShotAgent(save_path), save_path,
            train_info={
                "algo": "maml", "meta_iterations": meta_iterations, "meta_lr": meta_lr,
                "fast_lr": fast_lr, "tasks_per_iter": tasks_per_iter,
                "adapt_steps": adapt_steps, "episode_steps": episode_steps,
                "train_links": train_links, "seed": seed, "rollout": "sampled",
            },
        )
    except Exception as e:  # 검사 실패가 학습을 무효화하지는 않는다
        print(f"[policy-check] 건너뜀: {type(e).__name__}: {e}", flush=True)


# ── 추론 클래스 ───────────────────────────────────────────────────────────────

class FewShotAgent:
    def __init__(self, model_path: str = MODEL_PATH):
        self._model = PolicyNet()
        self._ready = False
        self.load_error: str | None = None
        if os.path.exists(model_path):
            try:
                self._model.load_state_dict(
                    torch.load(model_path, weights_only=True, map_location="cpu")
                )
                self._ready = True
            except Exception as e:
                self.load_error = f"{type(e).__name__}: {e}"
                print(f"[FewShotAgent] 체크포인트 로드 실패 ({model_path}): {self.load_error}", flush=True)

    def adapt_and_predict(
        self,
        support_transitions: list,
        obs: np.ndarray,
        fast_lr: float = 0.02,   # 학습 fast_lr과 통일
        adapt_steps: int = 2,    # 안정적 추론 적응
    ) -> int:
        """소수 샘플로 inner-loop 적응 후 행동 반환."""
        if not self._ready:
            raise RuntimeError("MAML 모델이 학습되지 않았습니다.")

        params = _named_params_copy(self._model)
        for _ in range(adapt_steps):
            loss   = _reinforce_loss(self._model, support_transitions, params)
            params = _inner_update(self._model, params, loss, fast_lr)

        with torch.no_grad():
            t      = torch.FloatTensor(obs).unsqueeze(0)
            logits = self._model.forward_with_params(t, params)
            return int(torch.argmax(logits, dim=-1).item())

    def predict(self, obs: np.ndarray) -> int:
        if not self._ready:
            raise RuntimeError("MAML 모델이 학습되지 않았습니다.")
        return self._model.act(obs)

    def is_ready(self) -> bool:
        return self._ready


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--train",           action="store_true")
    parser.add_argument("--meta-iterations", type=int, default=200)
    parser.add_argument("--snmp-url",        default="http://localhost:5001")
    parser.add_argument("--seed",            type=int, default=None)
    parser.add_argument("--save-path",       default=MODEL_PATH)
    args = parser.parse_args()
    if args.train:
        train(meta_iterations=args.meta_iterations, snmp_url=args.snmp_url,
              seed=args.seed, save_path=args.save_path)
    else:
        parser.print_help()
