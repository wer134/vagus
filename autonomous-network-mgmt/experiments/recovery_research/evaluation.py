"""Controller에는 관측만 전달하고 실제 안전성은 외부에서 채점한다."""

from typing import Final

from recovery_control.simulation import Simulator
from recovery_lifecycle.contracts import CalibrationDiagnostics, Controller
from recovery_research.artifacts import CalibrationRecord, CommandRecord, DecisionTrace, EpisodeResult
from recovery_research.policies import PolicySpec, create_controller
from recovery_research.scenarios import ScenarioCase

TRACE_LIMIT: Final = 96
RESTORATION_HOLD: Final = 10


def calibration_record(value: CalibrationDiagnostics) -> CalibrationRecord:
    return CalibrationRecord(enabled=value.enabled, valid=value.valid, reason=value.reason, estimates=value.estimates, estimate=value.estimate)


def evaluate_episode(case: ScenarioCase, policy: str, controller: Controller) -> EpisodeResult:
    """끝까지 실행해 조기 선언 이후의 안전성과 원상복귀도 보존한다.

    delay는 선언 tick에서 최초 실제 settled-safe tick을 뺀 부호 있는 값이다.
    overload_area는 각 링크의 max(utilization-1, 0)을 tick에 걸쳐 합한다.
    restore window는 비용 감소 요청부터 pending/convergence가 모두 끝날 때까지다.
    """
    simulator = Simulator(case.scenario)
    requested_costs = case.scenario.initial_costs
    declared_tick: int | None = None
    recovered_tick: int | None = None
    restoration_tick: int | None = None
    false_completion = False
    unsafe_restore = False
    restoring = False
    normal_streak = 0
    command_count = 0
    overload_area = 0.0
    trace: list[DecisionTrace] = []
    trace_omitted = 0
    previous_reason: tuple[str, str, bool, str] | None = None
    calibration = calibration_record(CalibrationDiagnostics())
    for _ in range(case.scenario.horizon):
        delivered = simulator.advance()
        observation = max(delivered, key=lambda item: item.sampled_at, default=None)
        decision = controller.decide(observation, simulator.tick)
        for command in decision.commands:
            restoring = restoring or command.value < requested_costs[command.edge]
            requested_costs = requested_costs.with_value(command.edge, command.value)
            simulator.issue(command)
            command_count += 1
        truth = simulator.truth_snapshot()
        overload_area += sum(max(load / capacity - 1, 0) for load, capacity in zip(truth.loads.values, truth.capacities.values, strict=True))
        if restoring:
            unsafe_restore = unsafe_restore or not truth.loads_safe
            restoring = not truth.settled
        if truth.tick >= case.scenario.incident_start:
            if truth.safe and recovered_tick is None:
                recovered_tick = truth.tick
            normal_streak = normal_streak + 1 if truth.restored and truth.loads_safe else 0
            if normal_streak >= RESTORATION_HOLD and restoration_tick is None:
                restoration_tick = truth.tick
        first_declaration = decision.declared_complete and declared_tick is None
        if first_declaration:
            declared_tick = truth.tick
            false_completion = not (truth.safe and truth.restored)
        calibration = calibration_record(decision.calibration)
        reason_key = (decision.phase.value, decision.reason, calibration.valid, calibration.reason)
        if reason_key != previous_reason or decision.commands or first_declaration or truth.tick == case.scenario.horizon:
            point = DecisionTrace(
                tick=truth.tick, sampled_at=observation.sampled_at if observation else None,
                phase=decision.phase.value, reason=decision.reason,
                commands=tuple(CommandRecord(edge=command.edge, value=command.value) for command in decision.commands),
                declared_complete=decision.declared_complete, evidence=decision.evidence,
                calibration=calibration, truth_settled=truth.settled,
                truth_max_utilization=truth.max_utilization,
            )
            if len(trace) < TRACE_LIMIT:
                trace.append(point)
            else:
                trace[-1] = point
                trace_omitted += 1
        previous_reason = reason_key
    delay = declared_tick - recovered_tick if declared_tick is not None and recovered_tick is not None else None
    return EpisodeResult(
        scenario_id=case.scenario_id, scenario_digest=case.digest, seed=case.scenario.seed,
        regime=case.regime.value, policy=policy, declared_tick=declared_tick,
        true_recovered_tick=recovered_tick, declaration_delay=delay,
        false_completion=false_completion, timeout=declared_tick is None,
        unsafe_restore=unsafe_restore, restoration_tick=restoration_tick,
        command_count=command_count, overload_area=overload_area,
        calibration=calibration, trace=tuple(trace), trace_omitted=trace_omitted,
    )


def run_episode(case: ScenarioCase, specification: PolicySpec) -> EpisodeResult:
    return evaluate_episode(case, specification.name, create_controller(specification))
