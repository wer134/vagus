"""추론 파이프라인 테스트용 더미 모델 파일 생성."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

# --- PPO 더미 모델 ---
from stable_baselines3 import PPO
from environment.network_env import NetworkEnv

print("[1/2] PPO 더미 모델 생성...")
env = NetworkEnv(snmp_base_url="http://localhost:5001", fast_mode=True)
model = PPO("MlpPolicy", env, n_steps=64, verbose=0)
# 학습 없이 즉시 저장 (랜덤 가중치)
model.save("agents/ppo_network")
env.close()
print("  -> agents/ppo_network.zip 저장 완료")

# --- MAML 더미 모델 ---
import torch
from agents.few_shot_agent import PolicyNet, MODEL_PATH

print("[2/2] MAML 더미 모델 생성...")
policy = PolicyNet()
torch.save(policy.state_dict(), MODEL_PATH)
print(f"  -> {MODEL_PATH} 저장 완료")

print("Done.")
