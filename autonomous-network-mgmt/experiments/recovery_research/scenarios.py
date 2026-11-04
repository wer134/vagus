"""정책 행동과 독립적인 사전 고정 시나리오 뱅크."""

from dataclasses import replace
from enum import StrEnum
from hashlib import sha256
from typing import Final, assert_never

from recovery_control.scenario import Scenario
from recovery_research.artifacts import FrozenModel, Split


class Regime(StrEnum):
    CLEAN = "clean"
    DEMAND_BIAS = "demand_bias"
    DELAYED = "delayed"
    MISSING = "missing"
    NOISY = "noisy"
    BACKGROUND_TRANSIT = "background_transit"


REGIMES: Final = tuple(Regime)
DEV_SEEDS: Final = tuple(range(4))
TEST_SEEDS: Final = tuple(range(1000, 1020))


class ScenarioCase(FrozenModel):
    scenario_id: str
    regime: Regime
    scenario: Scenario

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()


def scenario_case(seed: int, regime: Regime) -> ScenarioCase:
    """동일 seed는 환경과 무관하게 동일 사건 시각을 사용한다."""
    start = 8 + seed % 3
    end = 28 + seed % 7
    base = Scenario(seed=seed, horizon=120, incident_start=start, incident_end=end)
    match regime:
        case Regime.CLEAN:
            scenario = base
        case Regime.DEMAND_BIAS:
            scenario = replace(base, demand_bias=0.55)
        case Regime.DELAYED:
            scenario = replace(base, observation_delay=1, delay_jitter=2)
        case Regime.MISSING:
            scenario = replace(base, missing_probability=0.35)
        case Regime.NOISY:
            scenario = replace(base, load_noise=0.08)
        case Regime.BACKGROUND_TRANSIT:
            scenario = replace(base, background_via_source=True)
        case unreachable:
            assert_never(unreachable)
    return ScenarioCase(scenario_id=f"seed{seed}:{regime.value}", regime=regime, scenario=scenario)


def scenario_bank(split: Split) -> tuple[ScenarioCase, ...]:
    match split:
        case Split.DEV:
            seeds = DEV_SEEDS
        case Split.TEST:
            seeds = TEST_SEEDS
        case unreachable:
            assert_never(unreachable)
    return tuple(scenario_case(seed, regime) for seed in seeds for regime in REGIMES)
