"""Unit tests for statistical methods: power, z-test, Holm-Bonferroni, SRM, and guardrails."""
from __future__ import annotations

import numpy as np
import pytest

from controller.stats import (
    check_sample_ratio_mismatch,
    cusum_drift_score,
    guardrail_veto_check,
    holm_bonferroni_correction,
    required_sample_size,
    two_proportion_z_test,
    wilson_score_interval,
)


def test_required_sample_size_monotonicity():
    """Sample size must increase as Minimum Detectable Effect (MDE) decreases."""
    n_large_mde = required_sample_size(baseline_rate=0.30, mde=0.15)
    n_small_mde = required_sample_size(baseline_rate=0.30, mde=0.05)
    assert n_small_mde > n_large_mde
    assert n_large_mde >= 150  # Enforces safe minimum floor


def test_two_proportion_z_test_significant_difference():
    """Identifies statistically significant difference with proper confidence interval."""
    # Control: 100/1000 (10%), Treatment: 150/1000 (15%)
    z, p_val, diff, (ci_low, ci_high) = two_proportion_z_test(
        k1=100, n1=1000, k2=150, n2=1000, alternative="two-sided"
    )
    assert diff == pytest.approx(0.05, abs=1e-4)
    assert z > 3.0
    assert p_val < 0.002
    assert ci_low > 0.02
    assert ci_high < 0.08


def test_two_proportion_z_test_null_difference():
    """Produces non-significant p-value when sample rates are identical."""
    z, p_val, diff, (ci_low, ci_high) = two_proportion_z_test(
        k1=200, n1=1000, k2=200, n2=1000, alternative="two-sided"
    )
    assert diff == 0.0
    assert abs(z) < 1e-6
    assert p_val == pytest.approx(1.0, abs=1e-4)
    assert ci_low < 0 < ci_high


def test_holm_bonferroni_step_down():
    """Holm-Bonferroni step-down rejects hypotheses in order until threshold breached."""
    # m = 4 hypotheses:
    # 0: p=0.005  (tested vs 0.05/4 = 0.0125 -> Reject)
    # 1: p=0.015  (tested vs 0.05/3 = 0.0167 -> Reject)
    # 2: p=0.035  (tested vs 0.05/2 = 0.0250 -> Accept, step-down stops)
    # 3: p=0.040  (tested vs 0.05/1 = 0.0500 -> Retained because previous failed)
    p_vals = [0.005, 0.015, 0.035, 0.040]
    sig = holm_bonferroni_correction(p_vals, alpha=0.05)
    assert sig == [True, True, False, False]


def test_sample_ratio_mismatch_check():
    """Detects distorted allocations and passes expected allocations."""
    # Balanced 50/50 allocation (500 vs 500)
    p_val_ok, is_srm_ok = check_sample_ratio_mismatch([500, 500], [0.5, 0.5])
    assert not is_srm_ok
    assert p_val_ok > 0.50

    # Distorted 350 vs 650 on 50/50 target weights
    p_val_bad, is_srm_bad = check_sample_ratio_mismatch([350, 650], [0.5, 0.5])
    assert is_srm_bad
    assert p_val_bad < 0.0001


def test_guardrail_veto_logic():
    """Triggers veto when guardrail KPI drops beyond allowed tolerance."""
    # Control guardrail: 300/1000 (30%)
    # Treatment guardrail: 260/1000 (26%) -> Drop = -4%, exceeds max_drop 2%
    is_vetoed, delta, (ci_low, ci_high) = guardrail_veto_check(
        k_ctrl=300, n_ctrl=1000, k_treat=260, n_treat=1000, max_drop=0.02
    )
    assert is_vetoed
    assert delta == pytest.approx(-0.04, abs=1e-4)

    # Mild change within guardrail tolerance: 295/1000 vs 300/1000 (-0.5%)
    is_vetoed_safe, delta_safe, _ = guardrail_veto_check(
        k_ctrl=300, n_ctrl=1000, k_treat=295, n_treat=1000, max_drop=0.02
    )
    assert not is_vetoed_safe
    assert delta_safe == pytest.approx(-0.005, abs=1e-4)


def test_statistical_determinism():
    """Same inputs must always produce identical statistical metrics and decisions."""
    res1 = two_proportion_z_test(145, 600, 180, 600)
    res2 = two_proportion_z_test(145, 600, 180, 600)
    assert res1 == res2


def test_cusum_drift_false_positive_rate_on_null_data():
    """Under true null hypothesis (no drift), detector false positive rate must remain <= 5%."""
    rng = np.random.default_rng(42)
    baseline_p = 0.30
    n_trials = 100
    batch_size = 500

    false_alarms = 0
    for _ in range(n_trials):
        # Draw from null binomial
        k = rng.binomial(batch_size, baseline_p)
        score = cusum_drift_score(recent_k=k, recent_n=batch_size, baseline_rate=baseline_p, slack=0.02)
        # Threshold for score
        if score > 2.5:
            false_alarms += 1

    fp_rate = false_alarms / n_trials
    assert fp_rate <= 0.05
