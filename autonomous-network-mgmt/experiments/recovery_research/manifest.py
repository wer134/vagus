"""Held-out 실행 전에 설정, 외생 일정, 구현 내용을 동결한다."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from typing import Final

from pydantic import TypeAdapter

from recovery_control.scenario import ExogenousTick
from recovery_research.artifacts import FrozenModel, Split
from recovery_research.policies import POLICIES, PolicySpec
from recovery_research.scenarios import ScenarioCase, scenario_bank
from recovery_research.statistics import BootstrapConfig

SOURCE_ROOT: Final = Path(__file__).resolve().parents[1]
PACKAGES: Final = ("recovery_control", "recovery_lifecycle", "recovery_completion", "recovery_research")
SUPPORT_FILES: Final = ("../scripts/research_cycle_report.py", "../scripts/research_findings_plot.py", "../scripts/reproduce_research.sh", "../requirements-research.txt")


@dataclass(frozen=True, slots=True)
class EvaluationError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class SourceHash(FrozenModel):
    path: str
    sha256: str


class ScenarioBank(FrozenModel):
    split: Split
    cases: tuple[ScenarioCase, ...]
    schedule_digest: str


class Manifest(FrozenModel):
    schema_version: str = "recovery_research.manifest.v1"
    implementation_origin: str = "new_from_spec_snippet"
    contract: str = "observations_only_controller;paired_exogenous_v1;settled_safe_completion"
    git_base: str
    git_dirty: bool
    python_version: str
    policies: tuple[PolicySpec, ...]
    banks: tuple[ScenarioBank, ...]
    sources: tuple[SourceHash, ...]
    bootstrap: BootstrapConfig

    @property
    def digest(self) -> str:
        return sha256(self.model_dump_json().encode()).hexdigest()

    def bank(self, split: Split) -> ScenarioBank:
        matches = tuple(bank for bank in self.banks if bank.split == split)
        if len(matches) != 1:
            raise EvaluationError(f"split {split} requires exactly one frozen bank")
        return matches[0]


def source_hashes(root: Path = SOURCE_ROOT) -> tuple[SourceHash, ...]:
    files = sorted(path for package in PACKAGES for path in (root / package).glob("*.py"))
    if not files:
        raise EvaluationError("no research source files found")
    sources = [SourceHash(path=path.relative_to(root).as_posix(), sha256=sha256(path.read_bytes()).hexdigest()) for path in files]
    sources.extend(SourceHash(path=name, sha256=sha256((root / name).read_bytes()).hexdigest()) for name in SUPPORT_FILES if (root / name).is_file())
    return tuple(sources)


def frozen_bank(split: Split) -> ScenarioBank:
    cases = scenario_bank(split)
    adapter = TypeAdapter(tuple[ExogenousTick, ...])
    digest = sha256()
    for case in cases:
        digest.update(case.model_dump_json().encode())
        digest.update(adapter.dump_json(case.scenario.timeline))
    return ScenarioBank(split=split, cases=cases, schedule_digest=digest.hexdigest())


def build_manifest(root: Path = SOURCE_ROOT) -> Manifest:
    repository = root.parents[1]
    git = ["git", "-c", f"safe.directory={repository.as_posix()}", "-C", str(repository)]
    base = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run([*git, "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip())
    return Manifest(
        git_base=base, git_dirty=dirty, python_version=sys.version,
        policies=POLICIES, banks=tuple(frozen_bank(split) for split in Split),
        sources=source_hashes(root), bootstrap=BootstrapConfig(),
    )


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def verify_manifest(manifest: Manifest, root: Path = SOURCE_ROOT) -> None:
    """현재 구현과 고정 정책/뱅크가 모두 정확히 일치해야 실행한다."""
    if manifest.sources != source_hashes(root):
        raise EvaluationError("frozen source hashes differ; create a new experiment before evaluation")
    if manifest.policies != POLICIES:
        raise EvaluationError("frozen policy configuration differs")
    if manifest.banks != tuple(frozen_bank(split) for split in Split):
        raise EvaluationError("frozen scenario banks differ or are incomplete")
    if manifest.bootstrap != BootstrapConfig():
        raise EvaluationError("frozen bootstrap configuration differs")
    if manifest.implementation_origin != "new_from_spec_snippet":
        raise EvaluationError("implementation provenance differs")
    if manifest.schema_version != "recovery_research.manifest.v1":
        raise EvaluationError("unsupported manifest schema")
    if manifest.contract != "observations_only_controller;paired_exogenous_v1;settled_safe_completion":
        raise EvaluationError("evaluation contract differs")
    if manifest.python_version != sys.version:
        raise EvaluationError("Python runtime differs from frozen provenance")
