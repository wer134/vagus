# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: from experiments, python -m recovery_research.test_calibration
"""Executable source-cut contract checks using immutable observation fixtures."""

from dataclasses import replace

from recovery_control.types import (
    DEFAULT_CAPACITIES,
    DEFAULT_COSTS,
    Demands,
    LinkValues,
    Observation,
)
from recovery_research.calibration import SourceCutCalibration


def sample(tick: int = 1, target: float = 180.0) -> Observation:
    return Observation(
        sampled_at=tick,
        arrived_at=tick,
        loads=LinkValues((target / 2, target / 2, target / 2, target / 2, 15.0)),
        costs=DEFAULT_COSTS,
        capacities=DEFAULT_CAPACITIES,
        reported_demands=Demands(target / 5, 15.0),
        background_via_source=False,
    )


def test_target_when_cut_valid() -> None:
    # Given a biased demand report and a clean source cut.
    observation = sample()
    calibration = SourceCutCalibration()
    # When source-cut calibration consumes the immutable observation.
    result = calibration.apply(observation, 1)
    # Then only the copied target changes to the independently known target.
    assert result is not None and result.reported_demands.target == 180.0
    assert observation.reported_demands.target == 36.0
    assert calibration.diagnostics.valid and calibration.diagnostics.estimates == 1


def test_fallback_when_first_observation_invalid() -> None:
    for first in (sample(2), replace(sample(), costs=DEFAULT_COSTS.with_value("a-b", 100))):
        # Given an episode whose first accepted sample violates the initial contract.
        calibration = SourceCutCalibration()
        calibration.apply(first, first.sampled_at)
        later = sample(3)
        # When a subsequent normal observation arrives.
        result = calibration.apply(later, 3)
        # Then invalidity is permanent and reported demand is used unchanged.
        assert result is later and not calibration.diagnostics.valid
        assert calibration.diagnostics.estimates == 0


def test_ignored_when_observation_not_fresh() -> None:
    # Given one accepted initial observation.
    calibration = SourceCutCalibration()
    calibration.apply(sample(), 1)
    # When missing, duplicate, reordered, stale, or future samples are offered.
    rejected = ((None, 2), (sample(), 2), (sample(0), 1), (sample(2), 5),
                (sample(9), 5), (replace(sample(2), arrived_at=3), 2))
    for observation, now in rejected:
        assert calibration.apply(observation, now) is observation
    # Then rejected observations never become new calibration evidence.
    assert calibration.diagnostics.estimates == 1
    fresh = calibration.apply(sample(6), 6)
    assert fresh is not None and fresh.reported_demands.target == 180.0


def test_fallback_when_background_transits_source() -> None:
    # Given a formerly valid episode and observable background source transit.
    calibration = SourceCutCalibration()
    calibration.apply(sample(), 1)
    contaminated = replace(sample(2), background_via_source=True)
    calibration.apply(contaminated, 2)
    # When the configuration later stops using source transit.
    later = sample(3)
    result = calibration.apply(later, 3)
    # Then the conservative episode-wide assumption remains invalid.
    assert result is later and calibration.diagnostics.estimates == 1
    assert calibration.diagnostics.reason == "background_transits_source"


def test_fallback_when_shortest_background_route_crosses_source() -> None:
    # Given an initially valid source cut and a costly direct background edge.
    calibration = SourceCutCalibration()
    calibration.apply(sample(), 1)
    observation = replace(sample(2), costs=DEFAULT_COSTS.with_value("b-c", 100))
    # When the inferred observable ECMP background routes include b-a-c.
    result = calibration.apply(observation, 2)
    # Then a false waypoint flag does not hide ordinary route transit.
    assert result is observation and not calibration.diagnostics.valid
    assert calibration.diagnostics.reason == "background_transits_source"


def main() -> None:
    test_target_when_cut_valid()
    test_fallback_when_first_observation_invalid()
    test_ignored_when_observation_not_fresh()
    test_fallback_when_background_transits_source()
    test_fallback_when_shortest_background_route_crosses_source()
    print("calibration: 5 contract checks passed")


if __name__ == "__main__":
    main()
