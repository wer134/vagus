"""작은 개발 fixture만 사용하며 held-out 정책 결과를 실행하지 않는다."""

from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from recovery_control.types import CostCommand, Observation
from recovery_lifecycle.contracts import Decision
from recovery_research.aggregation import build_payload
from recovery_research.artifacts import ExperimentPayload, RunMetadata, Split, write_json
from recovery_research.evaluation import evaluate_episode, run_episode
from recovery_research.manifest import EvaluationError, Manifest, ScenarioBank, build_manifest, load_manifest, source_hashes, verify_manifest
from recovery_research.policies import POLICIES
from recovery_research.runner import evaluate_bank, run_frozen
from recovery_research.scenarios import Regime, scenario_case


@dataclass(frozen=True, slots=True)
class ScriptedController:
    declaration_at: int | None = None
    commands: tuple[tuple[int, CostCommand], ...] = ()

    def decide(self, observation: Observation | None, now: int) -> Decision:
        if observation is not None:
            assert type(observation) is Observation
            assert not hasattr(observation, "pending_commands")
            assert not hasattr(observation, "demands")
        return Decision(commands=tuple(command for tick, command in self.commands if tick == now), declared_complete=now == self.declaration_at)


def fixture_manifest(manifest: Manifest) -> Manifest:
    case = scenario_case(0, Regime.CLEAN)
    return manifest.model_copy(update={"banks": (ScenarioBank(split=Split.DEV, cases=(case,), schedule_digest="development_fixture"),)})


def test_repeatability_and_pairing_when_shared_scenario(manifest: Manifest) -> None:
    # Given: every frozen policy receives the same small development scenario.
    fixture = fixture_manifest(manifest)
    # When: run the complete fixture twice.
    first = evaluate_bank(fixture, Split.DEV)
    second = evaluate_bank(fixture, Split.DEV)
    # Then: deterministic payloads retain every policy and a shared schedule ID.
    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.episodes) == len(POLICIES)
    assert len({row.scenario_digest for row in first.episodes}) == 1
    assert ExperimentPayload.model_validate_json(first.model_dump_json()) == first


def test_timeout_retained_when_observations_missing() -> None:
    # Given: no initial or later observations.
    base = scenario_case(0, Regime.MISSING)
    case = base.model_copy(update={"scenario": replace(base.scenario, missing_probability=1.0, initial_missing=True)})
    # When: a real policy runs through the full horizon.
    result = run_episode(case, POLICIES[0])
    # Then: no fabricated TTR replaces missing completion.
    assert result.timeout and result.declared_tick is None and result.declaration_delay is None
    assert result.true_recovered_tick == case.scenario.incident_end
    assert result.restoration_tick == case.scenario.incident_end + 9


def test_missing_truth_times_when_network_never_recovers() -> None:
    # Given: traffic remains above default-route capacity for the full horizon.
    base = scenario_case(0, Regime.CLEAN)
    case = base.model_copy(update={"scenario": replace(base.scenario, normal_target=200, peak_target=220)})
    # When: no mitigating command or completion declaration is made.
    result = evaluate_episode(case, "persistent_overload_fixture", ScriptedController())
    # Then: horizon is never substituted for missing recovery/restoration times.
    assert result.timeout and result.true_recovered_tick is None
    assert result.restoration_tick is None and result.declaration_delay is None
    assert result.overload_area > 0


def test_false_completion_when_restore_is_pending() -> None:
    # Given: a detour, then premature restoration and declaration during overload.
    case = scenario_case(0, Regime.CLEAN)
    controller = ScriptedController(10, ((8, CostCommand("a-b", 100)), (10, CostCommand("a-b", 10))))
    # When: the observation-only scripted controller is evaluated.
    result = evaluate_episode(case, "fixture", controller)
    # Then: pending/convergence are unsafe, and the early signed delay survives.
    assert result.false_completion and result.unsafe_restore and not result.timeout
    assert result.declared_tick == 10 and result.declaration_delay is not None and result.declaration_delay < 0
    assert result.command_count == 2
    assert any(point.declared_complete and not point.truth_settled for point in result.trace)


def test_restore_window_when_issue_tick_is_safe() -> None:
    # Given: the detour has settled before the restore request at tick 16.
    case = scenario_case(0, Regime.CLEAN)
    controller = ScriptedController(16, ((8, CostCommand("a-b", 100)), (16, CostCommand("a-b", 10))))
    # When: restoring default routes creates overload only on subsequent ticks.
    result = evaluate_episode(case, "window_fixture", controller)
    # Then: pending state invalidates completion and later window overload is counted.
    restore = next(point for point in result.trace if point.tick == 16)
    assert restore.truth_max_utilization < 1 and not restore.truth_settled
    assert result.false_completion and result.unsafe_restore


def test_completion_rejects_safe_but_unrestored_detour() -> None:
    # Given: the detour is settled and safe, but baseline routing was never restored.
    case = scenario_case(0, Regime.CLEAN)
    controller = ScriptedController(20, ((8, CostCommand("a-b", 100)),))
    # When: the full lifecycle is declared complete without a restore command.
    result = evaluate_episode(case, "unrestored_fixture", controller)
    # Then: transient recovery alone is not lifecycle completion.
    assert result.false_completion
    assert result.restoration_tick is None


def test_timeout_when_post_initial_observations_are_stale() -> None:
    # Given: only the required initial calibration sample is fresh.
    base = scenario_case(0, Regime.DELAYED)
    case = base.model_copy(update={"scenario": replace(base.scenario, observation_delay=6, delay_jitter=0)})
    # When: the controller never receives fresh incident evidence.
    result = run_episode(case, next(policy for policy in POLICIES if policy.name == "calibrated_forecast"))
    # Then: stale evidence cannot manufacture a completion time.
    assert result.timeout and result.declared_tick is None and result.declaration_delay is None


