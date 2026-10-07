"""붕괴 원인 수정의 요인 실험 (cowork/ROADMAP.md §7 수정 1·2).

§7이 지목한 두 원인을 **따로 귀속할 수 있게** 2×2로 돌린다. 하나만 켜서 돌리지 않으면
"나아졌다"가 어느 수정 때문인지 말할 수 없다.

  수정 1  상태가치 baseline  — REINFORCE의 스칼라 평균 baseline을 V(s)로.
                              advantage가 행동이 아니라 상태를 23.8배 더 반영하던 원인.
  수정 2  엔트로피 정규화    — MAML 손실에 엔트로피 항, PPO에 ent_coef.
                              양쪽 다 0이라 조기 수렴을 막는 힘이 없었다.

PPO에는 수정 1을 적용하지 않는다 — **PPO는 이미 V(s)를 학습한다.** 기전이 맞다면 그것이
PPO는 붕괴에서 회복하고 MAML은 갇히는 이유이므로, 여기서 건드릴 것이 없다.

환경 계약(OBS/ACTION/REWARD/SIM_VERSION)은 그대로다 — 기존 결과와 직접 비교할 수 있다.

  python3 experiments/collapse_fix_study.py              # 전체 (약 45~60분)
  python3 experiments/collapse_fix_study.py --quick      # 짧게 (약 8분, 경향만)
  python3 experiments/collapse_fix_study.py --train-one maml_m3_both   # 내부용

엔트로피 계수는 0.01 — PPO 구현에서 널리 쓰이는 관례값이며, **결과를 보고 고르지 않았다.**
다른 값을 시도하면 전부 기록한다 (cowork/FIX_PLAN.md §0.2-1).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
AGENTS = os.path.join(PROJECT, "ai-engine", "agents")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT, "ai-engine"))

ENTROPY_COEF = 0.01

# 변종 정의 — (알고리즘, 체크포인트 이름, 학습 인자, 사람이 읽는 설명)
VARIANTS = {
    "maml_m0_base":  ("maml", dict(entropy_coef=0.0,          value_baseline=False),
                      "현행 재현 (스칼라 baseline, 엔트로피 없음)"),
    "maml_m1_ent":   ("maml", dict(entropy_coef=ENTROPY_COEF, value_baseline=False),
                      "수정 2만 — 엔트로피 정규화"),
    "maml_m2_vbase": ("maml", dict(entropy_coef=0.0,          value_baseline=True),
                      "수정 1만 — 상태가치 baseline"),
    "maml_m3_both":  ("maml", dict(entropy_coef=ENTROPY_COEF, value_baseline=True),
                      "수정 1+2"),
    "ppo_p0_base":   ("ppo",  dict(ent_coef=0.0),          "현행 재현 (ent_coef=0)"),
    "ppo_p1_ent":    ("ppo",  dict(ent_coef=ENTROPY_COEF), "수정 2 — ent_coef>0"),
    # ── 재실험 (2026-10-06) ───────────────────────────────────────────────────
    # 1차에서 V(s)를 켠 두 변종이 전부 붕괴했는데, critic을 meta-iteration당 gradient
    # 1스텝만 굴려서 V(s)가 거의 상수였다. 그러면 r - V(s)는 산술적으로 원래의 스칼라
    # baseline과 같아지므로, 그 두 칸은 수정 1을 테스트한 것이 아니다.
    #
    # value_steps=20은 critic_check.py가 "넉넉히 적합" **참고선**으로 쓴 값을 그대로
    # 가져온 것이다. 그 값은 정책 결과를 보기 전에 정해졌고, critic 자신의 회귀 과제
    # 기준으로만 고른 것이다 — 정책 성능이 좋아지는 값을 찾아 고른 것이 아니다.
    # 이번 학습은 critic의 품질(보상과의 상관·예측 분산)을 스스로 기록하므로,
    # 결과가 어느 쪽이든 "critic이 작동했는가"는 더 이상 추측거리가 아니다.
    # ── 재설계: 태스크 프로토콜 v2 위의 요인 실험 (2026-10-06) ───────────────
    # 1차 요인 실험은 **망가진 태스크 분포 위에서** 돌았다. 태스크의 41%에 장애가 아예
    # 없어 inner-loop가 적응할 대상이 없었고, 평가가 step 0에 지정 링크로 주입하는 것과도
    # 어긋나 있었다 (training_design_audit.py D-B/D-C). 그 위에서 baseline이나 엔트로피를
    # 바꿔 재는 것은 어느 쪽 결과가 나오든 아무것도 말하지 못한다.
    #
    # v2 = 태스크는 TRAIN 링크 하나, reset 직후 주입. 학습과 평가가 같은 프로토콜이 된다.
    # 학습 데이터 분포가 달라지므로 **v1 결과와 직접 비교할 수 없다.** 그래서 v2 안에서
    # 2×2를 새로 깔고, v1 대조군(maml_m0_base)을 비교 기준으로 남겨 둔다.
    #
    # critic은 value_steps=20 — critic_check.py가 정책 결과를 보기 전에 "넉넉히 적합"
    # 참고선으로 적어 둔 값이다. 1스텝에서는 V(s)가 사실상 상수여서 수정 1이 적용되지 않았다.
    # episode_steps=8은 v2_design_check.py V-5가 **측정으로** 고른 값이다. 길이 30에서는
    # 오라클 기준 유효 스텝이 13.9%뿐이고(고친 뒤가 빈다), 6에서는 오라클 해결률이 83%로
    # 떨어진다. 8은 해결률 100%를 유지하면서 유효 비율 51.0% — v1의 33.2%보다 높다.
    # "성공하면 종료"로 고치지 않은 이유: 스텝마다 보상이 양수라 일찍 끝낼수록 총 보상이
    # 줄어 **고치지 않는 쪽이 이득**이 된다. 길이를 줄이는 쪽은 그 병리를 만들지 않는다.
    "t2_maml_m0":    ("maml", dict(entropy_coef=0.0, value_baseline=False, task_protocol="v2",
                                   episode_steps=8),
                      "v2 기준선 — 두 수정 모두 끔 (태스크 정의만 바뀜)"),
    "t2_maml_ent":   ("maml", dict(entropy_coef=ENTROPY_COEF, value_baseline=False, task_protocol="v2",
                                   episode_steps=8),
                      "v2 + 수정 2 (엔트로피)"),
    "t2_maml_vbase": ("maml", dict(entropy_coef=0.0, value_baseline=True, value_steps=20,
                                   task_protocol="v2", episode_steps=8),
                      "v2 + 수정 1 (상태가치 baseline, critic 적합)"),
    "t2_maml_both":  ("maml", dict(entropy_coef=ENTROPY_COEF, value_baseline=True, value_steps=20,
                                   task_protocol="v2", episode_steps=8),
                      "v2 + 수정 1+2"),
    "t2_ppo_m0":     ("ppo",  dict(ent_coef=0.0, task_protocol="v2", episode_steps=8),
                      "v2 기준선 — PPO (커리큘럼만 바뀜)"),
    "t2_ppo_ent":    ("ppo",  dict(ent_coef=ENTROPY_COEF, task_protocol="v2", episode_steps=8),
                      "v2 + 수정 2 — PPO"),
}

# 재설계 실험에서 돌릴 변종 (--only 없이 --redesign으로 고를 수 있게)
REDESIGN = ["t2_maml_m0", "t2_maml_ent", "t2_maml_vbase", "t2_maml_both",
            "t2_ppo_m0", "t2_ppo_ent"]


def ckpt_path(name: str) -> str:
    algo = VARIANTS[name][0]
    return os.path.join(AGENTS, name + (".pt" if algo == "maml" else ".zip"))


def train_one(name: str, iters: int, steps: int, seed: int) -> None:
    """변종 하나를 학습한다 (서브프로세스로 호출됨 — 프로세스 상태 오염 방지)."""
    sys.path.insert(0, AGENTS)
    algo, kwargs, desc = VARIANTS[name]
    from run_experiment import TRAIN_LINKS
    print(f"\n=== {name} — {desc} ===", flush=True)
    if algo == "maml":
        from agents.few_shot_agent import train as train_maml
        train_maml(meta_iterations=iters, train_links=TRAIN_LINKS, seed=seed,
                   save_path=ckpt_path(name), probe_every=20,
                   curve_path=os.path.join(HERE, "results", f"train_curve_{name}.json"),
                   **kwargs)
    else:
        from agents.baseline_drl import train as train_ppo
        train_ppo(total_timesteps=steps, train_links=TRAIN_LINKS, seed=seed,
                  save_path=ckpt_path(name), probe_every=1,
                  curve_path=os.path.join(HERE, "results", f"train_curve_{name}.json"),
                  **kwargs)


def evaluate(name: str, episodes: int, seed: int) -> dict:
    """붕괴 검사 + 미학습(TEST) 링크 오프라인 평가."""
    sys.path.insert(0, AGENTS)
    from policy_check import policy_action_stats
    from run_experiment import evaluate_agent, TEST_LINKS

    algo = VARIANTS[name][0]
    path = ckpt_path(name)
    if not os.path.exists(path):
        return {"trained": False}

    if algo == "maml":
        from agents.few_shot_agent import FewShotAgent as A
    else:
        from agents.baseline_drl import BaselineAgent as A
    agent = A(path)
    if not agent.is_ready():
        return {"trained": True, "loaded": False, "error": str(agent.load_error)}

    stats = policy_action_stats(agent)
    eps = evaluate_agent("fewshot" if algo == "maml" else "baseline",
                         n_episodes=episodes, max_steps=200,
                         test_links=TEST_LINKS, model_path=path, sim_seed=seed)
    ttrs = [e.ttr_steps for e in eps]
    solved = [t for t in ttrs if t < 200]
    return {
        "trained": True, "loaded": True,
        "collapsed": stats["collapsed"],
        "top_action": stats["top_action"],
        "top_action_share": stats["top_action_share"],
        "action_entropy_bits": stats["action_entropy_bits"],
        "distinct_actions": stats["distinct_actions"],
        "avg_ttr": round(sum(ttrs) / len(ttrs), 2),
        "success_rate": round(100.0 * len(solved) / len(ttrs), 1),
        "n_episodes": len(ttrs),
        "avg_ttr_solved": round(sum(solved) / len(solved), 2) if solved else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-one")
    ap.add_argument("--only", help="쉼표로 구분한 변종 이름")
    ap.add_argument("--redesign", action="store_true",
                    help="태스크 프로토콜 v2 위의 재설계 요인 실험 6종")
    ap.add_argument("--no-train", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "results", "collapse_fix_study.json"))
    args = ap.parse_args()

    iters = 100 if args.quick else 500
    steps = 10_000 if args.quick else 50_000
    episodes = 10 if args.quick else 50

    if args.train_one:
        train_one(args.train_one, iters, steps, args.seed)
        return 0

    names = (REDESIGN if args.redesign else
             args.only.split(",") if args.only else list(VARIANTS))
    for n in names:
        if n not in VARIANTS:
            print(f"알 수 없는 변종: {n}. 가능: {list(VARIANTS)}", file=sys.stderr)
            return 2

    if not args.no_train:
        for n in names:
            # 학습은 변종마다 **새 프로세스**에서. 시뮬레이터가 모듈 전역이고 torch 시드도
            # 전역이라, 한 프로세스에서 연달아 학습하면 앞 변종이 뒤 변종에 영향을 준다.
            cmd = [sys.executable, os.path.abspath(__file__), "--train-one", n,
                   "--seed", str(args.seed)] + (["--quick"] if args.quick else [])
            r = subprocess.run(cmd, cwd=HERE)
            if r.returncode != 0:
                print(f"학습 실패: {n}", file=sys.stderr)
                return 1

    from _resultmeta import result_meta
    rows = {}
    for n in names:
        print(f"\n── 평가 {n}", flush=True)
        rows[n] = {"description": VARIANTS[n][2], "algo": VARIANTS[n][0],
                   "task_protocol": VARIANTS[n][1].get("task_protocol", "v1"),
                   "train_kwargs": VARIANTS[n][1], **evaluate(n, episodes, args.seed)}
        r = rows[n]
        if r.get("loaded"):
            print(f"   {'COLLAPSED' if r['collapsed'] else 'ok':9s} "
                  f"entropy={r['action_entropy_bits']}bit top={r['top_action']}"
                  f"({r['top_action_share']}) 행동 {r['distinct_actions']}종  "
                  f"TTR {r['avg_ttr']} 성공 {r['success_rate']}%")

    doc = result_meta(
        seed=args.seed,
        condition=(
            f"붕괴 원인 수정 요인 실험. MAML {iters} iter / PPO {steps} steps, "
            f"TRAIN 링크만 학습, TEST 링크({episodes}ep)로 오프라인 평가. "
            f"엔트로피 계수 {ENTROPY_COEF}(관례값, 결과를 보고 고르지 않음). "
            "환경 계약(OBS/ACTION/REWARD/SIM_VERSION)은 그대로다. 단 task_protocol=v2 변종은 "
            "**학습 데이터 분포가 다르므로** v1 변종과 직접 비교할 수 없다 — v2끼리, v1끼리 비교할 것."
        ),
        entropy_coef=ENTROPY_COEF,
        meta_iterations=iters, ppo_timesteps=steps, eval_episodes=episodes,
    )
    doc["variants"] = rows
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print(f"{'변종':16s} {'붕괴':9s} {'엔트로피':>9s} {'행동':>5s} {'TTR':>8s} {'성공률':>7s}")
    print("=" * 78)
    for n, r in rows.items():
        if not r.get("loaded"):
            print(f"{n:16s} (학습/로드 실패)")
            continue
        print(f"{n:16s} {'COLLAPSED' if r['collapsed'] else 'ok':9s} "
              f"{r['action_entropy_bits']:>9.2f} {r['distinct_actions']:>5d} "
              f"{r['avg_ttr']:>8.2f} {r['success_rate']:>6.1f}%")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
