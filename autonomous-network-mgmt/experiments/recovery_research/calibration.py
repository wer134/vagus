"""Reconstruct the supplied source-cut snippet with newly built typed dependencies.

The background-via-source flag is assumed observable routing configuration. It is
not inferred from hidden traffic truth. Any such transit permanently invalidates
the cut: a sum of undirected incident loads would otherwise double-count transit.
"""

from dataclasses import replace

from recovery_control.routing import route_plan
from recovery_control.types import DEFAULT_COSTS, Observation
from recovery_lifecycle.contracts import CalibrationDiagnostics, Settings
from recovery_lifecycle.controller import LifecycleController


class SourceCutCalibration:
    """Mutable per-episode accumulator of source-cut validity and estimates."""

    def __init__(self, max_age: int = 2) -> None:
        self.max_age = max_age
        self.last_sample = -1
        self.seen_initial = False
        self.invalid_reason: str | None = None
        self.diagnostics = CalibrationDiagnostics(enabled=True, reason="awaiting_initial")

    def apply(self, observation: Observation | None, now: int) -> Observation | None:
        """Return a copied calibrated sample, or the original on invalid evidence."""
        if observation is None:
            return None
        if not (
            0 <= now - observation.sampled_at <= self.max_age
            and observation.sampled_at > self.last_sample
            and observation.arrived_at <= now
        ):
            return observation
        self.last_sample = observation.sampled_at
        if not self.seen_initial:
            self.seen_initial = True
            if observation.sampled_at != 1 or observation.costs != DEFAULT_COSTS:
                self.invalid_reason = "initial_state_invalid"
        background = route_plan(observation.costs, observation.background_via_source).background
        if any("a" in path.nodes for path in background) and self.invalid_reason is None:
            self.invalid_reason = "background_transits_source"
        if self.invalid_reason is not None:
            self.diagnostics = replace(
                self.diagnostics, valid=False, reason=self.invalid_reason, estimate=None
            )
            return observation
        estimate = sum(load for edge, load in observation.loads.items() if "a" in edge.split("-"))
        self.diagnostics = CalibrationDiagnostics(
            enabled=True, valid=True, reason="source_cut", estimates=self.diagnostics.estimates + 1,
            estimate=estimate,
        )
        return replace(
            observation,
            reported_demands=replace(observation.reported_demands, target=estimate),
        )


class SourceCutMixin(LifecycleController):
    """Apply identical calibration to both lifecycle and forecast comparisons."""

    def __init__(self, settings: Settings = Settings()) -> None:
        super().__init__(settings)
        self.source_cut = SourceCutCalibration(settings.max_age)
        self.calibration = self.source_cut.diagnostics

    def _prepare_observation(self, observation: Observation | None, now: int) -> Observation | None:
        calibrated = self.source_cut.apply(observation, now)
        self.calibration = self.source_cut.diagnostics
        return calibrated

