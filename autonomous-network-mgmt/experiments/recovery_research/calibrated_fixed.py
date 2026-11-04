"""New fixed lifecycle comparison with the supplied source-cut calibration idea."""

from dataclasses import replace

from recovery_lifecycle.contracts import Mode, Settings
from recovery_research.calibration import SourceCutMixin


class CalibratedFixed(SourceCutMixin):
    def __init__(self, settings: Settings = Settings()) -> None:
        super().__init__(replace(settings, mode=Mode.FIXED))

