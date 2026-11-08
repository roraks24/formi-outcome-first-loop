"""Unit tests for statistical methods: power, z-test, Holm-Bonferroni, SRM, and guardrails."""
from __future__ import annotations

import numpy as np
import pytest

from controller.stats import (
    check_sample_ratio_mismatch,
    cluster_robust_two_proportion_test,
    compute_mde,
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


def test_compute_mde_dynamic():
    """Computes MDE dynamically from baseline rate, sample size, and power."""
    # Low volume workflow (n=300 per arm): requires large lift to detect
    mde_low = compute_mde(baseline_rate=0.50, sample_size_per_arm=300, alpha=0.05, power=0.80)
    # High volume workflow (n=5000 per arm): can detect subtle lifts
    mde_high = compute_mde(baseline_rate=0.50, sample_size_per_arm=5000, alpha=0.05, power=0.80)

    assert mde_low > 0.15  # ~23% MDE
    assert mde_high < 0.06  # ~5.6% MDE
    assert mde_low > mde_high


def test_lead_clustered_estimator_anti_conservative_naive():
    """Proves naive binomial z-test is anti-conservative on clustered multi-dial leads,
    while cluster_robust_two_proportion_test maintains nominal error control under H0.
    """
    rng = np.random.default_rng(42)
    n_sims = 200
    leads_per_arm = 120
    dials_per_lead = 5

    naive_rejections = 0
    cluster_rejections = 0

    for _ in range(n_sims):
        # Realistic voice calling mixture: responsive leads (p=0.70) vs unresponsive leads (p=0.05)
        # creates strong intra-cluster correlation across multi-dial retry cadences.
        p1 = rng.choice([0.70, 0.05], size=leads_per_arm, p=[0.40, 0.60])
        p2 = rng.choice([0.70, 0.05], size=leads_per_arm, p=[0.40, 0.60])

        arm1_leads = [(int(rng.binomial(dials_per_lead, p)), dials_per_lead) for p in p1]
        arm2_leads = [(int(rng.binomial(dials_per_lead, p)), dials_per_lead) for p in p2]

        # Naive two-proportion z-test (treats all dials as independent Bernoulli trials)
        y1_tot = sum(l[0] for l in arm1_leads)
        n1_tot = sum(l[1] for l in arm1_leads)
        y2_tot = sum(l[0] for l in arm2_leads)
        n2_tot = sum(l[1] for l in arm2_leads)

        _, p_naive, _, _ = two_proportion_z_test(y1_tot, n1_tot, y2_tot, n2_tot)
        if p_naive < 0.05:
            naive_rejections += 1

        # Cluster-robust two-proportion test
        _, p_clustered, _, _ = cluster_robust_two_proportion_test(arm1_leads, arm2_leads)
        if p_clustered < 0.05:
            cluster_rejections += 1

    naive_fpr = naive_rejections / n_sims
    cluster_fpr = cluster_rejections / n_sims

    # Naive test suffers severe variance deflation from intra-cluster correlation (FPR >= 18%)
    # Cluster-robust test properly controls FPR near nominal 5% (FPR <= 8%)
    assert naive_fpr >= 0.18, f"Naive FPR {naive_fpr} was not anti-conservative"
    assert cluster_fpr <= 0.08, f"Cluster-robust FPR {cluster_fpr} exceeded nominal threshold"