def test_invalid_calibration_retained_when_initial_sample_missing() -> None:
    # Given: sampling starts late, without the initial normal-state reference.
    base = scenario_case(0, Regime.CLEAN)
    case = base.model_copy(update={"scenario": replace(base.scenario, initial_missing=True)})
    # When: a calibrated policy runs using its documented fallback.
    result = run_episode(case, next(policy for policy in POLICIES if policy.name == "calibrated_forecast"))
    # Then: the raw artifact preserves the invalidity reason and absent estimate.
    assert result.calibration.enabled and not result.calibration.valid
    assert result.calibration.reason == "initial_state_invalid" and result.calibration.estimate is None


def test_manifest_rejected_when_source_changes(manifest: Manifest) -> None:
    # Given: a frozen source snapshot in an isolated development directory.
    with TemporaryDirectory() as directory:
        root = Path(directory)
        package = root / "recovery_research"
        package.mkdir()
        source = package / "fixture.py"
        source.write_text("VALUE = 1\n", encoding="utf-8")
        frozen = manifest.model_copy(update={"sources": source_hashes(root)})
        verify_manifest(frozen, root)
        # When: executable source changes after freeze.
        source.write_text("VALUE = 2\n", encoding="utf-8")
        try:
            verify_manifest(frozen, root)
        except EvaluationError as error:
            # Then: source mutation is rejected.
            assert "source hashes" in error.reason
        else:
            raise AssertionError("changed source accepted")


def test_manifest_rejected_when_policy_changes(manifest: Manifest) -> None:
    # Given: the frozen policy set.
    changed = replace(manifest.policies[0], phase_hold=manifest.policies[0].phase_hold + 1)
    altered = manifest.model_copy(update={"policies": (changed, *manifest.policies[1:])})
    # When: frozen configuration no longer equals executable configuration.
    try:
        verify_manifest(altered)
    except EvaluationError as error:
        # Then: policy mutation is rejected.
        assert "policy configuration" in error.reason
    else:
        raise AssertionError("changed policy accepted")


def test_comparison_rejected_when_policy_episode_missing(manifest: Manifest) -> None:
    # Given: a complete small fixture with one policy removed.
    fixture = fixture_manifest(manifest)
    case = fixture.bank(Split.DEV).cases[0]
    episodes = tuple(run_episode(case, policy) for policy in fixture.policies[:-1])
    # When: an incomplete comparison reaches aggregation.
    try:
        build_payload(fixture, Split.DEV, episodes)
    except EvaluationError as error:
        # Then: missing comparisons cannot silently improve a report.
        assert "incomplete" in error.reason
    else:
        raise AssertionError("incomplete comparison accepted")


def test_provenance_rejected_when_manifest_changes(manifest: Manifest) -> None:
    # Given: freeze metadata binds the full original manifest, including provenance.
    with TemporaryDirectory() as directory:
        output = Path(directory)
        altered = manifest.model_copy(update={"git_base": "changed_after_freeze"})
        write_json(output / "manifest.json", altered)
        write_json(output / "freeze.metadata.json", RunMetadata(created_at="fixture", manifest_digest=manifest.digest, payload_digest=manifest.digest, split=None))
        # When: an altered manifest is used through the real run entrypoint.
        try:
            run_frozen(output / "manifest.json", Split.DEV)
        except EvaluationError as error:
            # Then: the altered provenance is rejected before running scenarios.
            assert "manifest changed after freeze" in error.reason
        else:
            raise AssertionError("changed frozen provenance accepted")


def test_cli_freeze_and_immutable_test_when_output_exists() -> None:
    # Given: a fresh experiment directory, with no evaluated test scenarios.
    with TemporaryDirectory() as directory:
        output = Path(directory) / "experiment"
        # When: the actual CLI freezes the experiment.
        process = subprocess.run([sys.executable, "-X", "utf8", "-m", "recovery_research.runner", "freeze", "--output-dir", str(output)], check=False, capture_output=True, text=True, encoding="utf-8")
        # Then: a valid saved manifest exists; existing test output is preserved.
        assert process.returncode == 0, process.stderr
        manifest = load_manifest(output / "manifest.json")
        verify_manifest(manifest)
        existing = output / "test.json"
        existing.write_text("preserve", encoding="utf-8")
        try:
            run_frozen(output / "manifest.json", Split.TEST, replace=True)
        except EvaluationError as error:
            assert "cannot be replaced" in error.reason
        else:
            raise AssertionError("test replacement accepted")
        assert existing.read_text(encoding="utf-8") == "preserve"
        try:
            write_json(output / "manifest.json", manifest)
        except FileExistsError as error:
            assert error.filename == str(output / "manifest.json")
        else:
            raise AssertionError("manifest overwrite accepted")


def main() -> None:
    manifest = build_manifest()
    test_repeatability_and_pairing_when_shared_scenario(manifest)
    test_timeout_retained_when_observations_missing()
    test_missing_truth_times_when_network_never_recovers()
    test_false_completion_when_restore_is_pending()
    test_restore_window_when_issue_tick_is_safe()
    test_completion_rejects_safe_but_unrestored_detour()
    test_timeout_when_post_initial_observations_are_stale()
    test_invalid_calibration_retained_when_initial_sample_missing()
    test_manifest_rejected_when_source_changes(manifest)
    test_manifest_rejected_when_policy_changes(manifest)
    test_comparison_rejected_when_policy_episode_missing(manifest)
    test_provenance_rejected_when_manifest_changes(manifest)
    test_cli_freeze_and_immutable_test_when_output_exists()
    print("평가 하네스 자체검증 13개 통과 (개발 fixture 전용)")


if __name__ == "__main__":
    main()
