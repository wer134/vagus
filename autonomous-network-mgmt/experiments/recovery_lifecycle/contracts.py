"""New controller contracts; observations contain no evaluator ground truth."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from recovery_control.types import CostCommand, Observation


class Phase(StrEnum):
    MONITOR = "monitor"
    MITIGATE = "mitigate"
    HOLD = "hold"
    RESTORE = "restore"
    VERIFY = "verify"
    COMPLETED = "completed"


class Mode(StrEnum):
    FIXED = "fixed"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class Settings:
    mode: Mode = Mode.VERIFIED
    hold_samples: int = 3
    phase_hold: int = 3
    max_age: int = 2
    safety_threshold: float = 1.0
    causal: bool = False
    stability_tolerance: float = 0.02


@dataclass(frozen=True, slots=True)
class CalibrationDiagnostics:
    enabled: bool = False
    valid: bool = False
    reason: str = "disabled"
    estimates: int = 0
    estimate: float | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    commands: tuple[CostCommand, ...] = ()
    declared_complete: bool = False
    phase: Phase = Phase.MONITOR
    evidence: tuple[int, ...] = ()
    reason: str = "monitoring"
    calibration: CalibrationDiagnostics = CalibrationDiagnostics()

    @property
    def completed(self) -> bool:
        return self.declared_complete


class Controller(Protocol):
    def decide(self, observation: Observation | None, now: int) -> Decision: ...

