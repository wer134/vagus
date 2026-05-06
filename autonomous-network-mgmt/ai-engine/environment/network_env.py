"""NetworkEnv — gymnasium.Env 구현.

State  : [bw×N_NODES, lat×N_NODES, ospf_cost×N_LINKS]  (정규화 float32, 14차원)
Action : Discrete — (link_idx × cost_idx) 단일 정수 인코딩
           action = link_idx * len(OSPF_COSTS) + cost_idx

local_mode=True (학습 기본값):
  HTTP 없이 metric_generator 모듈을 직접 호출 → 매우 빠름 (WSL HTTP 지연 회피)
local_mode=False (평가/실서비스):
  Mock SNMP REST API 호출 (외부 서버 연동 시 이 모드 사용)
"""
import importlib
import sys
import os
import time
from typing import Any

import numpy as np
import gymnasium as gym
from gymnasium import spaces

# ── 상수 ────────────────────────────────────────────────────────────────────
NODES      = ["r1", "r2", "r3", "r4"]
LINKS      = ["r1-r2", "r1-r3", "r2-r3", "r2-r4", "r3-r4", "r1-r4"]
OSPF_COSTS = [10, 20, 50, 100, 200]

N_NODES = len(NODES)
N_LINKS = len(LINKS)

MAX_BW   = 1000.0
MAX_LAT  = 200.0
MAX_COST = 200.0

ANOMALY_PROB        = 0.03
ANOMALY_CLEAR_STEPS = 120

# metric_generator 모듈 경로
_MG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "simulation")


def _load_mg():
    """metric_generator 모듈 로드 (local_mode 전용)."""
    if _MG_PATH not in sys.path:
        sys.path.insert(0, _MG_PATH)
    return importlib.import_module("metric_generator")


class NetworkEnv(gym.Env):
    """네트워크 관리 환경.

    local_mode=True : metric_generator 직접 호출 (학습용, 빠름)
    local_mode=False: HTTP REST 호출 (평가/실서비스용)
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        snmp_base_url: str = "http://localhost:5001",
        max_steps: int = 200,
        fast_mode: bool = False,
        inject_anomalies: bool = True,
        local_mode: bool = True,
        train_links: list[str] | None = None,  # None=전체, 지정 시 해당 링크만 이상 주입
    ):
        super().__init__()
        self.snmp_url        = snmp_base_url
        self.max_steps       = max_steps
        self._fast_mode      = fast_mode
        self._inject_anomalies = inject_anomalies
        self._local_mode     = local_mode
        self._train_links    = train_links or LINKS
        self._step           = 0
        self._anomaly_timers: dict[str, int] = {}
        self._ospf_costs     = {lk: 10 for lk in LINKS}

        obs_dim = 2 * N_NODES + N_LINKS  # 14
        self.observation_space = spaces.Box(0.0, 1.0, shape=(obs_dim,), dtype=np.float32)
        self.action_space      = spaces.Discrete(N_LINKS * len(OSPF_COSTS))

        # local_mode: 모듈 직접 로드
        if local_mode:
            self._mg = _load_mg()
            self._client = None
        else:
            import httpx
            self._client = httpx.Client(timeout=5.0)
            self._mg     = None

    # ── gymnasium API ────────────────────────────────────────────────────────

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._step = 0
        self._anomaly_timers = {}
        self._ospf_costs = {lk: 10 for lk in LINKS}
        self._reset_backend()
        return self._get_obs(), {}

    def step(self, action: int):
        link_idx, cost_idx = divmod(action, len(OSPF_COSTS))
        link = LINKS[link_idx]
        cost = OSPF_COSTS[cost_idx]

        self._set_ospf_cost(link, cost)
        self._ospf_costs[link] = cost

        if self._inject_anomalies:
            self._maybe_inject_anomaly()
            self._tick_anomaly_timers()

        if not self._fast_mode:
            time.sleep(0.05)

        metrics = self._fetch_raw_metrics()
        obs     = self._obs_from_metrics(metrics)

        from reward import compute_reward
        reward = compute_reward(
            latencies     = [m["latency"]    for m in metrics],
            bandwidths    = [m["bandwidth"]  for m in metrics],
            packet_losses = [m["packetLoss"] for m in metrics],
        )

        self._step += 1
        truncated = self._step >= self.max_steps
        info = {
            "step": self._step, "link": link, "cost": cost,
            "anomalies": list(self._anomaly_timers.keys()),
        }
        return obs, reward, False, truncated, info

    def close(self):
        if self._client:
            self._client.close()

    def inject_anomaly(self, link: str | None = None) -> str:
        import random as _r
        target = link if link else _r.choice(LINKS)
        self._inject_congestion(target)
        self._anomaly_timers[target] = ANOMALY_CLEAR_STEPS
        return target

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _reset_backend(self):
        if self._local_mode:
            self._mg.reset_state()
            for lk in LINKS:
                self._mg.set_ospf_cost(lk, 10)
        else:
            try:
                self._client.post(f"{self.snmp_url}/debug/reset")
                for lk in LINKS:
                    self._client.put(f"{self.snmp_url}/ospf/costs/{lk}", json={"cost": 10})
            except Exception:
                pass

    def _maybe_inject_anomaly(self):
        import random as _r
        if _r.random() < ANOMALY_PROB and len(self._anomaly_timers) < 2:
            lk = _r.choice(self._train_links)
            self._inject_congestion(lk)
            self._anomaly_timers[lk] = ANOMALY_CLEAR_STEPS

    def _tick_anomaly_timers(self):
        expired = [lk for lk, t in self._anomaly_timers.items() if t <= 1]
        for lk in expired:
            self._clear_congestion(lk)
            del self._anomaly_timers[lk]
        for lk in list(self._anomaly_timers):
            if lk not in expired:
                self._anomaly_timers[lk] -= 1

    def _inject_congestion(self, link: str):
        if self._local_mode:
            self._mg.inject_congestion(link)
        else:
            try:
                self._client.post(f"{self.snmp_url}/debug/congestion/{link}")
            except Exception:
                pass

    def _clear_congestion(self, link: str):
        if self._local_mode:
            self._mg.clear_congestion(link)
        else:
            try:
                self._client.delete(f"{self.snmp_url}/debug/congestion/{link}")
            except Exception:
                pass

    def _set_ospf_cost(self, link: str, cost: int):
        if self._local_mode:
            self._mg.set_ospf_cost(link, cost)
        else:
            try:
                self._client.put(f"{self.snmp_url}/ospf/costs/{link}", json={"cost": cost})
            except Exception:
                pass

    def _fetch_raw_metrics(self) -> list[dict]:
        if self._local_mode:
            return self._mg.get_all_metrics()
        try:
            resp = self._client.get(f"{self.snmp_url}/metrics")
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return [
                {"nodeId": n, "bandwidth": 500.0, "latency": 10.0, "packetLoss": 0.0}
                for n in NODES
            ]

    def _get_obs(self) -> np.ndarray:
        return self._obs_from_metrics(self._fetch_raw_metrics())

    def _obs_from_metrics(self, metrics: list[dict]) -> np.ndarray:
        mm = {m["nodeId"]: m for m in metrics}
        bw   = np.array([mm.get(n, {}).get("bandwidth", 500.0) / MAX_BW  for n in NODES], dtype=np.float32)
        lat  = np.array([mm.get(n, {}).get("latency",   10.0)  / MAX_LAT for n in NODES], dtype=np.float32)
        cost = np.array([self._ospf_costs.get(lk, 10) / MAX_COST for lk in LINKS], dtype=np.float32)
        return np.clip(np.concatenate([bw, lat, cost]), 0.0, 1.0)
