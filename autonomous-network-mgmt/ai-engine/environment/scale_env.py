"""규모 확장 실험의 시나리오 정의와 학습 환경 (cowork/ROADMAP.md §6 (가), 2026-11-17).

평가 스크립트(experiments/scale_benchmark.py)와 학습 환경이 **같은 시나리오 정의와 같은 사이클
순서**를 쓰도록 한 파일에 둔다. 아래 상수는 측정 전에 정했고 결과를 보고 바꾸지 않는다.

시나리오 (에피소드 하나)
  혼잡     : 링크 1개(확률 0.6) 또는 2개(0.4), 균등 추출
  약한 링크: 없음(0.5) 또는 링크 하나의 용량을 공칭 부하의 1.2배로 (기본은 2.0배)
  수요 변화: 없음(0.5) 또는 노드 하나의 모든 쌍 수요 ×2.0 — SHIFT_AT 사이클 직후 (핫스팟)
  분할     : 시나리오 키의 crc32 % 5 == 0 → TEST (20 %), 나머지 TRAIN. 학습은 TRAIN 키만 본다

사이클 (평가 하네스와 같다)
  tick → 노드 관측 → 링크 관측 → 결정 → 실행 → 노드 관측(복구 판정) → (SHIFT_AT이면 수요 변화)
  관측·판정마다 시뮬레이터 난수를 같은 만큼 쓰므로, 같은 seed면 제어기가 달라도 노이즈가 같다.

학습 환경
  관측 (64차원, Abilene): [노드 대역폭/1000 ×N, 노드 지연/200 ×N, cost/200 ×L, 링크 이용률/2 ×L,
                          링크 손실/0.05 ×L], clip 0~1 — K4 관측 v2와 같은 구성
  행동 : Discrete(L×5 + 1). 마지막 인덱스 = 무행동 (ROADMAP A-2)
  보상 v2 : −(SLA 위반 노드 비율) − 0.02 × [cost가 실제로 바뀌었는가] (ROADMAP A-3)
  보상 v3 : +1 × [전 노드 SLA 충족] − 0.02 × [cost 변경] — **사후 진단용** (2026-11-18). 사전 등록
           실험(v2)에서 PPO가 실패한 뒤, TRAIN 분석에서 v2가 무행동(−7.20)과 규칙(−7.21)을 구분하지
           못함을 보고 만들었다. 평가 지표(전 노드 SLA)와 보상을 일치시킨다
  길이 : HORIZON 사이클

allowed_keys를 주면 그 시나리오 키만 뽑는다 (풀이 있는 TRAIN 시나리오만으로 학습하는 커리큘럼용).
"""
import os
import random
import sys
import zlib
from dataclasses import dataclass

import numpy as np
import gymnasium as gym
from gymnasium import spaces

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(_HERE, "..", "..", "simulation"))
from netsim import NetSim, TOPOLOGIES, OSPF_COSTS, SLA_LATENCY_MS, SLA_PACKET_LOSS  # noqa: E402

HORIZON      = 30
SHIFT_AT     = 3
SHIFT_FACTOR = 2.0
P_DOUBLE     = 0.4
P_WEAK       = 0.5
P_SHIFT      = 0.5
TEST_MOD     = 5

REWARD_VERSION   = 2          # 사전 등록 실험의 보상. v3은 ScaleEnv(reward_version=3)로 명시해야 쓴다
REWARD_VERSIONS  = (2, 3)
ACTION_CHANGE_COST = 0.02


@dataclass(frozen=True)
class Scenario:
    topo: str
    congested: tuple[str, ...]
    weak: str | None
    shift: str | None            # 수요가 2배가 되는 노드

    @property
    def key(self) -> str:
        return f"{self.topo}|c={'+'.join(sorted(self.congested))}|w={self.weak or '-'}|s={self.shift or '-'}"

    @property
    def split(self) -> str:
        return "test" if zlib.crc32(self.key.encode()) % TEST_MOD == 0 else "train"


