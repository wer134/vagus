"""Paired uncertainty with independent exogenous seeds as bootstrap clusters."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import floor, fsum, isfinite
from random import Random
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class PairValue:
    seed: int
    scenario_id: str
    value: float | None


class PairingError(ValueError):
    """A paired estimate cannot be formed from the supplied comparison."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    replicates: int = 2000
    seed: int = 6301

    def __post_init__(self) -> None:
        if self.replicates < 1:
            raise PairingError("bootstrap replicates must be positive")


DEFAULT_BOOTSTRAP: Final = BootstrapConfig()


class PairedEstimate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mean_difference: float | None
    ci_low: float | None
    ci_high: float | None
    seed_count: int
    paired_episodes: int
    missing_pairs: int
    total_pairs: int
    confidence: float = 0.95
    method: Literal["paired_seed_cluster_percentile"] = "paired_seed_cluster_percentile"


def _percentile(samples: Sequence[float], fraction: float) -> float:
    """Use linearly interpolated empirical percentiles on sorted samples."""
    position = (len(samples) - 1) * fraction
    left = floor(position)
    right = min(left + 1, len(samples) - 1)
    return samples[left] + (samples[right] - samples[left]) * (position - left)


def paired_estimate(
    candidate: Sequence[PairValue],
    baseline: Sequence[PairValue],
    config: BootstrapConfig = DEFAULT_BOOTSTRAP,
) -> PairedEstimate:
    """Estimate candidate minus baseline, with equal weight per available seed.

    Missing times are excluded only from this conditional time comparison; counts
    remain explicit. A singleton cluster has a point interval, not evidence of
    population certainty. Callers should report seed_count alongside the interval.
    """
    candidate_by_id = {row.scenario_id: row for row in candidate}
    baseline_by_id = {row.scenario_id: row for row in baseline}
    if len(candidate_by_id) != len(candidate) or len(baseline_by_id) != len(baseline):
        raise PairingError("duplicate scenario IDs in paired comparison")
    if candidate_by_id.keys() != baseline_by_id.keys():
        raise PairingError("candidate and baseline scenario IDs differ")
    differences: dict[int, list[float]] = {}
    paired_episodes = 0
    for scenario_id in sorted(candidate_by_id):
        proposed = candidate_by_id[scenario_id]
        reference = baseline_by_id[scenario_id]
        if proposed.seed != reference.seed:
            raise PairingError(f"seed differs for scenario {scenario_id}")
        if proposed.value is None or reference.value is None:
            continue
        if not isfinite(proposed.value) or not isfinite(reference.value):
            raise PairingError(f"non-finite metric for scenario {scenario_id}")
        differences.setdefault(proposed.seed, []).append(proposed.value - reference.value)
        paired_episodes += 1
    cluster_means = tuple(
        fsum(differences[seed]) / len(differences[seed]) for seed in sorted(differences)
    )
    total_pairs = len(candidate)
    mean_difference: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    if cluster_means:
        count = len(cluster_means)
        mean_difference = fsum(cluster_means) / count
        rng = Random(config.seed)
        samples = sorted(
            fsum(rng.choices(cluster_means, k=count)) / count
            for _ in range(config.replicates)
        )
        ci_low = _percentile(samples, 0.025)
        ci_high = _percentile(samples, 0.975)
    return PairedEstimate(
        mean_difference=mean_difference,
        ci_low=ci_low,
        ci_high=ci_high,
        seed_count=len(cluster_means),
        paired_episodes=paired_episodes,
        missing_pairs=total_pairs - paired_episodes,
        total_pairs=total_pairs,
    )
