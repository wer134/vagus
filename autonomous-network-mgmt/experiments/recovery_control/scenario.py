"""행동열과 무관한 seed/tick/field 기반 외생 일정."""

from dataclasses import dataclass
from hashlib import blake2b
from math import isfinite

from .types import DEFAULT_COSTS, ContractError, Demands, LinkValues


def keyed_unit(seed: int, tick: int, field: str) -> float:
    """공유 난수 상태를 소비하지 않는 [0,1) 값."""
    payload = f"{seed}:{tick}:{field}".encode("ascii")
    return int.from_bytes(blake2b(payload, digest_size=8).digest(), "big") / 2**64


@dataclass(frozen=True, slots=True)
class ExogenousTick:
    """한 tick에 고정된 수요와 관측 전송 조건."""

    tick: int
    demands: Demands
    reported_demands: Demands
    observation_delay: int
    emit_observation: bool
    noise_factors: LinkValues


@dataclass(frozen=True, slots=True)
class Scenario:
    """외생 조건 명세; 실제 수요를 포함하므로 제어기로 전달하지 않는다."""

    seed: int
    horizon: int = 80
    incident_start: int = 8
    incident_end: int = 30
    normal_target: float = 35.0
    peak_target: float = 180.0
    background_demand: float = 15.0
    demand_bias: float = 1.0
    propagation_delay: int = 3
    convergence_ticks: int = 4
    observation_delay: int = 0
    delay_jitter: int = 0
    missing_probability: float = 0.0
    load_noise: float = 0.0
    background_via_source: bool = False
    initial_missing: bool = False
    initial_costs: LinkValues = DEFAULT_COSTS

    def __post_init__(self) -> None:
        if not 1 < self.incident_start < self.incident_end <= self.horizon:
            raise ContractError("incident", "requires 1 < start < end <= horizon")
        if min(self.propagation_delay, self.observation_delay, self.delay_jitter) < 0:
            raise ContractError("delay", "requires nonnegative ticks")
        if self.convergence_ticks < 1:
            raise ContractError("convergence_ticks", "requires at least one tick")
        if not 0 <= self.missing_probability <= 1 or not 0 <= self.load_noise <= 1:
            raise ContractError("observation", "probability and noise must lie in [0,1]")
        if not isfinite(self.demand_bias) or self.demand_bias <= 0:
            raise ContractError("demand_bias", "requires finite positive factor")
        _ = Demands(self.normal_target, self.background_demand)
        _ = Demands(self.peak_target, self.background_demand)
        if any(value <= 0 for value in self.initial_costs.values):
            raise ContractError("initial_costs", "requires positive routing costs")

    def exogenous_at(self, tick: int) -> ExogenousTick:
        """초기 기준 샘플은 즉시 제공하고 이후 일정은 키로 독립 생성한다."""
        if not 0 <= tick <= self.horizon:
            raise ContractError("tick", "outside scenario horizon")
        nominal = self.peak_target if self.incident_start <= tick < self.incident_end else self.normal_target
        target_factor = 1.0 if tick <= 1 else 0.97 + 0.06 * keyed_unit(self.seed, tick, "target")
        background_factor = 1.0 if tick <= 1 else 0.97 + 0.06 * keyed_unit(self.seed, tick, "background")
        demands = Demands(nominal * target_factor, self.background_demand * background_factor)
        reported = Demands(demands.target * self.demand_bias, demands.background * self.demand_bias)
        jitter = int(keyed_unit(self.seed, tick, "delay") * (2 * self.delay_jitter + 1)) - self.delay_jitter
        delay = 0 if tick <= 1 else max(0, self.observation_delay + jitter)
        emit = not self.initial_missing if tick == 1 else keyed_unit(self.seed, tick, "missing") >= self.missing_probability
        factors = tuple(1.0 + self.load_noise * (2 * keyed_unit(self.seed, tick, f"load:{index}") - 1) for index in range(5))
        return ExogenousTick(tick, demands, reported, delay, emit, LinkValues(factors))

    @property
    def timeline(self) -> tuple[ExogenousTick, ...]:
        return tuple(self.exogenous_at(tick) for tick in range(self.horizon + 1))
