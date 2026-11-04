#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.10", "matplotlib>=3.10"]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# Run: uv run scripts/test_research_reporting.py
# ──────────────────
"""작은 고정 입력으로 보고서 수치와 CLI 산출물을 검증한다."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Final
from xml.etree import ElementTree

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from research_cycle_report import ReportData, load_data, render_report
from research_findings_plot import FIGURE_NAME, build_figure, save_figure
from recovery_research.aggregation import build_payload
from recovery_research.artifacts import CalibrationRecord, EpisodeResult, Split, write_json
from recovery_research.manifest import EvaluationError, Manifest, ScenarioBank, SourceHash
from recovery_research.policies import POLICIES
from recovery_research.scenarios import REGIMES, scenario_case
from recovery_research.statistics import BootstrapConfig


def fixture() -> ReportData:
    """2개 seed의 측정값을 명시하며 실제 simulator는 실행하지 않는다."""
    cases = tuple(scenario_case(seed, regime) for seed in (0, 1) for regime in REGIMES)
    manifest = Manifest(git_base="fixture", git_dirty=True, python_version="fixture", policies=POLICIES,
                        banks=(ScenarioBank(split=Split.DEV, cases=cases, schedule_digest="fixture"),),
                        sources=(SourceHash(path="fixture.py", sha256="0" * 64),),
                        bootstrap=BootstrapConfig(replicates=16, seed=7))
    rows = []
    for case in cases:
        for policy in POLICIES:
            complete = case.scenario.seed == 0
            early = policy.name == "fixed"
            delay = -2 if early else 2
            calibration = CalibrationRecord(enabled=policy.calibration, valid=complete, reason="valid" if complete else "missing_first",
                                            estimates=int(complete), estimate=100.0 if complete else None)
            rows.append(EpisodeResult(
                scenario_id=case.scenario_id, scenario_digest=case.digest, seed=case.scenario.seed,
                regime=case.regime.value, policy=policy.name, declared_tick=10 + delay if complete else None,
                true_recovered_tick=10, declaration_delay=delay if complete else None,
                false_completion=early and complete, timeout=not complete, unsafe_restore=early and not complete,
                restoration_tick=9, command_count=2 if complete else 4,
                overload_area=(4.0 if complete else 8.0) if early else (3.0 if complete else 5.0),
                calibration=calibration, trace=(), trace_omitted=0,
            ))
    return ReportData(manifest, build_payload(manifest, Split.DEV, tuple(rows)))


def test_report_numbers(data: ReportData) -> None:
    # Given: 조기 선언, 미선언, unsafe restore, 결측 지연이 있는 고정 입력.
    # When: 한국어 보고서를 렌더링한다.
    report = render_report(data)
    # Then: 실패 분모·부호·결측·평균·짝지은 차이가 실제 입력과 같다.
    assert "| `fixed` | 50.0% (6/12) | 50.0% (6/12) | 50.0% (6/12) | -2.000 (n=6, 누락=6) | 6.000 | 3.000 |" in report
    assert "| all | `fixed` | `verified` | declaration_delay | -4.0000 | [-4.0000, -4.0000] | 1 | 6/12 | 6 |" in report
    assert "new_from_spec_snippet" in report and "논문 수치를 재현한 결과가 아니다" in report
    for policy in data.payload.policy_names:
        assert f"`{policy}`" in report
    for regime in REGIMES:
        assert f"### {regime.value}" in report
    assert "missing_first" in report


def test_invalid_records(data: ReportData, directory: Path) -> None:
    # Given: 원본 manifest와 누락·중복·seed 불일치·잘못된 요약 입력.
    write_json(directory / "manifest.json", data.manifest)
    variants = (
        data.payload.model_copy(update={"episodes": tuple(row for row in data.payload.episodes if row.policy != "fixed")}),
        data.payload.model_copy(update={"episodes": (*data.payload.episodes, data.payload.episodes[0])}),
        data.payload.model_copy(update={"episodes": (data.payload.episodes[0].model_copy(update={"seed": 999}), *data.payload.episodes[1:])}),
        data.payload.model_copy(update={"summaries": ()}),
    )
    for index, invalid in enumerate(variants):
        path = directory / f"invalid{index}.json"
        write_json(path, invalid)
        # When: 잘못된 평가 기록을 읽는다.
        try:
            load_data(path)
        except EvaluationError as error:
            assert str(error)
        else:
            # Then: 정상 보고서로 받아들이지 않는다.
            raise AssertionError(f"잘못된 입력이 허용됨: {index}")


def test_cli_outputs(data: ReportData, directory: Path) -> None:
    # Given: 저장된 작은 fixture와 프로젝트 밖 작업 디렉터리.
    input_path = directory / "dev.json"
    write_json(input_path, data.payload)
    report_path = directory / "FINDINGS.md"
    output_dir = directory / "plots"
    commands = (
        ("research_cycle_report.py", "--output", str(report_path)),
        ("research_findings_plot.py", "--output-dir", str(output_dir)),
    )
    for filename, output_flag, output_value in commands:
        # When: 실제 CLI를 실행한다.
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / filename), "--input", str(input_path), output_flag, output_value],
                                cwd=directory, check=False, capture_output=True, encoding="utf-8")
        # Then: 각 형식 산출물이 생성된다.
        assert result.returncode == 0, result.stderr
        assert "생성" in result.stdout and result.stderr == ""
    assert "50.0% (6/12)" in report_path.read_text(encoding="utf-8")
    svg_path = output_dir / f"{FIGURE_NAME}.svg"
    assert ElementTree.parse(svg_path).getroot().tag.endswith("svg")
    svg = svg_path.read_text(encoding="utf-8")
    assert all(name in svg for name in data.payload.policy_names)
    assert "source-sha256" in svg and "protocol" in svg and "seeds 2" in svg
    assert (output_dir / f"{FIGURE_NAME}.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    # Given: 이미 생성한 도판 경로.
    # When: 같은 경로로 다시 저장하려 한다.
    try:
        save_figure(data, output_dir)
    except EvaluationError as error:
        assert "already exists" in str(error)
    else:
        # Then: 기존 산출물 덮어쓰기를 거부한다.
        raise AssertionError("기존 도판 덮어쓰기가 허용됨")


def test_plot_axes(data: ReportData) -> None:
    # Given: 전체 9정책의 고정 측정값.
    # When: 도판을 구성한다.
    figure = build_figure(data)
    # Then: 별도 축 세 개, 전체 정책 라벨, ASCII 텍스트를 제공한다.
    assert len(figure.axes) == 3
    assert tuple(text.get_text() for text in figure.axes[0].get_yticklabels()) == data.payload.policy_names
    for axis in figure.axes:
        assert axis.get_xlabel().isascii()
        assert axis.get_title(loc="left").isascii()
    assert all(text.get_text().isascii() for text in figure.texts)
    import matplotlib.pyplot as plt
    plt.close(figure)


def test_missing_delays(data: ReportData) -> None:
    # Given: 모든 시행에서 완료 선언 시각이 없는 입력.
    rows = tuple(row.model_copy(update={"declared_tick": None, "declaration_delay": None,
                                       "false_completion": False, "timeout": True}) for row in data.payload.episodes)
    missing = ReportData(data.manifest, build_payload(data.manifest, Split.DEV, rows))
    # When: 도판을 구성한다.
    figure = build_figure(missing)
    # Then: 결측을 0 tick으로 그리지 않고 명시한다.
    assert sum(text.get_text() == "missing all times" for text in figure.axes[1].texts) == 9
    import matplotlib.pyplot as plt
    plt.close(figure)


def main() -> None:
    """실제 평가 뱅크 실행 없이 보고서 계약을 점검한다."""
    data = fixture()
    test_report_numbers(data)
    test_plot_axes(data)
    test_missing_delays(data)
    with TemporaryDirectory(prefix="research-reporting-") as directory:
        test_invalid_records(data, Path(directory))
        test_cli_outputs(data, Path(directory))
        for filename in ("research_cycle_report.py", "research_findings_plot.py"):
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "scripts" / filename), "--help"],
                cwd=directory, check=False, capture_output=True, encoding="utf-8",
            )
            # Then: 경로와 관계없이 각 CLI 진입점이 사용 가능하다.
            assert result.returncode == 0, result.stderr
            assert "--input" in result.stdout
    print("보고서·도판 자가 테스트 통과")


if __name__ == "__main__":
    main()