def sample_scenario(topo_name: str, rng: random.Random, split: str | None = None,
                    allowed_keys: set[str] | None = None) -> Scenario:
    topo = TOPOLOGIES[topo_name]
    while True:
        k = 2 if rng.random() < P_DOUBLE else 1
        cong = tuple(rng.sample(topo.links, k))
        weak = rng.choice(topo.links) if rng.random() < P_WEAK else None
        shift = rng.choice(topo.nodes) if rng.random() < P_SHIFT else None
        s = Scenario(topo_name, cong, weak, shift)
        if (split is None or s.split == split) and (allowed_keys is None or s.key in allowed_keys):
            return s


def all_scenarios(topo_name: str) -> list[Scenario]:
    """시나리오 공간 전체 (혼잡 1~2개 × 약한 링크 없음/1개 × 수요 변화 없음/노드 1개)."""
    import itertools
    topo = TOPOLOGIES[topo_name]
    congs = [(lk,) for lk in topo.links] + list(itertools.combinations(topo.links, 2))
    return [Scenario(topo_name, c, w, s)
            for c in congs for w in (None, *topo.links) for s in (None, *topo.nodes)]


def setup(sim: NetSim, scn: Scenario, seed: int) -> None:
    sim.reset(seed=seed)
    if scn.weak:
        sim.set_weak(scn.weak)
    for lk in scn.congested:
        sim.inject_congestion(lk)


def node_ok_count(node_metrics: list[dict]) -> int:
    return sum(1 for m in node_metrics if m["latency"] < SLA_LATENCY_MS and m["packetLoss"] < SLA_PACKET_LOSS)


def build_obs(sim: NetSim, nm: list[dict], lm: list[dict]) -> np.ndarray:
    parts = [
        [m["bandwidth"] / 1000.0 for m in nm],
        [m["latency"] / 200.0 for m in nm],
        [c / 200.0 for c in sim.costs],
        [m["utilization"] / 2.0 for m in lm],
        [m["packetLoss"] / 0.05 for m in lm],
    ]
    return np.clip(np.concatenate([np.asarray(p, dtype=np.float32) for p in parts]), 0.0, 1.0)


def decode_action(sim: NetSim, a: int) -> tuple[str, int] | None:
    if a == sim.L * len(OSPF_COSTS):
        return None
    i, k = divmod(int(a), len(OSPF_COSTS))
    return sim.topo.links[i], OSPF_COSTS[k]


class ScaleEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, topo: str = "abilene", split: str = "train", seed: int = 0,
                 reward_version: int = REWARD_VERSION, allowed_keys: set[str] | None = None):
        super().__init__()
        if reward_version not in REWARD_VERSIONS:
            raise ValueError(reward_version)
        self.reward_version = reward_version
        self.allowed_keys = allowed_keys
        self.sim = NetSim(TOPOLOGIES[topo])
        self.topo_name = topo
        self.split = split
        self._rng = random.Random(seed)
        n_obs = 2 * self.sim.N + 3 * self.sim.L
        self.observation_space = spaces.Box(0.0, 1.0, shape=(n_obs,), dtype=np.float32)
        self.action_space = spaces.Discrete(self.sim.L * len(OSPF_COSTS) + 1)
        self.scenario: Scenario | None = None
        self._t = 0

    def _observe(self) -> np.ndarray:
        self.sim.tick()
        nm = self.sim.node_metrics()
        lm = self.sim.link_metrics()
        return build_obs(self.sim, nm, lm)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:                       # gymnasium 규약: seed를 주면 시나리오 추출도 재현된다
            self._rng = random.Random(seed)
        if options and "scenario" in options:
            self.scenario = options["scenario"]
            sim_seed = options["sim_seed"]
        else:
            self.scenario = sample_scenario(self.topo_name, self._rng, self.split, self.allowed_keys)
            sim_seed = self._rng.randrange(2 ** 31)
        setup(self.sim, self.scenario, sim_seed)
        self._t = 1
        return self._observe(), {"scenario": self.scenario.key}

    def step(self, action):
        act = decode_action(self.sim, int(action))
        changed = self.sim.set_cost(*act) if act else False
        ok_nodes = node_ok_count(self.sim.node_metrics())
        if self.reward_version == 2:
            reward = -(self.sim.N - ok_nodes) / self.sim.N - ACTION_CHANGE_COST * float(changed)
        else:
            reward = float(ok_nodes == self.sim.N) - ACTION_CHANGE_COST * float(changed)
        if self._t == SHIFT_AT and self.scenario.shift:
            self.sim.scale_node_demand(self.scenario.shift, SHIFT_FACTOR)
        truncated = self._t >= HORIZON
        info = {"sla_ok": ok_nodes == self.sim.N, "changed": changed}
        if truncated:
            obs = np.zeros(self.observation_space.shape, dtype=np.float32)
        else:
            self._t += 1
            obs = self._observe()
        return obs, float(reward), False, truncated, info


