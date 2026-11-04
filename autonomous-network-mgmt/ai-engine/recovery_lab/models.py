from dataclasses import replace
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from recovery_control.scenario import Scenario
from recovery_control.types import EDGES, Observation
from recovery_research.scenarios import Regime, scenario_case


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Overrides(Model):
    demand_bias: Annotated[float | None, Field(ge=0.1, le=2)] = None
    observation_delay: Annotated[int | None, Field(ge=0, le=15)] = None
    delay_jitter: Annotated[int | None, Field(ge=0, le=10)] = None
    missing_probability: Annotated[float | None, Field(ge=0, le=1)] = None
    load_noise: Annotated[float | None, Field(ge=0, le=0.5)] = None
    propagation_delay: Annotated[int | None, Field(ge=0, le=15)] = None
    convergence_ticks: Annotated[int | None, Field(ge=1, le=15)] = None
    background_via_source: bool | None = None
    initial_missing: bool | None = None

    def apply(self, base: Scenario) -> Scenario:
        return replace(base, **self.model_dump(exclude_none=True))


class CreateRequest(Model):
    policy: str = "calibrated_forecast"
    regime: Regime = Regime.DEMAND_BIAS
    seed: Annotated[int, Field(ge=0, le=2**31-1)] = 42
    overrides: Overrides = Overrides()

    def scenario(self) -> Scenario:
        return self.overrides.apply(scenario_case(self.seed, self.regime).scenario)


class StepRequest(Model):
    ticks: Annotated[int, Field(ge=1, le=120)] = 1


class LinkReading(Model):
    edge: str
    load: float
    capacity: float
    cost: float


class ObservationView(Model):
    sampled_at: int
    arrived_at: int
    target_demand: float
    background_demand: float
    max_utilization: float
    links: tuple[LinkReading, ...]

    @classmethod
    def from_sample(cls, sample: Observation) -> "ObservationView":
        return cls(sampled_at=sample.sampled_at, arrived_at=sample.arrived_at,
                   target_demand=sample.reported_demands.target,
                   background_demand=sample.reported_demands.background,
                   max_utilization=sample.max_utilization,
                   links=tuple(LinkReading(edge=e, load=sample.loads[e],
                                           capacity=sample.capacities[e], cost=sample.costs[e]) for e in EDGES))


class TruthView(Model):
    """평가기 전용 값으로 제어기에 전달하지 않는다."""
    target_demand: float
    max_utilization: float
    settled: bool
    restored: bool
    safe: bool
    links: tuple[LinkReading, ...]


class CalibrationView(Model):
    enabled: bool
    valid: bool
    reason: str
    estimates: int
    estimate: float | None


class Frame(Model):
    tick: int
    phase: str
    reason: str
    commands: tuple[str, ...]
    evidence: tuple[int, ...]
    completed: bool
    observation: ObservationView | None
    truth: TruthView
    calibration: CalibrationView


class SessionView(Model):
    session_id: str
    request: CreateRequest
    tick: int
    horizon: int
    finished: bool
    declared_tick: int | None
    false_completion: bool
    unsafe_restore: bool
    command_count: int
    overload_area: float
    history: tuple[Frame, ...]
    exploratory: bool = True
