# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: from experiments, python -m recovery_research.test_controllers
"""Observation-only lifecycle tests; no simulator or truth fixture is needed."""

from dataclasses import replace

from recovery_control.types import DEFAULT_COSTS, DETOUR_COMMAND
from recovery_lifecycle.contracts import Mode, Phase, Settings
from recovery_lifecycle.controller import LifecycleController
from recovery_research.test_calibration import sample


def test_once_when_fixed_episode_completes() -> None:
    # Given a time-based legacy reference with a one-tick phase duration.
    controller = LifecycleController(Settings(mode=Mode.FIXED, phase_hold=1))
    first = controller.decide(sample(), 1)
    # When it proceeds without any further observations.
    decisions = [controller.decide(None, tick) for tick in range(2, 12)]
    # Then it sends exactly detour and restore once, and completion is sticky.
    commands = first.commands + tuple(command for result in decisions for command in result.commands)
    assert len(commands) == 2 and commands[0] == DETOUR_COMMAND
    assert commands[1].value == 10 and decisions[-1].declared_complete
    assert all(not result.commands for result in decisions if result.declared_complete)


def test_waits_when_samples_missing_or_reused() -> None:
    # Given a verified controller after its mitigation command.
    controller = LifecycleController(Settings(phase_hold=1))
    controller.decide(sample(), 1)
    controller.decide(None, 2)
    safe = replace(sample(3, 35), costs=DEFAULT_COSTS.with_value("a-b", 100))
    controller.decide(safe, 3)
    controller.decide(safe, 4)
    controller.decide(None, 5)
    # When one fresh safe observation follows those gaps.
    result = controller.decide(replace(safe, sampled_at=6, arrived_at=6), 6)
    # Then old/missing inputs have reset consecutive evidence, preventing restore.
    assert result.phase == Phase.HOLD and result.evidence == (6,) and not result.commands


def test_waits_when_causal_sample_predates_command() -> None:
    # Given a command issued at tick 10 from a fresh but delayed overload sample.
    controller = LifecycleController(Settings(phase_hold=1, max_age=5, causal=True))
    controller.decide(replace(sample(8), arrived_at=10), 10)
    controller.decide(None, 11)
    old = replace(sample(9, 35), arrived_at=12, costs=DEFAULT_COSTS.with_value("a-b", 100))
    # When a later-delivered sample was actually sampled before the command.
    result = controller.decide(old, 12)
    # Then it cannot count as post-command evidence.
    assert not result.commands and not result.evidence and not result.declared_complete


def test_waits_when_observed_flow_is_still_changing() -> None:
    # Given causal verification and three safe observations with changing loads.
    controller = LifecycleController(Settings(phase_hold=1, causal=True))
    controller.decide(sample(), 1)
    controller.decide(None, 2)
    for tick in (3, 4):
        observation = replace(sample(tick, 30 + tick * 10), costs=DEFAULT_COSTS.with_value("a-b", 100))
        controller.decide(observation, tick)
    # When the third observation is safe but not stable.
    result = controller.decide(
        replace(sample(5, 80), costs=DEFAULT_COSTS.with_value("a-b", 100)), 5
    )
    # Then three merely safe observations do not prove observed stability.
    assert result.evidence == (5,) and not result.commands


def test_waits_when_observed_cost_not_applied() -> None:
    # Given a verified controller whose detour command has not appeared in samples.
    controller = LifecycleController(Settings(phase_hold=1))
    controller.decide(sample(), 1)
    controller.decide(None, 2)
    # When multiple safe observations retain the pre-command costs.
    results = [controller.decide(sample(tick, 35), tick) for tick in range(3, 10)]
    # Then configured-cost acknowledgement is required before restoring.
    assert all(result.phase == Phase.HOLD and not result.commands for result in results)


def test_command_records_evidence_when_gate_passes() -> None:
    # Given a verified controller and three accepted safe observations.
    controller = LifecycleController(Settings(phase_hold=1))
    detection = controller.decide(sample(), 1)
    controller.decide(None, 2)
    for tick in (3, 4):
        controller.decide(replace(sample(tick, 35), costs=DEFAULT_COSTS.with_value("a-b", 100)), tick)
    # When the restore gate passes on the third sample.
    restoration = controller.decide(replace(sample(5, 35), costs=DEFAULT_COSTS.with_value("a-b", 100)), 5)
    # Then both commands retain the observation ticks supporting their decisions.
    assert detection.evidence == (1,)
    assert restoration.commands and restoration.evidence == (3, 4, 5)


def test_hold10_when_ten_safe_fresh_samples_arrive() -> None:
    # Given a ten-observation baseline after its mitigation wait.
    controller = LifecycleController(Settings(phase_hold=1, hold_samples=10))
    controller.decide(sample(), 1)
    controller.decide(None, 2)
    for tick in range(3, 12):
        before_tenth = controller.decide(replace(sample(tick, 35), costs=DEFAULT_COSTS.with_value("a-b", 100)), tick)
        assert not before_tenth.commands
    # When the tenth fresh safe observation arrives.
    result = controller.decide(replace(sample(12, 35), costs=DEFAULT_COSTS.with_value("a-b", 100)), 12)
    # Then exactly ten evidence samples support restoration.
    assert result.commands and result.evidence == tuple(range(3, 13))


def main() -> None:
    test_once_when_fixed_episode_completes()
    test_waits_when_samples_missing_or_reused()
    test_waits_when_causal_sample_predates_command()
    test_waits_when_observed_flow_is_still_changing()
    test_waits_when_observed_cost_not_applied()
    test_command_records_evidence_when_gate_passes()
    test_hold10_when_ten_safe_fresh_samples_arrive()
    print("controllers: 7 contract checks passed")


if __name__ == "__main__":
    main()
