"""Cycle 2: supplied calibration idea paired with the same new forecast gate."""

from recovery_completion.controller import CompletionController
from recovery_research.calibration import SourceCutMixin


class CalibratedForecast(SourceCutMixin, CompletionController):
    """Use the source-cut estimate without gaining access to simulator truth."""


def create_controller() -> CalibratedForecast:
    return CalibratedForecast()

