"""New verified lifecycle comparison with identical source-cut calibration."""

from dataclasses import replace

from recovery_lifecycle.contracts import Mode, Settings
from recovery_research.calibration import SourceCutMixin


class CalibratedVerified(SourceCutMixin):
    def __init__(self, settings: Settings = Settings()) -> None:
        super().__init__(replace(settings, mode=Mode.VERIFIED))

