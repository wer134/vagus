"""평가 산출물의 검증 가능한 JSON 경계."""

from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from recovery_research.statistics import PairedEstimate


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Split(StrEnum):
    DEV = "dev"
    TEST = "test"


class MetricName(StrEnum):
    FALSE_COMPLETION = "false_completion"
    TIMEOUT = "timeout"
    UNSAFE_RESTORE = "unsafe_restore"
    DECLARATION_DELAY = "declaration_delay"
    TRUE_RECOVERED_TICK = "true_recovered_tick"
    RESTORATION_TICK = "restoration_tick"
    COMMAND_COUNT = "command_count"
    OVERLOAD_AREA = "overload_area"


METRICS: Final = tuple(MetricName)


class CalibrationRecord(FrozenModel):
    enabled: bool = False
    valid: bool
    reason: str
    estimates: int
    estimate: float | None


class CommandRecord(FrozenModel):
    edge: str
    value: float


class DecisionTrace(FrozenModel):
    tick: int
    sampled_at: int | None
    phase: str
    reason: str
    commands: tuple[CommandRecord, ...]
    declared_complete: bool
    evidence: tuple[int, ...]
    calibration: CalibrationRecord
    truth_settled: bool
    truth_max_utilization: float


class EpisodeResult(FrozenModel):
    scenario_id: str
    scenario_digest: str
    seed: int
    regime: str
    policy: str
    declared_tick: int | None
    true_recovered_tick: int | None
    declaration_delay: int | None
    false_completion: bool
    timeout: bool
    unsafe_restore: bool
    restoration_tick: int | None
    command_count: int
    overload_area: float
    calibration: CalibrationRecord
    trace: tuple[DecisionTrace, ...]
    trace_omitted: int


class MetricSummary(FrozenModel):
    name: MetricName
    mean: float | None
    observed: int
    missing: int


class PolicySummary(FrozenModel):
    policy: str
    regime: str
    episode_count: int
    metrics: tuple[MetricSummary, ...]


class PolicyComparison(FrozenModel):
    candidate: str
    baseline: str
    regime: str
    metric: MetricName
    estimate: PairedEstimate


class ExperimentPayload(FrozenModel):
    schema_version: str = "recovery_research.v1"
    manifest_digest: str
    split: Split
    scenario_count: int
    policy_names: tuple[str, ...]
    episodes: tuple[EpisodeResult, ...]
    summaries: tuple[PolicySummary, ...]
    comparisons: tuple[PolicyComparison, ...]


class RunMetadata(FrozenModel):
    created_at: str
    manifest_digest: str
    payload_digest: str
    split: Split | None


def write_json(path: Path, value: BaseModel, replace: bool = False) -> None:
    """기본적으로 기존 산출물을 보존한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if replace else "x", encoding="utf-8", newline="\n") as stream:
        stream.write(value.model_dump_json(indent=2))
        stream.write("\n")
