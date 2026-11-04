from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4

from recovery_control.simulation import Simulator
from recovery_control.types import EDGES, LinkValues
from recovery_lifecycle.contracts import Controller
from recovery_research.policies import POLICIES, create_controller
from .models import (
    CalibrationView, CreateRequest, Frame, LinkReading, ObservationView,
    SessionView, TruthView,
)


@dataclass(frozen=True, slots=True)
class SessionError(Exception):
    status: int
    detail: str


@dataclass(slots=True)
class LiveSession:
    identity: str
    request: CreateRequest
    simulator: Simulator
    controller: Controller
    requested_costs: LinkValues
    history: list[Frame] = field(default_factory=list)
    declared_tick: int | None = None
    false_completion: bool = False
    unsafe_restore: bool = False
    restoring: bool = False
    command_count: int = 0
    overload_area: float = 0.0

    def advance(self, ticks: int) -> SessionView:
        horizon = self.request.scenario().horizon
        if self.simulator.tick >= horizon:
            raise SessionError(409, "시뮬레이션이 끝났습니다. 새 실험을 시작하세요.")
        for _ in range(min(ticks, horizon - self.simulator.tick)):
            delivered = self.simulator.advance()
            sample = max(delivered, key=lambda item: item.sampled_at, default=None)
            decision = self.controller.decide(sample, self.simulator.tick)
            for command in decision.commands:
                self.restoring = self.restoring or command.value < self.requested_costs[command.edge]
                self.requested_costs = self.requested_costs.with_value(command.edge, command.value)
                self.simulator.issue(command)
                self.command_count += 1
            truth = self.simulator.truth_snapshot()
            self.overload_area += sum(max(truth.loads[e] / truth.capacities[e] - 1, 0) for e in EDGES)
            if self.restoring:
                self.unsafe_restore = self.unsafe_restore or not truth.loads_safe
                self.restoring = not truth.settled
            if decision.declared_complete and self.declared_tick is None:
                self.declared_tick = truth.tick
                self.false_completion = not (truth.safe and truth.restored)
            calibration = decision.calibration
            self.history.append(Frame(
                tick=truth.tick, phase=decision.phase.value, reason=decision.reason,
                commands=tuple(f"{c.edge} → {c.value:g}" for c in decision.commands),
                evidence=decision.evidence, completed=decision.declared_complete,
                observation=ObservationView.from_sample(sample) if sample else None,
                truth=TruthView(target_demand=truth.demands.target,
                                max_utilization=truth.max_utilization,
                                settled=truth.settled, restored=truth.restored, safe=truth.safe,
                                links=tuple(LinkReading(edge=e, load=truth.loads[e],
                                                        capacity=truth.capacities[e], cost=truth.costs[e]) for e in EDGES)),
                calibration=CalibrationView(enabled=calibration.enabled, valid=calibration.valid,
                                            reason=calibration.reason, estimates=calibration.estimates,
                                            estimate=calibration.estimate),
            ))
        return self.view()

    def view(self) -> SessionView:
        horizon = self.request.scenario().horizon
        return SessionView(session_id=self.identity, request=self.request,
                           tick=self.simulator.tick, horizon=horizon,
                           finished=self.simulator.tick >= horizon,
                           declared_tick=self.declared_tick, false_completion=self.false_completion,
                           unsafe_restore=self.unsafe_restore, command_count=self.command_count,
                           overload_area=self.overload_area, history=tuple(self.history))


class SessionStore:
    def __init__(self, capacity: int = 32) -> None:
        self._capacity = capacity
        self._sessions: dict[str, LiveSession] = {}
        self._lock = RLock()

    def create(self, request: CreateRequest) -> SessionView:
        policy = next((p for p in POLICIES if p.name == request.policy), None)
        if policy is None:
            raise SessionError(422, "등록되지 않은 정책입니다.")
        with self._lock:
            if len(self._sessions) >= self._capacity:
                raise SessionError(429, "실험 세션이 가득 찼습니다. 기존 세션을 삭제하세요.")
            scenario = request.scenario()
            identity = uuid4().hex
            session = LiveSession(identity, request, Simulator(scenario),
                                  create_controller(policy), scenario.initial_costs)
            self._sessions[identity] = session
            return session.view()

    def _get(self, identity: str) -> LiveSession:
        session = self._sessions.get(identity)
        if session is None:
            raise SessionError(404, "실험 세션을 찾을 수 없습니다.")
        return session

    def view(self, identity: str) -> SessionView:
        with self._lock:
            return self._get(identity).view()

    def advance(self, identity: str, ticks: int) -> SessionView:
        with self._lock:
            return self._get(identity).advance(ticks)

    def delete(self, identity: str) -> None:
        with self._lock:
            self._get(identity)
            del self._sessions[identity]
