"""에이전트 정책이 상태와 무관한 상수 행동인지 확인 (cowork/AUDIT_2026-09-09.md P8).

실행: cd ai-engine && python ../cowork/scratch/policy_collapse_check.py
"""
import os, sys, collections
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "ai-engine"))
from agents.baseline_drl import BaselineAgent
from agents.few_shot_agent import FewShotAgent
from environment.network_env import NetworkEnv, LINKS, OSPF_COSTS

def dec(a):
    l, c = divmod(int(a), len(OSPF_COSTS)); return f"{LINKS[l]}@{OSPF_COSTS[c]}"

env = NetworkEnv(inject_anomalies=False)
agents = {
    "ppo (2026-09-06, 50k steps)": BaselineAgent(),
    "maml (2026-09-09, sampled rollouts)": FewShotAgent(),
    "maml (pre-audit, argmax rollouts)": FewShotAgent(os.path.join(
        os.path.dirname(__file__), "..", "..", "ai-engine", "agents", "maml_network_pre_audit.pt")),
}
for name, ag in agents.items():
    if not ag.is_ready():
        print(f"{name}: not loadable ({ag.load_error})"); continue
    on_policy = collections.Counter()
    for link in LINKS:                       # 링크별 혼잡을 주입하고 8스텝 동안 행동 분포를 본다
        obs, _ = env.reset(); env.inject_anomaly(link)
        for _ in range(8):
            a = ag.predict(obs); on_policy[dec(a)] += 1
            obs, *_ = env.step(a)
    rnd = collections.Counter(dec(ag.predict(np.random.rand(14).astype(np.float32))) for _ in range(200))
    print(f"{name}\n  on-policy (6 links × 8 steps): {on_policy.most_common(4)}\n  random obs ×200: {rnd.most_common(3)}")
