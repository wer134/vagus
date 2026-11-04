"""누락 없는 정책별 기술 통계와 seed 단위 짝지은 비교."""

from collections.abc import Sequence
from math import fsum
from typing import Final, assert_never

from recovery_research.artifacts import METRICS, EpisodeResult, ExperimentPayload, MetricName, MetricSummary, PolicyComparison, PolicySummary, Split
from recovery_research.manifest import EvaluationError, Manifest
from recovery_research.statistics import PairValue, paired_estimate

BASELINES: Final = ("verified", "calibrated_hold10")


def metric_value(episode: EpisodeResult, metric: MetricName) -> float | None:
    match metric:
        case MetricName.FALSE_COMPLETION:
            return float(episode.false_completion)
        case MetricName.TIMEOUT:
            return float(episode.timeout)
        case MetricName.UNSAFE_RESTORE:
            return float(episode.unsafe_restore)
        case MetricName.DECLARATION_DELAY:
            return episode.declaration_delay
        case MetricName.TRUE_RECOVERED_TICK:
            return episode.true_recovered_tick
        case MetricName.RESTORATION_TICK:
            return episode.restoration_tick
        case MetricName.COMMAND_COUNT:
            return episode.command_count
        case MetricName.OVERLOAD_AREA:
            return episode.overload_area
        case unreachable:
            assert_never(unreachable)


def summarize(episodes: Sequence[EpisodeResult], metric: MetricName) -> MetricSummary:
    values = tuple(value for row in episodes if (value := metric_value(row, metric)) is not None)
    return MetricSummary(name=metric, mean=fsum(values) / len(values) if values else None, observed=len(values), missing=len(episodes) - len(values))


def build_payload(manifest: Manifest, split: Split, episodes: tuple[EpisodeResult, ...]) -> ExperimentPayload:
    """동결된 모든 scenario-policy 쌍을 한 번씩 포함해야 집계한다."""
    cases = manifest.bank(split).cases
    names = tuple(spec.name for spec in manifest.policies)
    expected = {(case.scenario_id, name) for case in cases for name in names}
    actual = {(row.scenario_id, row.policy) for row in episodes}
    if expected != actual or len(actual) != len(episodes):
        raise EvaluationError("incomplete or duplicate scenario-policy comparisons")
    by_id = {case.scenario_id: case for case in cases}
    for row in episodes:
        case = by_id[row.scenario_id]
        if row.seed != case.scenario.seed or row.regime != case.regime.value or row.scenario_digest != case.digest:
            raise EvaluationError("episode does not match the frozen scenario")
    regimes = ("all", *sorted({case.regime.value for case in cases}))
    summaries: list[PolicySummary] = []
    comparisons: list[PolicyComparison] = []
    for regime in regimes:
        selected = tuple(row for row in episodes if regime == "all" or row.regime == regime)
        by_policy = {name: tuple(row for row in selected if row.policy == name) for name in names}
        for name in names:
            rows = by_policy[name]
            summaries.append(PolicySummary(policy=name, regime=regime, episode_count=len(rows), metrics=tuple(summarize(rows, metric) for metric in METRICS)))
            for baseline in BASELINES:
                if name == baseline:
                    continue
                for metric in METRICS:
                    candidate = tuple(PairValue(row.seed, row.scenario_id, metric_value(row, metric)) for row in rows)
                    reference = tuple(PairValue(row.seed, row.scenario_id, metric_value(row, metric)) for row in by_policy[baseline])
                    comparisons.append(PolicyComparison(candidate=name, baseline=baseline, regime=regime, metric=metric, estimate=paired_estimate(candidate, reference, manifest.bootstrap)))
    return ExperimentPayload(manifest_digest=manifest.digest, split=split, scenario_count=len(cases), policy_names=names, episodes=episodes, summaries=tuple(summaries), comparisons=tuple(comparisons))
