"""정책 붕괴 검사 (cowork/ROADMAP.md A-5).

2026-09-09 감사에서 PPO·MAML 체크포인트가 모두 **상태와 무관한 상수 행동**을 내는 것이
확인됐다 (AUDIT P8). 그런 정책은 오프라인 평가에서 "성공률 50%"처럼 그럴듯한 수치를 내지만
(TEST 링크 두 개 중 하나가 상수 행동과 우연히 맞아서), 정책 품질을 잰 것이 아니다.

이 모듈은 그 상태를 **학습 직후 자동으로 잡아내기 위한** 게이트다. 학습 스크립트가 끝나면
`write_checkpoint_meta()`가 체크포인트 옆에 `<name>.meta.json`을 남기고, 상수 비율이
임계치를 넘으면 로그에 경고를 찍는다. 게이트는 학습을 실패시키지 않는다 — 붕괴한 체크포인트도
"붕괴했다는 사실과 함께" 보존하는 것이 이 리포의 방침이다.

측정 방법
  on-policy : 링크마다 혼잡을 주입하고 그 상태에서 N스텝 동안 고른 행동의 분포
  random    : 무작위 관측 벡터에 대한 행동 분포 (상태 의존성이 있는지 보는 거친 프로브)
  지표      : top_action_share (최빈 행동 비율), action_entropy (경험적 엔트로피, bit),
              distinct_actions, collapsed (= top_action_share >= COLLAPSE_THRESHOLD)

실행:
  python ai-engine/policy_check.py                       # 운영 체크포인트 전부 검사
  python ai-engine/policy_check.py --maml agents/x.pt    # 특정 체크포인트
  python ai-engine/policy_check.py --json out.json
"""
import argparse
import collections
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from topology import LINKS, OSPF_COSTS  # noqa: E402

# 최빈 행동이 이 비율 이상이면 "붕괴"로 표시한다.
COLLAPSE_THRESHOLD = 0.8

PROBE_STEPS_PER_LINK = 8
PROBE_RANDOM_OBS     = 200


def decode_action(action_idx: int) -> str:
    link_idx, cost_idx = divmod(int(action_idx), len(OSPF_COSTS))
    return f"{LINKS[link_idx]}@{OSPF_COSTS[cost_idx]}"


def _entropy_bits(counts: collections.Counter) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    # 단일 행동일 때 -0.0이 나오지 않도록 max로 clip
    return max(0.0, -sum((c / total) * math.log2(c / total) for c in counts.values() if c))


def policy_action_stats(
    agent,
    steps_per_link: int = PROBE_STEPS_PER_LINK,
    n_random: int = PROBE_RANDOM_OBS,
    seed: int = 0,
) -> dict:
    """에이전트의 행동 분포를 재고 붕괴 여부를 판정한다.

    agent: `predict(obs) -> int`와 `is_ready() -> bool`을 가진 객체
           (BaselineAgent / FewShotAgent 둘 다 만족).
    """
    from environment.network_env import NetworkEnv

    env = NetworkEnv(inject_anomalies=False)
    rng = np.random.default_rng(seed)

    on_policy: collections.Counter = collections.Counter()
    for link in LINKS:
        obs, _ = env.reset()
        env.inject_anomaly(link)
        for _ in range(steps_per_link):
            action = agent.predict(obs)
            on_policy[decode_action(action)] += 1
            obs, *_ = env.step(action)
    env.close()

    random_obs: collections.Counter = collections.Counter()
    for _ in range(n_random):
        obs = rng.random(2 * 4 + len(LINKS)).astype(np.float32)
        random_obs[decode_action(agent.predict(obs))] += 1

    total = sum(on_policy.values())
    top_action, top_count = on_policy.most_common(1)[0]
    share = top_count / total

    return {
        "top_action":         top_action,
        "top_action_share":   round(share, 4),
        "action_entropy_bits": round(_entropy_bits(on_policy), 4),
        "distinct_actions":   len(on_policy),
        "collapsed":          bool(share >= COLLAPSE_THRESHOLD),
        "collapse_threshold": COLLAPSE_THRESHOLD,
        "on_policy_counts":   dict(on_policy.most_common()),
        "random_obs_counts":  dict(random_obs.most_common(5)),
        "probe": {
            "links": len(LINKS), "steps_per_link": steps_per_link,
            "n_random_obs": n_random, "seed": seed,
        },
    }


