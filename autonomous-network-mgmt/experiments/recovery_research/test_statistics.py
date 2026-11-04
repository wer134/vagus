"""Run with: python -m recovery_research.test_statistics."""

from recovery_research.statistics import (
    BootstrapConfig,
    PairingError,
    PairValue,
    paired_estimate,
)


def test_difference_when_seed_clusters_have_unequal_sizes() -> None:
    # Given: seed 1 has three regimes while seed 2 has one.
    candidate = (
        PairValue(1, "1:a", 2.0),
        PairValue(1, "1:b", 2.0),
        PairValue(1, "1:c", 2.0),
        PairValue(2, "2:a", 8.0),
    )
    baseline = tuple(PairValue(row.seed, row.scenario_id, 0.0) for row in candidate)
    # When: paired uncertainty is computed at the independent seed level.
    result = paired_estimate(candidate, baseline)
    # Then: the estimate weights two seeds equally, not four episodes equally.
    assert result.mean_difference == 5.0
    assert result.ci_low == 2.0
    assert result.ci_high == 8.0
    assert result.seed_count == 2


def test_nulls_when_one_policy_does_not_complete() -> None:
    # Given: only one of three time pairs is complete.
    candidate = (PairValue(1, "1:a", None), PairValue(1, "1:b", 9), PairValue(2, "2:a", 5))
    baseline = (PairValue(1, "1:a", 4), PairValue(1, "1:b", 6), PairValue(2, "2:a", None))
    # When: nullable times are compared without assigning timeout sentinels.
    result = paired_estimate(candidate, baseline)
    # Then: the conditional estimate and excluded observations stay distinguishable.
    assert (result.mean_difference, result.ci_low, result.ci_high) == (3, 3, 3)
    assert (result.total_pairs, result.paired_episodes, result.missing_pairs) == (3, 1, 2)
    assert result.seed_count == 1


def test_empty_estimate_when_every_time_pair_is_missing() -> None:
    # Given: every candidate episode timed out.
    candidate = (PairValue(1, "1:a", None), PairValue(2, "2:a", None))
    baseline = (PairValue(1, "1:a", 4), PairValue(2, "2:a", 6))
    # When: the conditional time comparison is requested.
    result = paired_estimate(candidate, baseline)
    # Then: no artificial numeric recovery time or confidence interval is created.
    assert (result.mean_difference, result.ci_low, result.ci_high) == (None, None, None)
    assert (result.total_pairs, result.missing_pairs, result.seed_count) == (2, 2, 0)


def test_reproducibility_when_input_order_changes() -> None:
    # Given: the same paired measurements in a different order.
    candidate = tuple(PairValue(seed, f"{seed}:a", float(seed * seed)) for seed in range(8))
    baseline = tuple(PairValue(seed, f"{seed}:a", float(seed)) for seed in range(8))
    config = BootstrapConfig(replicates=400, seed=91)
    expected = paired_estimate(candidate, baseline, config)
    # When: scenario order is reversed, keeping the seed schedule fixed.
    result = paired_estimate(tuple(reversed(candidate)), tuple(reversed(baseline)), config)
    # Then: payloads remain identical and the analytic mean is preserved.
    assert result == expected
    assert result.mean_difference == 14.0


def test_rejection_when_pairing_is_incomplete() -> None:
    # Given: an episode exists in only the candidate policy.
    candidate = (PairValue(1, "1:a", 2.0),)
    # When: a comparison omits its baseline episode.
    try:
        paired_estimate(candidate, ())
    except PairingError as error:
        # Then: an incomplete comparison is rejected rather than silently joined.
        assert error.reason == "candidate and baseline scenario IDs differ"
    else:
        raise AssertionError("incomplete comparison was accepted")


def test_rejection_when_scenario_seeds_differ() -> None:
    # Given: a reused scenario ID points to different exogenous seeds.
    candidate = (PairValue(1, "a", 2),)
    baseline = (PairValue(2, "a", 3),)
    # When: those records are paired.
    try:
        paired_estimate(candidate, baseline)
    except PairingError as error:
        # Then: scenario IDs cannot conceal mismatched seed schedules.
        assert error.reason == "seed differs for scenario a"
    else:
        raise AssertionError("mismatched seeds were accepted")


def test_rejection_when_scenario_ids_are_duplicated() -> None:
    # Given: one episode was recorded twice.
    duplicate = (PairValue(1, "a", 2), PairValue(1, "a", 2))
    # When: duplicate rows enter a comparison.
    try:
        paired_estimate(duplicate, duplicate)
    except PairingError as error:
        # Then: duplicated trials cannot create spurious precision.
        assert error.reason == "duplicate scenario IDs in paired comparison"
    else:
        raise AssertionError("duplicate scenarios were accepted")


def test_rejection_when_metric_is_non_finite() -> None:
    # Given: a numeric metric is invalid rather than explicitly missing.
    candidate = (PairValue(1, "a", float("nan")),)
    baseline = (PairValue(1, "a", 3),)
    # When: an estimate is attempted.
    try:
        paired_estimate(candidate, baseline)
    except PairingError as error:
        # Then: NaN cannot silently contaminate the result payload.
        assert error.reason == "non-finite metric for scenario a"
    else:
        raise AssertionError("non-finite metric was accepted")


if __name__ == "__main__":
    test_difference_when_seed_clusters_have_unequal_sizes()
    test_nulls_when_one_policy_does_not_complete()
    test_empty_estimate_when_every_time_pair_is_missing()
    test_reproducibility_when_input_order_changes()
    test_rejection_when_pairing_is_incomplete()
    test_rejection_when_scenario_seeds_differ()
    test_rejection_when_scenario_ids_are_duplicated()
    test_rejection_when_metric_is_non_finite()
    print("통계 자체검증 통과")
