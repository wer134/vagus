"""New forecast gate based only on reported demand and observed capacities."""

from dataclasses import replace

from recovery_control.routing import route_loads
from recovery_control.types import DEFAULT_COSTS, Observation
from recovery_lifecycle.contracts import Settings
from recovery_lifecycle.controller import LifecycleController


class CompletionController(LifecycleController):
    """Forecast normal-cost safety; optional settling means observed stability only.

    No actual convergence flag, pending command, incident end or true demand is
    available. The retained require_settled name never means simulator truth.
    """

    def __init__(self, settings: Settings = Settings(), require_settled: bool | None = None) -> None:
        effective = settings if require_settled is None else replace(settings, causal=require_settled)
        super().__init__(effective)
        self.require_settled = effective.causal

    def _safe(self, observation: Observation) -> bool:
        predicted = route_loads(DEFAULT_COSTS, observation.reported_demands,
                               observation.background_via_source)
        forecast_safe = all(
            load <= capacity * self.settings.safety_threshold
            for load, capacity in zip(predicted.values, observation.capacities.values, strict=True)
        )
        return super()._safe(observation) and forecast_safe

