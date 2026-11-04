"""New lifecycle reference controller; no original implementation was supplied.

Missing, stale, future, duplicate and reordered input resets consecutive evidence.
Fixed mode deliberately uses elapsed time after detection, including completion
without observation evidence; it is a legacy comparison, not a safety guarantee.
"""

from typing import assert_never

from recovery_control.types import (
    DEFAULT_COSTS, DETOUR_COMMAND, RESTORE_COMMAND, CostCommand, Observation,
)
from recovery_lifecycle.contracts import CalibrationDiagnostics, Decision, Mode, Phase, Settings


class LifecycleController:
    """Mutable per-episode state machine driven solely by observations and time."""

    def __init__(self, settings: Settings = Settings()) -> None:
        self.settings = settings
        self.phase = Phase.MONITOR
        self.phase_started = 0
        self.command_at = -1
        self.last_sample = -1
        self.expected_costs = DEFAULT_COSTS
        self.evidence: tuple[int, ...] = ()
        self.previous_safe: Observation | None = None
        self.calibration = CalibrationDiagnostics()

    def decide(self, observation: Observation | None, now: int) -> Decision:
        """Advance at most one phase; commands are emitted only on phase entry."""
        match self.phase:
            case Phase.COMPLETED:
                return self._result("completed")
            case Phase.MONITOR | Phase.MITIGATE | Phase.HOLD | Phase.RESTORE | Phase.VERIFY:
                prepared = self._prepare_observation(observation, now)
                fresh = self._accept_observation(prepared, now)
                return self._advance(fresh, now)
            case unreachable:
                assert_never(unreachable)

    def step(self, observation: Observation | None, now: int) -> Decision:
        return self.decide(observation, now)

    def _prepare_observation(self, observation: Observation | None, now: int) -> Observation | None:
        return observation

    def _accept_observation(self, observation: Observation | None, now: int) -> Observation | None:
        if observation is not None and (
            0 <= now - observation.sampled_at <= self.settings.max_age
            and observation.sampled_at > self.last_sample
            and observation.arrived_at <= now
        ):
            self.last_sample = observation.sampled_at
            return observation
        self._clear_evidence()
        return None

    def _advance(self, observation: Observation | None, now: int) -> Decision:
        match self.phase:
            case Phase.MONITOR:
                if observation is not None and observation.max_utilization > self.settings.safety_threshold:
                    self.phase = Phase.MITIGATE
                    self.evidence = (observation.sampled_at,)
                    decision = self._result("overload_detected", (DETOUR_COMMAND,))
                    self._mark_command(DETOUR_COMMAND, now)
                    return decision
                return self._result("monitoring")
            case Phase.MITIGATE:
                if now - self.phase_started >= self.settings.phase_hold:
                    self.phase = Phase.HOLD
                    self.phase_started = now
                return self._result("mitigation_wait")
            case Phase.HOLD:
                if self._ready(observation, now):
                    self.phase = Phase.RESTORE
                    decision = self._result("restore_gate_passed", (RESTORE_COMMAND,))
                    self._mark_command(RESTORE_COMMAND, now)
                    return decision
                return self._result("hold_gate_wait")
            case Phase.RESTORE:
                if now - self.phase_started >= self.settings.phase_hold:
                    self.phase = Phase.VERIFY
                    self.phase_started = now
                return self._result("restoration_wait")
            case Phase.VERIFY:
                if self._ready(observation, now):
                    self.phase = Phase.COMPLETED
                    return self._result("completion_gate_passed")
                return self._result("verification_wait")
            case Phase.COMPLETED:
                return self._result("completed")
            case unreachable:
                assert_never(unreachable)

    def _mark_command(self, command: CostCommand, now: int) -> None:
        self.command_at = now
        self.phase_started = now
        self.expected_costs = DEFAULT_COSTS.with_value(command.edge, command.value)
        self._clear_evidence()

    def _ready(self, observation: Observation | None, now: int) -> bool:
        match self.settings.mode:
            case Mode.FIXED:
                return now - self.phase_started >= self.settings.phase_hold
            case Mode.VERIFIED:
                if observation is None:
                    return False
                if not self._safe(observation) or observation.costs != self.expected_costs:
                    self._clear_evidence()
                    return False
                if self.settings.causal and observation.sampled_at <= self.command_at:
                    self._clear_evidence()
                    return False
                if self.settings.causal and self.previous_safe is not None:
                    changes = zip(observation.loads.values, self.previous_safe.loads.values,
                                  observation.capacities.values, strict=True)
                    if any(abs(current - previous) > capacity * self.settings.stability_tolerance
                           for current, previous, capacity in changes):
                        self._clear_evidence()
                self.evidence += (observation.sampled_at,)
                self.previous_safe = observation
                return (len(self.evidence) >= self.settings.hold_samples
                        and now - self.phase_started >= self.settings.phase_hold)
            case unreachable:
                assert_never(unreachable)

    def _safe(self, observation: Observation) -> bool:
        return observation.max_utilization <= self.settings.safety_threshold

    def _clear_evidence(self) -> None:
        self.evidence = ()
        self.previous_safe = None

    def _result(self, reason: str, commands: tuple[CostCommand, ...] = ()) -> Decision:
        return Decision(commands=commands, declared_complete=self.phase == Phase.COMPLETED,
                        phase=self.phase, evidence=self.evidence, reason=reason,
                        calibration=self.calibration)

