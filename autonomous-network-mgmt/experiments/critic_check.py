"""V(s) critic이 실제로 학습됐는지 진단 (ROADMAP §7 수정 1의 사후 검증).

요인 실험에서 상태가치 baseline을 켠 MAML 변종이 전부 붕괴했다. 그런데 그 결과는 두 가지로
읽힐 수 있다:

  (A) 수정 1이 틀렸다 — 상태 오프셋을 제거해도 붕괴한다
  (B) 수정 1을 테스트하지 못했다 — V(s)가 덜 적합돼 거의 상수를 뱉었다면
      r - V(s)는 산술적으로 원래의 스칼라 baseline과 같아진다

둘을 구별하지 않으면 §7을 고쳐 쓸 수도, 유지할 수도 없다. **가설을 살리려는 튜닝이 아니라,
테스트 대상이 작동했는지 확인하는 것이다** — critic이 멀쩡했다면 (A)로 확정하고 §7을 다시 쓴다.

학습 코드가 critic을 버려서 사후 검사가 불가능하므로, **학습 때와 똑같은 조건**(동일 lr,
meta-iteration당 1스텝, 동일 데이터 분포)으로 재현해 그 궤적을 측정한다.

측정:
  1. 학습한 V(s)가 정상/혼잡 상태를 구분하는가 (두 상태의 예측값 차이)
  2. 그 차이가 실제 보상 차이(0.358)에 얼마나 근접하는가
  3. advantage의 상태 설명력(eta^2)이 실제로 떨어졌는가 — collapse_diagnosis D5와 같은 척도
  4. 참고: 넉넉히 학습시킨 V(s)는 어디까지 가는가 (도달 가능한 상한)

    python3 experiments/critic_check.py
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

import numpy as np
import torch
import torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT, "ai-engine"))
sys.path.insert(0, os.path.join(PROJECT, "ai-engine", "agents"))

from few_shot_agent import ValueNet          # noqa: E402
from environment.network_env import NetworkEnv  # noqa: E402
from _resultmeta import result_meta          # noqa: E402

TRAIN_LINKS = ["r1-r2", "r1-r3", "r2-r3", "r2-r4"]
EPISODE_STEPS = 30
N_ACTIONS = 30


def collect(n_iters: int, seed: int) -> list[tuple]:
    """학습이 critic에 먹이는 것과 같은 모양의 데이터.

    MAML은 meta-iteration마다 태스크 4개 × (inner 3 + query 1) 에피소드를 모아 그걸로 V(s)를
    한 스텝 갱신했다. 여기서는 같은 분포를 균일 무작위 정책으로 만든다 — 붕괴가 학습 초반에
    일어나므로 그 구간의 행동 분포가 관련 구간이다.
    """
    import random as _r
    _r.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    env = NetworkEnv(max_steps=50, fast_mode=True, local_mode=True,
                     train_links=TRAIN_LINKS, sim_seed=seed)
    rng = _r.Random(seed)
    per_iter = []
    for _ in range(n_iters):
        batch = []
        for _ in range(4 * 4):          # 태스크 4개 × (inner 3 + query 1)
            env.reset()
            for _ in range(EPISODE_STEPS):
                a = rng.randrange(N_ACTIONS)
                obs_prev = env._get_obs()
                _, r, _, trunc, info = env.step(a)
                batch.append((obs_prev, bool(info["anomalies"]), float(r)))
                if trunc:
                    break
        per_iter.append(batch)
    env.close()
    return per_iter


def train_critic(per_iter: list, lr: float, steps_per_iter: int) -> tuple[ValueNet, list]:
    """학습 때와 같은 방식으로 V(s)를 굴린다. steps_per_iter=1이 실제 학습 설정."""
    torch.manual_seed(0)
    net = ValueNet()
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    trace = []
    for i, batch in enumerate(per_iter, 1):
        ob = torch.FloatTensor(np.array([o for o, _, _ in batch]))
        rw = torch.FloatTensor([r for _, _, r in batch])
        for _ in range(steps_per_iter):
            opt.zero_grad()
            loss = F.mse_loss(net(ob), rw)
            loss.backward()
            opt.step()
        if i % max(1, len(per_iter) // 25) == 0 or i == 1:
            trace.append({"iter": i, "mse": round(float(loss.item()), 6),
                          **separation(net, batch)})
    return net, trace


def separation(net: ValueNet, batch: list) -> dict:
    """V(s)가 정상/혼잡을 얼마나 벌리는가. 실제 보상 차이와 비교한다."""
    with torch.no_grad():
        ob = torch.FloatTensor(np.array([o for o, _, _ in batch]))
        pred = net(ob).numpy()
    healthy = [float(p) for p, (_, a, _) in zip(pred, batch) if not a]
    anom = [float(p) for p, (_, a, _) in zip(pred, batch) if a]
    r_healthy = [r for _, a, r in batch if not a]
    r_anom = [r for _, a, r in batch if a]
    if not healthy or not anom:
        return {"v_gap": None, "reward_gap": None, "gap_ratio": None, "v_sd": None}
    v_gap = statistics.mean(healthy) - statistics.mean(anom)
    r_gap = statistics.mean(r_healthy) - statistics.mean(r_anom)
    return {
        "v_gap": round(v_gap, 5),
        "reward_gap": round(r_gap, 5),
        "gap_ratio": round(v_gap / r_gap, 4) if abs(r_gap) > 1e-9 else None,
        "v_sd": round(statistics.pstdev([float(p) for p in pred]), 5),
    }


def eta2_state(net: ValueNet | None, batch: list) -> float:
    """advantage 분산 중 '이상 유무'가 설명하는 비율 — D5와 같은 척도."""
    if net is None:
        base = statistics.mean(r for _, _, r in batch)
        adv = [(a, r - base) for _, a, r in batch]
    else:
        with torch.no_grad():
            ob = torch.FloatTensor(np.array([o for o, _, _ in batch]))
            v = net(ob).numpy()
        adv = [(a, r - float(vi)) for (_, a, r), vi in zip(batch, v)]
    vals = [x for _, x in adv]
    gm = statistics.mean(vals)
    ss_total = sum((x - gm) ** 2 for x in vals)
    groups = {True: [x for a, x in adv if a], False: [x for a, x in adv if not a]}
    ss_between = sum(len(g) * (statistics.mean(g) - gm) ** 2 for g in groups.values() if g)
    return ss_between / ss_total if ss_total > 1e-12 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=500, help="학습과 같은 meta-iteration 수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(HERE, "results", "critic_check.json"))
    args = ap.parse_args()

    print(f"학습과 동일 조건 재현 중 (meta-iteration {args.iters}, 반복당 1스텝, lr 1e-3)…\n",
          flush=True)
    per_iter = collect(args.iters, args.seed)
    eval_batch = per_iter[-1]

    as_trained, trace = train_critic(per_iter, lr=1e-3, steps_per_iter=1)
    # 참고선: 같은 데이터를 넉넉히 돌렸을 때 V(s)가 도달할 수 있는 수준
    well_fit, _ = train_critic(per_iter, lr=1e-3, steps_per_iter=20)

    rows = {
        "as_trained": {"steps_per_iter": 1, **separation(as_trained, eval_batch)},
        "well_fit_reference": {"steps_per_iter": 20, **separation(well_fit, eval_batch)},
    }
    e_scalar = eta2_state(None, eval_batch)
    e_trained = eta2_state(as_trained, eval_batch)
    e_well = eta2_state(well_fit, eval_batch)

    for k, v in rows.items():
        print(f"  {k:20s} V(s) 정상-혼잡 차이 {v['v_gap']}  "
              f"(실제 보상 차이 {v['reward_gap']}, 비율 {v['gap_ratio']})")
    print(f"\n  advantage의 상태 설명력 (eta^2)  ※ 이 표 안에서만 비교할 것 —"
          f" collapse_diagnosis D5(0.2179)는 에피소드별 정규화를 거친 값이라 척도가 다르다")
    print(f"    스칼라 baseline (원래)     {e_scalar:.5f}")
    print(f"    V(s) 학습된 그대로         {e_trained:.5f}")
    print(f"    V(s) 넉넉히 적합 (참고)    {e_well:.5f}")

    verdict = ("critic이 상태를 거의 구분하지 못했다 — 요인 실험은 수정 1을 테스트하지 못했다"
               if (rows["as_trained"]["gap_ratio"] or 0) < 0.5 else
               "critic이 상태를 구분했다 — 수정 1은 적용됐고, 그래도 붕괴했다")
    print(f"\n  판정: {verdict}")

    doc = result_meta(
        seed=args.seed,
        condition=(
            f"학습 코드가 critic을 저장하지 않아 사후 검사가 불가능하므로, 학습과 동일 조건"
            f"(meta-iteration {args.iters}, 반복당 gradient 1스텝, lr 1e-3, 같은 데이터 분포)으로 "
            "재현해 측정했다. 균일 무작위 정책 — 붕괴가 학습 초반에 일어나므로 그 구간의 분포다. "
            "'넉넉히 적합'은 도달 가능한 상한을 보기 위한 참고선이며 학습에 쓴 설정이 아니다."
        ),
        iters=args.iters,
        eta2_note=("이 eta^2는 에피소드 정규화 없이 묶음 전체에서 계산한 값이라 "
                   "collapse_diagnosis.json의 d5(에피소드별 정규화 후)와 직접 비교할 수 없다. "
                   "여기서는 스칼라 baseline / 학습된 critic / 넉넉히 적합한 critic 셋을 "
                   "서로 비교하는 용도다."),
    )
    doc.update({
        "separation": rows,
        "eta2_state_scalar_baseline": round(e_scalar, 5),
        "eta2_state_critic_as_trained": round(e_trained, 5),
        "eta2_state_critic_well_fit": round(e_well, 5),
        "trace_as_trained": trace,
        "verdict": verdict,
    })
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
