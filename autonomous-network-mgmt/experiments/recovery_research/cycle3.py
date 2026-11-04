"""Cycle 3: new post-command, observed-stability completion experiment."""

from dataclasses import replace

from recovery_lifecycle.contracts import Settings
from recovery_research.cycle2 import CalibratedForecast


class CausalCalibratedForecast(CalibratedForecast):
    def __init__(self, settings: Settings = Settings()) -> None:
        super().__init__(replace(settings, causal=True))


def create_controller() -> CausalCalibratedForecast:
    return CausalCalibratedForecast()

