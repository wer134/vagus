#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.10", "matplotlib>=3.10"]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# Run: uv run scripts/research_findings_plot.py --input RUN/test.json --output-dir RUN/artifacts
# ──────────────────
"""모든 정책의 실패율·선언 지연·과부하를 별도 축으로 표시한다."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Final

import matplotlib
matplotlib.use("Agg")
from matplotlib.axes import Axes
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from pydantic import BaseModel, ConfigDict, ValidationError

from research_cycle_report import ROOT, ReportData, load_data
from recovery_research.aggregation import summarize
from recovery_research.artifacts import MetricName
from recovery_research.manifest import EvaluationError

sys.path.insert(0, str(ROOT / "experiments/figures"))
from figspec import assert_ascii
from palette import CATEGORICAL, INK, SURFACE

FIGURE_NAME: Final = "recovery_research_comparison"


class PlotOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input: Path
    output_dir: Path


def style_axis(axis: Axes) -> None:
    """기존 도판의 표면·잉크·축 규약을 적용한다."""
    axis.set_facecolor(SURFACE["light"])
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(INK["light"]["axis"])
    axis.tick_params(colors=INK["light"]["secondary"], labelsize=9)
    axis.grid(axis="x", color=INK["light"]["axis"], alpha=0.3)
    axis.set_axisbelow(True)
    axis.set_ylim(8.6, -0.6)


def build_figure(data: ReportData) -> Figure:
    """기술 평균을 표시하며 결측 선언 지연은 수치로 대체하지 않는다."""
    names = data.payload.policy_names
    positions = tuple(range(len(names)))
    figure = plt.figure(figsize=(18, 8), facecolor=SURFACE["light"])
    failure, delay, overload = tuple(figure.add_subplot(1, 3, index) for index in range(1, 4))
    for axis in (failure, delay, overload):
        style_axis(axis)
        axis.set_yticks(positions, names if axis is failure else [""] * len(names))
    series = (
        (MetricName.FALSE_COMPLETION, "False completion"),
        (MetricName.UNSAFE_RESTORE, "Unsafe restore"),
        (MetricName.TIMEOUT, "Timeout"),
    )
    for series_index, (metric, label) in enumerate(series):
        values = []
        for name in names:
            rows = tuple(row for row in data.payload.episodes if row.policy == name)
            mean = summarize(rows, metric).mean
            if mean is None:
                raise EvaluationError("failure rates require observed values")
            values.append(mean * 100)
        offset = (series_index - 1) * 0.23
        failure.barh([position + offset for position in positions], values, height=0.21,
                     label=label, color=CATEGORICAL["light"][series_index])
    failure.set_title("A  Safety and completion failures", loc="left", fontsize=12)
    failure.set_xlabel("Episodes (%) - all episodes in denominator")
    failure.set_xlim(0, 106)
    failure.legend(loc="upper left", bbox_to_anchor=(0, 1.14), frameon=False, fontsize=8)
    delay.set_title("B  Declaration delay", loc="left", fontsize=12)
    delay.set_xlabel("Declared tick - true recovery tick")
    delay.axvline(0, color=INK["light"]["axis"], linestyle="--", linewidth=1)
    overload.set_title("C  Overload area", loc="left", fontsize=12)
    overload.set_xlabel("Sum of excess link utilization x tick")
    for position, name in zip(positions, names, strict=True):
        rows = tuple(row for row in data.payload.episodes if row.policy == name)
        time = summarize(rows, MetricName.DECLARATION_DELAY)
        if time.mean is None:
            delay.text(0.03, position, "missing all times", transform=delay.get_yaxis_transform(), fontsize=8)
        else:
            delay.plot(time.mean, position, "o", color=CATEGORICAL["light"][0], markersize=6)
            delay.annotate(f"{time.mean:.1f} (n={time.observed}/{len(rows)})", (time.mean, position),
                           xytext=(8, 0), textcoords="offset points", va="center", fontsize=8)
        excess = summarize(rows, MetricName.OVERLOAD_AREA).mean
        if excess is None:
            raise EvaluationError("overload area requires observed values")
        overload.barh(position, excess, height=0.5, color=CATEGORICAL["light"][0])
        overload.annotate(f"{excess:.2f}", (excess, position), xytext=(5, 0),
                          textcoords="offset points", va="center", fontsize=8)
    delay.margins(x=0.65)
    overload.margins(x=0.18)
    figure.suptitle(f"Frozen recovery research / {data.payload.split.value} / all six regimes / all nine policies", fontsize=16, x=0.55)
    figure.text(0.35, 0.92, "Descriptive means; paired seed-cluster confidence intervals and all regime tables are in FINDINGS.md", fontsize=10)
    seeds = data.seeds
    footer = (f"source-sha256 {data.source_digest[:16]} | manifest {data.manifest.digest[:16]} | "
              f"seeds {len(seeds)} ({seeds[0]}..{seeds[-1]}) | protocol {data.manifest.contract}")
    assert_ascii(footer, *names)
    figure.text(0.01, 0.025, footer, fontsize=7, color=INK["light"]["secondary"])
    figure.subplots_adjust(left=0.18, right=0.98, bottom=0.13, top=0.80, wspace=0.25)
    return figure


def save_figure(data: ReportData, output_dir: Path) -> tuple[Path, Path]:
    """공유 figures/out를 사용하지 않고 새 지정 파일만 저장한다."""
    paths = (output_dir / f"{FIGURE_NAME}.svg", output_dir / f"{FIGURE_NAME}.png")
    if any(path.exists() for path in paths):
        raise EvaluationError("plot output already exists; choose a new output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    figure = build_figure(data)
    try:
        for path in paths:
            with path.open("xb") as stream:
                figure.savefig(stream, format=path.suffix[1:], dpi=140, facecolor=SURFACE["light"])
    finally:
        plt.close(figure)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="고정 평가 JSON에서 SVG·PNG 도판을 생성합니다.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    try:
        options = PlotOptions.model_validate(vars(parser.parse_args()))
        paths = save_figure(load_data(options.input), options.output_dir)
    except (OSError, ValidationError, EvaluationError) as error:
        parser.exit(2, f"도판 생성 실패: {error}\n")
    print(f"연구 도판 생성: {paths[0]} / {paths[1]}")


if __name__ == "__main__":
    main()
