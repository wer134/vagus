"""지연 적용·경로 수렴·비동기 관측을 분리하는 합성 시뮬레이터."""

from dataclasses import dataclass

from .routing import Forwarding, blend_routes, forwarding_loads, route_plan
from .scenario import Scenario
from .truth import TruthSnapshot
from .types import DEFAULT_CAPACITIES, CommandAck, ContractError, CostCommand, LinkValues, Observation


@dataclass(frozen=True, slots=True)
class _ScheduledCommand:
    command: CostCommand
    effective_at: int


class Simulator:
    """시계·대기열·forwarding이 전진하는 가변 시뮬레이션 상태 머신."""

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self._timeline = scenario.timeline
        self._tick = 0
        self._costs = scenario.initial_costs
        self._forwarding = route_plan(self._costs, scenario.background_via_source)
        self._start_routes: Forwarding = self._forwarding
        self._goal_routes: Forwarding = self._forwarding
        self._transition_started: int | None = None
        self._residual_fraction = 0.0
        self._commands: list[_ScheduledCommand] = []
        self._observations: list[Observation] = []

    @property
    def tick(self) -> int:
        return self._tick

    def issue(self, command: CostCommand) -> CommandAck:
        """즉시 ACK를 반환하되 다음 tick보다 빨리 적용하지 않는다."""
        effective_at = self._tick + max(1, self._scenario.propagation_delay)
        self._commands.append(_ScheduledCommand(command, effective_at))
        return CommandAck(command, self._tick)

    def advance(self) -> tuple[Observation, ...]:
        """1 tick 전진 후 지금까지 도착한 새 관측을 모두 반환한다."""
        if self._tick >= self._scenario.horizon:
            raise ContractError("tick", "scenario horizon exhausted")
        self._tick += 1
        self._apply_commands()
        if self._transition_started is not None:
            elapsed = self._tick - self._transition_started + 1
            fraction = min(1.0, elapsed / self._scenario.convergence_ticks)
            self._forwarding = blend_routes(self._start_routes, self._goal_routes, fraction)
            self._residual_fraction = 1 - fraction
            if fraction == 1.0:
                self._transition_started = None
        self._sample()
        delivered = tuple(sorted((obs for obs in self._observations if obs.arrived_at <= self._tick), key=lambda obs: (obs.arrived_at, obs.sampled_at)))
        self._observations = [obs for obs in self._observations if obs.arrived_at > self._tick]
        return delivered

    def _apply_commands(self) -> None:
        """같은 tick에 도착한 명령은 접수 순서대로 적용한다."""
        ready = [pending for pending in self._commands if pending.effective_at <= self._tick]
        self._commands = [pending for pending in self._commands if pending.effective_at > self._tick]
        if not ready:
            return
        for pending in ready:
            self._costs = self._costs.with_value(pending.command.edge, pending.command.value)
        goal = route_plan(self._costs, self._scenario.background_via_source)
        if goal != self._goal_routes:
            self._start_routes = self._forwarding
            self._goal_routes = goal
            self._transition_started = self._tick

    def _sample(self) -> None:
        exogenous = self._timeline[self._tick]
        if not exogenous.emit_observation:
            return
        actual_loads = forwarding_loads(self._forwarding, exogenous.demands)
        measured_loads = LinkValues(tuple(load * factor for load, factor in zip(actual_loads.values, exogenous.noise_factors.values, strict=True)))
        self._observations.append(Observation(
            sampled_at=self._tick,
            arrived_at=self._tick + exogenous.observation_delay,
            loads=measured_loads,
            costs=self._costs,
            capacities=DEFAULT_CAPACITIES,
            reported_demands=exogenous.reported_demands,
            background_via_source=self._scenario.background_via_source,
        ))

    def truth_snapshot(self) -> TruthSnapshot:
        """평가기만 접근하는 실제 시점 상태를 불변 값으로 반환한다."""
        demands = self._timeline[self._tick].demands
        return TruthSnapshot(
            tick=self._tick,
            loads=forwarding_loads(self._forwarding, demands),
            costs=self._costs,
            demands=demands,
            capacities=DEFAULT_CAPACITIES,
            forwarding=self._forwarding,
            pending_commands=len(self._commands),
            residual_fraction=self._residual_fraction,
            background_via_source=self._scenario.background_via_source,
        )