if __name__ == "__main__":
    from gymnasium.utils.env_checker import check_env

    # 1) 분할: TRAIN/TEST가 겹치지 않고 TEST는 약 20 %
    rng = random.Random(0)
    keys = [sample_scenario("abilene", rng) for _ in range(5000)]
    frac = sum(s.split == "test" for s in keys) / len(keys)
    assert 0.15 < frac < 0.25, frac
    tr = sample_scenario("abilene", random.Random(1), "train")
    te = sample_scenario("abilene", random.Random(1), "test")
    assert tr.split == "train" and te.split == "test"
    print(f"OK — 시나리오 분할: TEST 비율 {frac:.3f} (키 해시 기준, 결정적)")

    # 2) gymnasium 규약
    env = ScaleEnv("abilene", "train", seed=0)
    check_env(env, skip_render_check=True)
    assert env.observation_space.shape == (64,) and env.action_space.n == 71
    print("OK — gymnasium check_env 통과, 관측 64차원·행동 71개")

    # 3) 무행동 에피소드: 길이 HORIZON, 보상 범위
    obs, _ = env.reset()
    total, n = 0.0, 0
    while True:
        obs, r, term, trunc, info = env.step(env.action_space.n - 1)
        total += r; n += 1
        assert -1.0 <= r <= 0.0
        if trunc:
            break
    assert n == HORIZON
    print(f"OK — 무행동 에피소드 {n} 스텝, 누적 보상 {total:.2f}")

    # 3b) 사후 진단용 보상 v3 범위, 허용 키 제한, 시나리오 공간 크기
    e3 = ScaleEnv("abilene", "train", seed=0, reward_version=3)
    e3.reset()
    assert all(-0.02 <= e3.step(e3.action_space.n - 1)[1] <= 1.0 for _ in range(HORIZON))
    allowed = {sample_scenario("abilene", random.Random(9), "train").key}
    e4 = ScaleEnv("abilene", "train", seed=0, allowed_keys=allowed)
    assert all(e4.reset()[1]["scenario"] in allowed for _ in range(3))
    assert len(all_scenarios("abilene")) == 105 * 15 * 12
    print("OK — 보상 v3 범위, 허용 키 제한, 시나리오 공간 18,900")

    # 4) 같은 scenario·seed면 같은 궤적 (평가 하네스와 환경이 같은 시나리오를 재현할 수 있다)
    scn = sample_scenario("abilene", random.Random(3), "test")
    a = ScaleEnv(); b = ScaleEnv()
    oa, _ = a.reset(options={"scenario": scn, "sim_seed": 7})
    ob, _ = b.reset(options={"scenario": scn, "sim_seed": 7})
    assert np.array_equal(oa, ob)
    for t in range(HORIZON):
        act = (t * 7) % a.action_space.n
        ra = a.step(act); rb = b.step(act)
        assert np.array_equal(ra[0], rb[0]) and ra[1] == rb[1]
    print("OK — 같은 시나리오·seed → 같은 궤적")
    print("\n모든 자가 테스트 통과")
