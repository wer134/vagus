# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: from experiments, python -m recovery_research.test_cycle2
"""Calibration ablation, observed causal confirmation and real simulator checks."""

from dataclasses import replace

from recovery_completion.controller import CompletionController
from recovery_control.scenario import Scenario
from recovery_control.simulation import Simulator
from recovery_control.types import DEFAULT_COSTS, DETOUR_COMMAND, LinkValues, Observation
from recovery_lifecycle.contracts import Controller, Decision, Phase, Settings
from recovery_research.cycle2 import CalibratedForecast
from recovery_research.cycle3 import CausalCalibratedForecast
from recovery_research.policies import POLICIES, Family, PolicySpec, create_controller
from recovery_research.test_calibration import sample


def detoured(tick: int, target: float = 180) -> Observation:
    return replace(sample(tick, target), costs=DEFAULT_COSTS.with_value("a-b", 100),
                   loads=LinkValues((0, target, 0, target, 15)))


def through_hold(controller: Controller) -> Decision:
    controller.decide(sample(1, 35), 1)
    controller.decide(sample(2), 2)
    controller.decide(detoured(3), 3)
    controller.decide(detoured(4), 4)
    controller.decide(detoured(5), 5)
    return controller.decide(detoured(6), 6)


def test_restore_gate_when_demand_is_biased() -> None:
    # Given identical observations with target180 and reported target36.
    settings = Settings(phase_hold=1)
    raw, calibrated = CompletionController(settings), CalibratedForecast(settings)
    # When both consume the same full detection and safe-detour evidence.
    raw_result, corrected_result = through_hold(raw), through_hold(calibrated)
    # Then only the raw forecast restores while true source-cut demand is high.
    assert raw_result.commands and raw_result.commands[0].value == 10
    assert corrected_result.phase == Phase.HOLD and not corrected_result.commands
    assert corrected_result.calibration.estimate == 180


def test_fallback_matches_raw_when_initial_sample_missing() -> None:
    # Given a calibrated episode with a missing initial reference sample.
    settings = Settings(phase_hold=1)
    raw, calibrated = CompletionController(settings), CalibratedForecast(settings)
    inputs = (sample(2), detoured(3), detoured(4), detoured(5), detoured(6))
    # When both controllers receive the same subsequent evidence.
    pairs = [(raw.decide(obs, obs.sampled_at), calibrated.decide(obs, obs.sampled_at)) for obs in inputs]
    # Then fallback uses the raw gate while making invalid calibration explicit.
    assert all(left.commands == right.commands and left.phase == right.phase for left, right in pairs)
    assert not pairs[-1][1].calibration.valid
    assert pairs[-1][1].calibration.reason == "initial_state_invalid"


def test_completion_when_only_postcommand_stable_evidence_exists() -> None:
    # Given restoration issued after a low-demand stable detour.
    controller = CausalCalibratedForecast(Settings(phase_hold=1, max_age=20))
    controller.decide(sample(1, 35), 1)
    controller.decide(sample(2), 10)
    controller.decide(detoured(11, 35), 11)
    for tick in (12, 13, 14):
        result = controller.decide(detoured(tick, 35), tick + 1)
    assert result.commands and result.commands[0].value == 10
    controller.decide(None, 16)
    old = replace(sample(15, 35), arrived_at=17)
    rejected = controller.decide(old, 17)
    assert rejected.phase == Phase.VERIFY and not rejected.evidence
    # When three fresh, normal-cost, stable post-command observations arrive.
    controller.decide(sample(18, 35), 18)
    controller.decide(sample(19, 35), 19)
    completed = controller.decide(sample(20, 35), 20)
    # Then completion identifies only the causal evidence ticks.
    assert completed.declared_complete and completed.evidence == (18, 19, 20)
    assert not controller.decide(sample(21), 21).commands


def test_integration_when_simulator_recovers() -> None:
    # Given the actual delayed-actuation simulator and a calibrated causal policy.
    simulator = Simulator(Scenario(seed=7, demand_bias=0.2))
    controller = CausalCalibratedForecast()
    commands: list[tuple[str, float]] = []
    completed_at: int | None = None
    # When only publicly delivered observations and time are passed to it.
    for now in range(1, 81):
        observations = simulator.advance()
        result = controller.decide(observations[-1] if observations else None, now)
        for command in result.commands:
            commands.append((command.edge, command.value))
            simulator.issue(command)
        if result.declared_complete and completed_at is None:
            completed_at = now
    # Then its two actions lead to real restoration and a safe terminal state.
    assert commands[0] == (DETOUR_COMMAND.edge, DETOUR_COMMAND.value) and len(commands) == 2
    assert completed_at is not None and simulator.truth_snapshot().restored
    assert simulator.truth_snapshot().safe


def test_deterministic_when_factories_reused() -> None:
    # Given each of the nine preregistered specifications.
    for spec in POLICIES:
        left, right = create_controller(spec), create_controller(spec)
        # When independent state machines consume the same inputs.
        decisions = [(left.decide(sample(tick), tick), right.decide(sample(tick), tick)) for tick in range(1, 5)]
        # Then per-episode construction is deterministic without shared state.
        assert all(a == b for a, b in decisions)


def test_uncalibrated_when_only_causal_flag_requested() -> None:
    # Given an explicitly uncalibrated causal forecast specification.
    controller = create_controller(PolicySpec("raw_causal", Family.FORECAST, causal=True))
    # When its first valid observation arrives.
    result = controller.decide(sample(1, 35), 1)
    # Then causal gating does not silently enable the independent calibration flag.
    assert not result.calibration.enabled


def test_baseline_actions_match_when_calibration_is_added() -> None:
    # Given each raw lifecycle baseline and its calibrated counterpart.
    for raw_spec, calibrated_spec in zip(POLICIES[:3], POLICIES[4:7], strict=True):
        raw, calibrated = create_controller(raw_spec), create_controller(calibrated_spec)
        inputs = (sample(1, 35), sample(2), *(detoured(tick) for tick in range(3, 20)))
        # When the pair sees the same biased-demand observations.
        decisions = [(raw.decide(obs, obs.sampled_at), calibrated.decide(obs, obs.sampled_at)) for obs in inputs]
        # Then observed-load-only baseline actions are unaffected by demand calibration.
        assert all(left.commands == right.commands and left.phase == right.phase for left, right in decisions)
        assert decisions[-1][1].calibration.estimates > 0


def main() -> None:
    test_restore_gate_when_demand_is_biased()
    test_fallback_matches_raw_when_initial_sample_missing()
    test_completion_when_only_postcommand_stable_evidence_exists()
    test_integration_when_simulator_recovers()
    test_deterministic_when_factories_reused()
    test_uncalibrated_when_only_causal_flag_requested()
    test_baseline_actions_match_when_calibration_is_added()
    print("cycle2: 7 ablation and integration checks passed")


if __name__ == "__main__":
    main()
