"""Predeclared frozen policy specifications; never selected from test outcomes."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never

from recovery_completion.controller import CompletionController
from recovery_lifecycle.contracts import Controller, Mode, Settings
from recovery_lifecycle.controller import LifecycleController
from recovery_research.calibrated_fixed import CalibratedFixed
from recovery_research.calibrated_hold10 import CalibratedHold10
from recovery_research.calibrated_verified import CalibratedVerified
from recovery_research.cycle2 import CalibratedForecast
from recovery_research.cycle3 import CausalCalibratedForecast


class Family(StrEnum):
    LIFECYCLE = "lifecycle"
    FORECAST = "forecast"


@dataclass(frozen=True, slots=True)
class PolicySpec:
    name: str
    family: Family
    mode: Mode = Mode.VERIFIED
    hold_samples: int = 3
    phase_hold: int = 3
    max_age: int = 2
    margin: float = 1.0
    calibration: bool = False
    causal: bool = False
    stability_tolerance: float = 0.02


POLICIES: Final = (
    PolicySpec("fixed", Family.LIFECYCLE, mode=Mode.FIXED),
    PolicySpec("verified", Family.LIFECYCLE),
    PolicySpec("hold10", Family.LIFECYCLE, hold_samples=10),
    PolicySpec("forecast", Family.FORECAST),
    PolicySpec("calibrated_fixed", Family.LIFECYCLE, mode=Mode.FIXED, calibration=True),
    PolicySpec("calibrated_verified", Family.LIFECYCLE, calibration=True),
    PolicySpec("calibrated_hold10", Family.LIFECYCLE, hold_samples=10, calibration=True),
    PolicySpec("calibrated_forecast", Family.FORECAST, calibration=True),
    PolicySpec("causal_calibrated_forecast", Family.FORECAST, calibration=True, causal=True),
)


def create_controller(spec: PolicySpec) -> Controller:
    """Create independent episode state from an immutable preregistered spec."""
    settings = Settings(mode=spec.mode, hold_samples=spec.hold_samples, phase_hold=spec.phase_hold,
                        max_age=spec.max_age, safety_threshold=spec.margin, causal=spec.causal,
                        stability_tolerance=spec.stability_tolerance)
    match spec.family:
        case Family.FORECAST:
            if spec.causal and spec.calibration:
                return CausalCalibratedForecast(settings)
            return CalibratedForecast(settings) if spec.calibration else CompletionController(settings)
        case Family.LIFECYCLE:
            return _lifecycle(spec, settings)
        case unreachable:
            assert_never(unreachable)


def _lifecycle(spec: PolicySpec, settings: Settings) -> Controller:
    if not spec.calibration:
        return LifecycleController(settings)
    match spec.mode:
        case Mode.FIXED:
            return CalibratedFixed(settings)
        case Mode.VERIFIED:
            return CalibratedHold10(settings) if spec.hold_samples == 10 else CalibratedVerified(settings)
        case unreachable:
            assert_never(unreachable)
