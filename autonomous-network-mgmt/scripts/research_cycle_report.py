#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.10"]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# Run: uv run scripts/research_cycle_report.py --input RUN/test.json --output RUN/FINDINGS.md
# ──────────────────
"""고정 manifest와 평가 기록으로 한국어 연구 결과를 생성한다."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import sys
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from recovery_research.artifacts import ExperimentPayload, MetricName, MetricSummary
from recovery_research.aggregation import build_payload, summarize
from recovery_research.manifest import EvaluationError, Manifest, load_manifest
from recovery_research.policies import POLICIES
from recovery_research.scenarios import REGIMES

MAIN_METRICS: Final = (
    MetricName.FALSE_COMPLETION, MetricName.UNSAFE_RESTORE, MetricName.TIMEOUT,
    MetricName.DECLARATION_DELAY, MetricName.OVERLOAD_AREA, MetricName.COMMAND_COUNT,
)


@dataclass(frozen=True, slots=True)
class ReportData:
    manifest: Manifest
    payload: ExperimentPayload

    @property
    def source_digest(self) -> str:
        content = "\n".join(f"{source.path}:{source.sha256}" for source in self.manifest.sources)
        return sha256(content.encode()).hexdigest()

    @property
    def seeds(self) -> tuple[int, ...]:
        return tuple(sorted({row.seed for row in self.payload.episodes}))


class ReportOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input: Path
    output: Path


def load_data(input_path: Path) -> ReportData:
    """미완전하거나 다른 manifest에 속하는 입력은 집계 전에 거부한다."""
    manifest = load_manifest(input_path.with_name("manifest.json"))
    payload = ExperimentPayload.model_validate_json(input_path.read_text(encoding="utf-8"))
    policies = tuple(policy.name for policy in manifest.policies)
    if policies != tuple(policy.name for policy in POLICIES) or payload.policy_names != policies:
        raise EvaluationError("all nine preregistered policies are required")
    if payload.manifest_digest != manifest.digest:
        raise EvaluationError("evaluation and manifest digests differ")
    cases = manifest.bank(payload.split).cases
    if {case.regime for case in cases} != set(REGIMES):
        raise EvaluationError("all six preregistered regimes are required")
    if payload != build_payload(manifest, payload.split, payload.episodes):
        raise EvaluationError("stored summaries or paired comparisons differ from raw records")
    if manifest.implementation_origin != "new_from_spec_snippet":
        raise EvaluationError("unexpected implementation provenance")
    return ReportData(manifest, payload)


def format_metric(summary: MetricSummary) -> str:
    if summary.mean is None:
        return f"미관측 (n=0, 누락={summary.missing})"
    match summary.name:
        case MetricName.FALSE_COMPLETION | MetricName.UNSAFE_RESTORE | MetricName.TIMEOUT:
            return f"{100 * summary.mean:.1f}% ({round(summary.mean * summary.observed)}/{summary.observed})"
        case MetricName.DECLARATION_DELAY | MetricName.TRUE_RECOVERED_TICK | MetricName.RESTORATION_TICK:
            return f"{summary.mean:.3f} (n={summary.observed}, 누락={summary.missing})"
        case MetricName.OVERLOAD_AREA | MetricName.COMMAND_COUNT:
            return f"{summary.mean:.3f}"
        case unreachable:
            assert_never(unreachable)


def render_report(data: ReportData) -> str:
    """모든 정책·환경·실패를 포함하는 수치 보고서를 렌더링한다."""
    payload, manifest = data.payload, data.manifest
    lines = ["# 복구 완료·원상복귀 제어 연구 결과", "",
        f"이 보고서는 `{payload.split.value}.json`의 {len(payload.episodes)}개 시행을 원시 기록에서 집계했다. "
        f"정책 {len(payload.policy_names)}개, 환경 {len(REGIMES)}개, 독립 seed {len(data.seeds)}개를 모두 포함한다.", "",
        "## 연구 범위와 출처", "",
        "- 구현 출처: `new_from_spec_snippet`. 제공 명세·코드 조각을 바탕으로 새로 구현한 합성 실험이다.",
        "- 관련 문헌은 설계 비교·조사 근거이며, 원저자의 코드나 데이터로 논문 수치를 재현한 결과가 아니다.",
        "- 실제 장비 성능, 표준 전체 준수, 신규성 또는 모든 환경에서의 우월성을 입증하지 않는다.",
        f"- manifest SHA-256: `{manifest.digest}`", f"- source-set SHA-256: `{data.source_digest}`",
        f"- 고정 계약: `{manifest.contract}`", f"- seed: `{', '.join(map(str, data.seeds))}`", "",
        "## 지표 해석", "",
        "false_completion·unsafe_restore·timeout 비율의 분모는 해당 정책의 전체 시행이다. "
        "`declaration_delay = declared_tick - true_recovered_tick`이며 음수는 조기 선언을 뜻한다. "
        "두 시각 중 하나라도 없으면 결측으로 유지한다. 지연 평균은 두 시각이 관측된 시행에만 적용되며 "
        "timeout을 0으로 치환하지 않는다. 지연만 낮다는 이유로 정책이 우수하다고 판단할 수 없다.", "",
        "overload_area는 관측 종료까지 각 링크의 `max(utilization - 1, 0)`을 모두 합한 tick 적분이다. "
        "command_count는 명령 횟수이며 완료 선언 이후도 정해진 horizon까지 추적한다.", "",
        "## 전체 및 환경별 측정값", ""]
    for regime in ("all", *(regime.value for regime in REGIMES)):
        lines.extend([f"### {regime}", "", "| 정책 | false_completion | unsafe_restore | timeout | 선언 지연 (tick) | overload_area | 명령 수 |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for policy in payload.policy_names:
            rows = tuple(row for row in payload.episodes if row.policy == policy and (regime == "all" or row.regime == regime))
            cells = (format_metric(summarize(rows, metric)) for metric in MAIN_METRICS)
            lines.append(f"| `{policy}` | " + " | ".join(cells) + " |")
        lines.append("")
    lines.extend(["## 보정 유효성과 실패 공개", "",
                  "보정을 사용하는 정책에서 유효성 실패를 환경·사유별로 모두 집계했다. 무효 보정은 제어기의 fallback 경로로 남는다.", "",
                  "| 환경 | 정책 | 무효 보정 사유 | 시행 수 |", "| --- | --- | --- | ---: |"])
    calibrated = {policy.name for policy in manifest.policies if policy.calibration}
    invalid = Counter((row.regime, row.policy, row.calibration.reason) for row in payload.episodes
                      if row.policy in calibrated and not row.calibration.valid)
    lines.extend(f"| {regime} | `{policy}` | `{reason}` | {count} |" for (regime, policy, reason), count in sorted(invalid.items()))
    if not invalid:
        lines.append("| 전체 | 전체 보정 정책 | 무효 보정 없음 | 0 |")
    lines.extend(["", "## 짝지은 차이와 95% 구간", "",
        f"차이는 candidate − baseline이다. 실패율 차이는 0~1 비율 단위이며 −0.10은 −10%p이다. bootstrap {manifest.bootstrap.replicates}회, "
        f"재표집 seed {manifest.bootstrap.seed}를 사용한 `paired_seed_cluster_percentile` 구간이다. "
        "같은 독립 seed의 환경들을 하나의 cluster로 재표집한다. 구간은 다중 비교 보정이 없으며 탐색적이다. "
        "지연의 음수 차이가 안전성 개선을 뜻하지는 않는다. 결측 쌍·실패를 함께 공개한다. "
        "독립 seed가 하나이면 점구간은 모집단 불확실성이 없다는 증거가 아니다.", "",
        "| 환경 | candidate | baseline | 지표 | 평균 차이 | 95% 구간 | seed | 유효 쌍 / 전체 | 누락 쌍 |",
        "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: |"])
    for comparison in payload.comparisons:
        estimate = comparison.estimate
        value = "미관측" if estimate.mean_difference is None else f"{estimate.mean_difference:.4f}"
        interval = "미관측" if estimate.ci_low is None or estimate.ci_high is None else f"[{estimate.ci_low:.4f}, {estimate.ci_high:.4f}]"
        lines.append(f"| {comparison.regime} | `{comparison.candidate}` | `{comparison.baseline}` | {comparison.metric.value} | "
                     f"{value} | {interval} | {estimate.seed_count} | {estimate.paired_episodes}/{estimate.total_pairs} | {estimate.missing_pairs} |")
    lines.extend(["", "## 해석의 한계", "",
        "표의 전체 평균과 각 환경 결과를 함께 해석해야 한다. 실패·timeout·보정 무효 조건을 제외해 승자를 선택하지 않았다. "
        "테스트 결과를 보고 정책이나 임계값을 다시 선택하면 이 동결 평가와 구분되는 새 실험이 필요하다.", "",
        "도판은 `research_findings_plot.py --input <평가.json> --output-dir <새 디렉터리>`로 생성한다. "
        "세 패널의 축 단위는 각각 실패율(%), 선언 지연(tick), 과부하 적분이며 이중 축을 사용하지 않는다.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="고정 평가 JSON에서 한국어 FINDINGS를 생성합니다.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    try:
        options = ReportOptions.model_validate(vars(parser.parse_args()))
        data = load_data(options.input)
        options.output.parent.mkdir(parents=True, exist_ok=True)
        with options.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(render_report(data))
    except (OSError, ValidationError, EvaluationError) as error:
        parser.exit(2, f"보고서 생성 실패: {error}\n")
    print(f"연구 보고서 생성: {options.output}")


if __name__ == "__main__":
    main()