def write_checkpoint_meta(agent, checkpoint_path: str, train_info: dict | None = None) -> dict:
    """체크포인트 옆에 `<name>.meta.json`을 쓰고 요약을 stdout에 남긴다.

    학습 조건(train_info)과 붕괴 검사 결과를 함께 기록해 체크포인트-결과-커밋을 잇는다.
    """
    stats = policy_action_stats(agent)
    meta = {"checkpoint": os.path.basename(checkpoint_path),
            "train": train_info or {}, "policy_check": stats}
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "experiments"))
        from _resultmeta import result_meta
        meta = {**result_meta(seed=(train_info or {}).get("seed"),
                              condition="학습 직후 정책 붕괴 검사 (ROADMAP A-5)"), **meta}
    except Exception:
        pass

    out_path = os.path.splitext(checkpoint_path)[0] + ".meta.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    verdict = "COLLAPSED" if stats["collapsed"] else "ok"
    print(
        f"[policy-check] {verdict}: top={stats['top_action']} "
        f"share={stats['top_action_share']:.2f} entropy={stats['action_entropy_bits']:.2f}bit "
        f"distinct={stats['distinct_actions']} → {out_path}",
        flush=True,
    )
    if stats["collapsed"]:
        print(
            "[policy-check] 경고: 정책이 상태와 무관한 상수 행동에 수렴했다. 이 체크포인트로 잰"
            " 성공률/TTR은 정책 품질이 아니라 상수 행동과 평가 시나리오의 우연한 일치를 반영한다"
            " (cowork/AUDIT_2026-09-09.md P8, ROADMAP Track A).",
            flush=True,
        )
    return meta


def _load_agents(maml_path: str | None, ppo_path: str | None) -> dict:
    from agents.baseline_drl import BaselineAgent
    from agents.few_shot_agent import FewShotAgent
    agents = {}
    agents["ppo"]  = BaselineAgent(ppo_path) if ppo_path else BaselineAgent()
    agents["maml"] = FewShotAgent(maml_path) if maml_path else FewShotAgent()
    return agents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maml", default=None, help="MAML 체크포인트 경로")
    parser.add_argument("--ppo",  default=None, help="PPO 체크포인트 경로")
    parser.add_argument("--json", default=None, help="결과를 이 경로에 JSON으로 저장")
    parser.add_argument("--fail-on-collapse", action="store_true",
                        help="붕괴 감지 시 비영 종료 (기본은 보고만)")
    args = parser.parse_args()

    report, any_collapsed = {}, False
    for name, agent in _load_agents(args.maml, args.ppo).items():
        if not agent.is_ready():
            print(f"{name}: 로드 불가 ({agent.load_error}) — 건너뜀", flush=True)
            report[name] = {"loaded": False, "load_error": agent.load_error}
            continue
        stats = policy_action_stats(agent)
        report[name] = {"loaded": True, **stats}
        any_collapsed |= stats["collapsed"]
        print(
            f"{name:5s} {'COLLAPSED' if stats['collapsed'] else 'ok':9s} "
            f"top={stats['top_action']:12s} share={stats['top_action_share']:.2f} "
            f"entropy={stats['action_entropy_bits']:.2f}bit distinct={stats['distinct_actions']}\n"
            f"      on-policy: {list(stats['on_policy_counts'].items())[:4]}\n"
            f"      random   : {list(stats['random_obs_counts'].items())[:3]}",
            flush=True,
        )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Saved -> {args.json}", flush=True)

    return 1 if (any_collapsed and args.fail_on_collapse) else 0


if __name__ == "__main__":
    raise SystemExit(main())
