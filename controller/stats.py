"""Pure statistical functions for experimental design, hypothesis testing, and drift detection.

All algorithms are deterministic, fully reproducible, and grounded in standard statistical
theory (power calculations, two-proportion z-tests, Holm-Bonferroni FWER control,
Pearson chi-square SRM tests, and Wilson confidence intervals).
"""
from __future__ import annotations

import math
from typing import Sequence

import scipy.stats as st


def required_sample_size(
    baseline_rate: float,
    mde: float = 0.08,
    alpha: float = 0.05,
    power: float = 0.80,
    min_floor: int = 150,
) -> int:
    """Calculates the minimum required sample size per arm for a two-proportion test.

    Formula:
      n = [(z_{alpha/2} * sqrt(2*p_bar*(1-p_bar)) + z_beta * sqrt(p1*(1-p1) + p2*(1-p2))) / (p2 - p1)]^2
    where:
      p1 = baseline_rate
      p2 = p1 * (1 + mde)
      p_bar = (p1 + p2) / 2
      z_{alpha/2} is standard normal quantile for significance alpha (default 1.96 for alpha=0.05)
      z_beta is normal quantile for statistical power (default 0.84 for power=0.80)
    """
    p1 = max(min(baseline_rate, 0.95), 0.05)
    p2 = max(min(p1 * (1.0 + mde), 0.95), 0.05)
    if abs(p2 - p1) < 1e-6:
        return min_floor

    z_alpha = st.norm.ppf(1.0 - alpha / 2.0)
    z_beta = st.norm.ppf(power)

    p_bar = (p1 + p2) / 2.0
    numerator = (
        z_alpha * math.sqrt(2.0 * p_bar * (1.0 - p_bar))
        + z_beta * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2))
    )
    denominator = p2 - p1
    n = math.ceil((numerator / denominator) ** 2)
    return max(n, min_floor)


def two_proportion_z_test(
    k1: int,
    n1: int,
    k2: int,
    n2: int,
    alternative: str = "two-sided",
) -> tuple[float, float, float, tuple[float, float]]:
    """Performs a two-proportion z-test comparing treatment (k2/n2) against control (k1/n1).

    Returns:
      (z_stat, p_value, effect_diff, (ci_lower, ci_upper))
    where:
      effect_diff = p2 - p1
      Confidence interval uses unpooled standard error.
      Hypothesis test z_stat uses pooled standard error under H0: p1 == p2.
    """
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0, 0.0, (0.0, 0.0)

    p1 = k1 / n1
    p2 = k2 / n2
    diff = p2 - p1

    # Pooled proportion for test statistic
    p_pool = (k1 + k2) / (n1 + n2)
    se_pool = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n2))

    if se_pool < 1e-12:
        return 0.0, 1.0, diff, (diff, diff)

    z = diff / se_pool

    if alternative == "two-sided":
        p_val = 2.0 * (1.0 - st.norm.cdf(abs(z)))
    elif alternative == "greater":
        p_val = 1.0 - st.norm.cdf(z)
    elif alternative == "less":
        p_val = st.norm.cdf(z)
    else:
        raise ValueError(f"Unknown alternative: {alternative}")

    # Unpooled standard error for 95% confidence interval
    se_unpool = math.sqrt((p1 * (1.0 - p1) / n1) + (p2 * (1.0 - p2) / n2))
    margin = 1.95996 * se_unpool
    ci = (diff - margin, diff + margin)

    return float(z), float(p_val), float(diff), ci


def holm_bonferroni_correction(p_values: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """Applies the Holm-Bonferroni step-down procedure to control Family-Wise Error Rate (FWER).

    Ensures FWER <= alpha across multiple candidate treatment arms.
    Returns a list of booleans corresponding to each hypothesis (True if significant).
    """
    m = len(p_values)
    if m == 0:
        return []

    # Sort p-values with original indices
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    significant = [False] * m

    for rank, (orig_idx, p_val) in enumerate(indexed):
        threshold = alpha / (m - rank)
        if p_val <= threshold:
            significant[orig_idx] = True
        else:
            # Step-down stops at first non-rejection
            break

    return significant


def check_sample_ratio_mismatch(
    observed: Sequence[int],
    weights: Sequence[float],
    alpha: float = 0.001,
) -> tuple[float, bool]:
    """Tests for Sample-Ratio Mismatch (SRM) using Pearson's chi-square goodness-of-fit.

    Returns (p_value, is_srm_detected).
    If p_value < alpha (default 0.001), SRM is present, indicating allocation distortion.
    """
    tot_obs = sum(observed)
    tot_w = sum(weights)
    if tot_obs == 0 or tot_w == 0 or len(observed) < 2:
        return 1.0, False

    expected = [tot_obs * (w / tot_w) for w in weights]
    if any(e < 5 for e in expected):
        # Sample size too small for reliable chi-square approximation
        return 1.0, False

    chi2_stat = sum(((o - e) ** 2) / e for o, e in zip(observed, expected))
    df = len(observed) - 1
    p_val = float(st.chi2.sf(chi2_stat, df))
    return p_val, bool(p_val < alpha)


def guardrail_veto_check(
    k_ctrl: int,
    n_ctrl: int,
    k_treat: int,
    n_treat: int,
    max_drop: float = 0.02,
) -> tuple[bool, float, tuple[float, float]]:
    """Evaluates whether treatment breaches the non-inferiority guardrail margin.

    Returns (is_vetoed, point_delta, (ci_lower, ci_upper)).
    Veto triggers if:
      1. Point estimate drop exceeds policy limit: delta < -max_drop.
      2. Statistically confident degradation: delta < -0.015 and p < 0.05.
    """
    if n_ctrl <= 0 or n_treat <= 0:
        return False, 0.0, (0.0, 0.0)

    p_ctrl = k_ctrl / n_ctrl
    p_treat = k_treat / n_treat
    delta = p_treat - p_ctrl

    se = math.sqrt((p_ctrl * (1.0 - p_ctrl) / n_ctrl) + (p_treat * (1.0 - p_treat) / n_treat))
    ci_lower = delta - 1.95996 * se
    ci_upper = delta + 1.95996 * se

    z, p_val_less, _, _ = two_proportion_z_test(k_ctrl, n_ctrl, k_treat, n_treat, alternative="less")
    is_vetoed = delta < -max_drop or (delta < -0.015 and p_val_less < 0.05)
    return is_vetoed, float(delta), (float(ci_lower), float(ci_upper))


def wilson_score_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Computes the Wilson score interval for a binomial proportion.

    Robust for small sample sizes and proportions near 0 or 1.
    """
    if n <= 0:
        return 0.0, 0.0
    z = st.norm.ppf(1.0 - (1.0 - confidence) / 2.0)
    p = k / n
    denom = 1.0 + (z**2) / n
    center = (p + (z**2) / (2.0 * n)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) / n) + (z**2) / (4.0 * n**2))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def cusum_drift_score(
    recent_k: int,
    recent_n: int,
    baseline_rate: float,
    slack: float = 0.015,
) -> float:
    """Computes a one-sided downward CUSUM score for performance degradation.

    Positive score indicates cumulative performance below baseline minus slack.
    """
    if recent_n <= 0:
        return 0.0
    recent_rate = recent_k / recent_n
    # Deviation below expected benchmark
    deviation = (baseline_rate - slack) - recent_rate
    return max(0.0, deviation * math.sqrt(recent_n))
