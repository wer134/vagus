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


class ValueNet(nn.Module):
    """상태가치 baseline V(s) — 붕괴 원인 수정 1번 (cowork/ROADMAP.md §7).

    REINFORCE의 baseline이 **에피소드 보상 평균이라는 스칼라 하나**라는 것이 붕괴의 핵심
    기전이었다. 보상은 상태에 따라 0.681(정상)/0.323(혼잡)로 갈리는데 행동이 만드는 차이는
    최대 0.0134 — 28배다. 스칼라는 그 상태 오프셋을 제거하지 못하므로 advantage가 행동이
    아니라 상태를 23.8배 더 반영했고, 보상이 높은 정상 상태에서 **우연히 뽑힌 행동**이
    강화됐다. 그것이 상수 정책의 정체다.

    V(s)는 메타 파라미터가 아니다 — inner-loop로 적응시키지 않고 모든 태스크가 공유하는
    보통의 회귀 모델로 둔다. 태스크(링크)가 달라도 "이 관측이면 보상이 이쯤"이라는 관계는
    같기 때문이고, 측정(D5)이 보여준 것도 상태 조건부 baseline이면 충분하다는 것이었다.

    정책 체크포인트(PolicyNet)와 **별도 모듈**이라 기존 체크포인트 포맷·policy_check·
    FewShotAgent.act는 그대로다.
    """

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(OBS_DIM, 64)
        self.fc2 = nn.Linear(64, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(F.relu(self.fc1(x))).squeeze(-1)


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
    entropy_coef: float = 0.0,
    value_net: "ValueNet | None" = None,
) -> torch.Tensor:
    """REINFORCE + baseline: log-prob × advantage (분산 감소).

    entropy_coef > 0 : 정책 엔트로피 보너스 (붕괴 원인 수정 2번). 기존 손실에는 엔트로피 항이
        없었고 PPO도 `ent_coef` 기본값 0.0이었다 — 양쪽 다 조기 수렴을 막는 힘이 없었다.
    value_net 지정   : baseline을 스칼라 평균 대신 **V(s)**로 (수정 1번). 이때는 분산 정규화를
        하지 않는다. 정규화는 스칼라 baseline이 중심을 제대로 못 잡는 것을 보완하려던 장치인데,
        V(s)가 이미 상태별로 중심을 잡으므로 다시 정규화하면 스케일만 부풀린다. 특히 고칠 것이
        없어 보상이 노이즈뿐인 에피소드(전체의 40%)에서 그 노이즈를 단위 분산으로 **증폭**해
        신호가 있는 에피소드와 같은 크기의 gradient를 만든다 (ROADMAP §7 기전 5).

    기본값은 둘 다 꺼짐 — 인자를 주지 않으면 이전과 **같은 손실**이다.
    """
    if not transitions:
        return torch.tensor(0.0, requires_grad=True)

    rewards = [r for _, _, r in transitions]

    log_probs  = []
    entropies  = []
    for obs, action, r in transitions:
        t = torch.FloatTensor(obs).unsqueeze(0)
        logits = model.forward_with_params(t, params) if params is not None else model(t)
        logp_all = F.log_softmax(logits, dim=-1)
        log_probs.append(logp_all[0, action])
        if entropy_coef:
            entropies.append(-(logp_all.exp() * logp_all).sum())

    if value_net is not None:
        obs_batch = torch.FloatTensor(np.array([o for o, _, _ in transitions]))
        with torch.no_grad():
            values = value_net(obs_batch)
        adv_tensor = torch.tensor(rewards, dtype=torch.float32) - values
    else:
        baseline = float(np.mean(rewards))
        adv_tensor = torch.tensor([r - baseline for r in rewards], dtype=torch.float32)
        # 분산 정규화 (분산이 0이면 skip)
        if adv_tensor.std() > 1e-8:
            adv_tensor = (adv_tensor - adv_tensor.mean()) / (adv_tensor.std() + 1e-8)

    loss = -(torch.stack(log_probs) * adv_tensor).mean()
    if entropy_coef:
        loss = loss - entropy_coef * torch.stack(entropies).mean()
    return loss


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
    probe_every: int = 20,        # 0이면 학습 중 프로브 생략
    curve_path: str | None = None,
    # ── 붕괴 원인 수정 (cowork/ROADMAP.md §7). 기본값은 **이전 동작 그대로**다 —
    #    끄고 켜서 두 수정의 기여를 따로 잴 수 있게 둔다.
    entropy_coef: float = 0.0,    # >0: 엔트로피 보너스 (수정 2)
    value_baseline: bool = False, # True: baseline을 스칼라 평균 → V(s) (수정 1)
    value_lr: float = 1e-3,
    value_steps: int = 1,         # 반복당 V(s) 회귀 gradient 스텝 수
):
    if seed is not None:
        torch.manual_seed(seed)
        np.random.seed(seed)
        import random as _r; _r.seed(seed)
    # 학습 중 붕괴 곡선 (VISUALIZATION_PLAN T1). snapshot/restore로 감싸므로 프로브를
    # 켜도 같은 seed의 학습 결과는 바뀌지 않는다.
    probe = None
    if probe_every:
        try:
            from policy_check import TrainingProbe
            probe = TrainingProbe("maml", meta_iterations, probe_every, seed=seed or 0)
        except Exception as e:
            print(f"[probe] 비활성화: {type(e).__name__}: {e}", flush=True)

    model    = PolicyNet()
    meta_opt = torch.optim.Adam(model.parameters(), lr=meta_lr)
    value_net = ValueNet() if value_baseline else None
    value_opt = torch.optim.Adam(value_net.parameters(), lr=value_lr) if value_net else None
    value_quality: dict = {}
    print(f"[MAML] entropy_coef={entropy_coef}  value_baseline={value_baseline}"
          + (f"  value_steps={value_steps} lr={value_lr}" if value_baseline else ""), flush=True)
    env      = NetworkEnv(snmp_base_url=snmp_url, max_steps=50, fast_mode=True,
                          local_mode=True, train_links=train_links, sim_seed=seed)

    for iteration in range(meta_iterations):
        meta_opt.zero_grad()
        task_losses = []
        episode_buffer: list = []   # V(s) 회귀용 (obs, action, reward)

        for _ in range(tasks_per_iter):
            params = _named_params_copy(model)

            # inner-loop: adapt_steps번 적응
            for _ in range(adapt_steps):
                support    = _collect_episode(env, model, params, episode_steps)
                inner_loss = _reinforce_loss(model, support, params,
                                             entropy_coef, value_net)
                params     = _inner_update(model, params, inner_loss, fast_lr)
                episode_buffer.extend(support)

            # outer-loop: 적응 후 query set 평가
            query     = _collect_episode(env, model, params, episode_steps)
            task_loss = _reinforce_loss(model, query, params, entropy_coef, value_net)
            task_losses.append(task_loss)
            episode_buffer.extend(query)

        meta_loss = torch.stack(task_losses).mean()
        meta_loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        meta_opt.step()

        # V(s)를 이번 반복에서 모은 (관측, 보상)으로 회귀 학습한다. 정책 gradient와 분리돼
        # 있으므로 메타 파라미터에 영향을 주지 않는다.
        if value_net is not None and episode_buffer:
            ob = torch.FloatTensor(np.array([o for o, _, _ in episode_buffer]))
            rw = torch.FloatTensor([r for _, _, r in episode_buffer])
            for _ in range(value_steps):
                value_opt.zero_grad()
                v_loss = F.mse_loss(value_net(ob), rw)
                v_loss.backward()
                value_opt.step()
            # critic이 실제로 작동했는지를 **학습이 스스로 기록한다.** 첫 요인 실험에서는
            # critic을 저장하지도 측정하지도 않아서, 붕괴가 "수정 1이 틀려서"인지 "V(s)가
            # 거의 상수라 사실상 스칼라 baseline이어서"인지 사후에 구별할 수 없었다.
            # 보상과의 상관이 낮거나 예측 분산이 0에 가까우면 critic은 아무 일도 하지 않은 것이다.
            with torch.no_grad():
                pred = value_net(ob)
                sd_p, sd_r = pred.std().item(), rw.std().item()
                value_quality = {
                    "mse": round(float(v_loss.item()), 6),
                    "pred_sd": round(sd_p, 5),
                    "reward_sd": round(sd_r, 5),
                    "corr_with_reward": (
                        round(float(((pred - pred.mean()) * (rw - rw.mean())).mean()
                                    / (sd_p * sd_r + 1e-12)), 4)
                        if sd_p > 1e-8 and sd_r > 1e-8 else 0.0),
                }

        done = iteration + 1
        if probe is not None and (done % probe_every == 0 or done == 1):
            sm = probe.record(done, model.act)
            if sm:
                print(f"[MAML] iter {done:3d}/{meta_iterations}  "
                      f"meta_loss={meta_loss.item():.4f}  "
                      f"entropy={sm['action_entropy_bits']:.2f}bit "
                      f"top={sm['top_action']}({sm['top_action_share']:.2f})", flush=True)
                continue
        if done % 20 == 0:
            print(f"[MAML] iter {done:3d}/{meta_iterations}  meta_loss={meta_loss.item():.4f}")

    torch.save(model.state_dict(), save_path)
    print(f"MAML model saved → {save_path}")
    if value_net is not None:
        # critic을 버리면 "수정 1이 실제로 적용됐는가"를 나중에 확인할 수 없다 (2026-10-06 교훈)
        vpath = os.path.splitext(save_path)[0] + ".value.pt"
        torch.save(value_net.state_dict(), vpath)
        print(f"MAML critic saved → {vpath}  품질 {value_quality}", flush=True)
    env.close()

    if probe is not None and probe.samples:
        probe.save(
            curve_path or os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "..",
                "experiments", "results", "train_curve_maml.json")),
            train_info={"algo": "maml", "meta_iterations": meta_iterations,
                        "meta_lr": meta_lr, "fast_lr": fast_lr,
                        "train_links": train_links, "seed": seed, "rollout": "sampled",
                        "entropy_coef": entropy_coef, "value_baseline": value_baseline,
                        "value_steps": value_steps, "value_lr": value_lr,
                        "value_quality": value_quality},
        )

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
                "entropy_coef": entropy_coef, "value_baseline": value_baseline,
                "value_steps": value_steps, "value_lr": value_lr,
                "value_quality": value_quality,
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
    parser.add_argument("--probe-every",     type=int, default=20,
                        help="학습 중 정책 프로브 주기 (0=끔) — VISUALIZATION_PLAN T1")
    args = parser.parse_args()
    if args.train:
        train(meta_iterations=args.meta_iterations, snmp_url=args.snmp_url,
              seed=args.seed, save_path=args.save_path, probe_every=args.probe_every)
    else:
        parser.print_help()
