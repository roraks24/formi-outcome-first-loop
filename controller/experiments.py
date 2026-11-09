"""Experiment lifecycle management: design, allocation, analysis, promotion, and decay rollback.

Enforces:
  1. Lead-level randomization and lead-level analysis.
  2. Power-based sample size discipline (fixed horizon, no peeking).
  3. Multiple comparison control (Holm-Bonferroni across treatment arms).
  4. Non-inferiority guardrail veto.
  5. Sample-Ratio Mismatch (SRM) checks.
  6. Persistent holdout arm for decay detection and automated rollback (C7).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from .governance import GovernanceEngine, make_deterministic_key
from .stats import (
    check_sample_ratio_mismatch,
    compute_mde,
    guardrail_veto_check,
    holm_bonferroni_correction,
    required_sample_size,
    two_proportion_z_test,
)
from .store import Store


@dataclass
class Experiment:
    experiment_id: str
    workflow_id: str
    cohort_id: str
    dim: str
    control_arm: str
    arms: list[str]
    weights: dict[str, float]
    primary_kpi: str
    guardrail_kpi: str
    required_n_per_arm: int
    status: str
    created_minute: int
    applied_minute: int | None = None
    applied_version: int | None = None
    concluded_minute: int | None = None
    winning_arm: str | None = None


class ExperimentManager:
    """Oversees A/B experiment design, evaluation, winner promotion, and decay monitoring."""

    def __init__(self, store: Store, governance: GovernanceEngine):
        self.store = store
        self.governance = governance
        self._active_experiments: dict[str, Experiment] = {}
        self._load_active_experiments()

    def _load_active_experiments(self) -> None:
        """Restores active experiments from SQLite on startup."""
        self.restore_active_experiments()

    def restore_active_experiments(self) -> None:
        """Restores active experiments from SQLite on startup or post-crash."""
        cur = self.store._conn.cursor()
        cur.execute("SELECT * FROM experiments WHERE status IN ('RUNNING', 'PENDING_APPLY');")
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            arms = json.loads(r["arms_json"])
            wid = r["workflow_id"]
            dim = r["dim"]
            coh = r["cohort_id"]
            try:
                cur_cfg = self.governance.api.get_config(wid).get("config", {})
                alloc = (cur_cfg.get("allocations", {}).get(coh) or {}).get(dim) or cur_cfg.get("allocations", {}).get("default", {}).get(dim, {})
                weights = {a: float(alloc.get(a, 1.0 / len(arms))) for a in arms}
            except Exception:
                w = 1.0 / len(arms)
                weights = {a: w for a in arms}
            exp = Experiment(
                experiment_id=r["experiment_id"],
                workflow_id=r["workflow_id"],
                cohort_id=r["cohort_id"],
                dim=r["dim"],
                control_arm=r["control_arm"],
                arms=arms,
                weights=weights,
                primary_kpi=r["primary_kpi"],
                guardrail_kpi=r["guardrail_kpi"],
                required_n_per_arm=r["required_n_per_arm"],
                status=r["status"],
                created_minute=r["created_minute"],
                applied_minute=r["applied_minute"] if "applied_minute" in r.keys() else None,
                applied_version=r["applied_version"] if "applied_version" in r.keys() else None,
            )
            self._active_experiments[r["workflow_id"]] = exp

    def get_active_experiment(self, workflow_id: str) -> Experiment | None:
        return self._active_experiments.get(workflow_id)

    def design_and_launch(
        self,
        workflow_spec: dict[str, Any],
        dim: str,
        sim_minute: int,
        sim_ts: str,
        cohort_id: str = "default",
    ) -> bool:
        """Designs a multi-arm experiment for a dimension and submits via governance."""
        wid = workflow_spec["workflow_id"]
        # Block experiment launches if workflow has an open incident
        if self.store.has_open_incident(wid):
            return False

        # Check cooldown: no relaunch on the same (workflow, dim) after NO_EFFECT/INVALIDATED/HALTED
        if self.store.is_in_cooldown(wid, dim, sim_minute):
            return False

        # Concurrency cap: maximum 1 concurrent experiment per workflow
        if wid in self._active_experiments:
            return False

        # SQLite check for existing active experiment or pending proposal
        cur = self.store._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM proposals WHERE workflow_id = ? AND status IN ('SUBMITTED', 'PENDING');", (wid,))
        pending_props = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM experiments WHERE workflow_id = ? AND status IN ('RUNNING', 'PENDING_APPLY');", (wid,))
        running_exps = cur.fetchone()[0]
        cur.close()
        if pending_props > 0 or running_exps > 0:
            return False

        opts = workflow_spec["options"].get(dim, [])
        if len(opts) < 2:
            return False

        cur_cfg_resp = self.governance.api.get_config(wid)
        cur_cfg = cur_cfg_resp["config"]
        base_version = cur_cfg_resp["version"]

        # Current production variant serves as control arm
        current_alloc = (cur_cfg.get("allocations", {}).get(cohort_id) or {}).get(dim) or cur_cfg.get("allocations", {}).get("default", {}).get(dim, {})
        control_arm = max(current_alloc.items(), key=lambda x: x[1])[0] if current_alloc else opts[0]

        # Select 1 candidate treatment arm at a time that has not been retired
        candidate_treatments = [o for o in opts if o != control_arm and not self.store.is_candidate_retired(wid, dim, o)][:1]
        if not candidate_treatments:
            return False

        all_arms = [control_arm] + candidate_treatments

        # Determine primary KPI and baseline rate
        obj = workflow_spec.get("objective", {})
        primary_kpi = obj.get("primary", "engagement_rate")
        guardrail_kpi = "pickup_rate" if primary_kpi == "engagement_rate" else "engagement_rate"

        # Baseline rate estimate from events_agg
        cur = self.store._conn.cursor()
        cur.execute(
            """
            SELECT SUM(dials) AS dials, SUM(connected) AS connected, SUM(engaged) AS engaged
            FROM events_agg WHERE workflow_id = ?;
            """,
            (wid,),
        )
        row = cur.fetchone()
        cur.close()

        dials = int((row and row["dials"]) or 0)
        conn = int((row and row["connected"]) or 0)
        eng = int((row and row["engaged"]) or 0)

        if primary_kpi == "engagement_rate":
            baseline_p = (eng / conn) if conn > 0 else 0.50
        else:
            baseline_p = (conn / dials) if dials > 0 else 0.30

        # Dynamically compute MDE and required sample size from baseline rate, power and max duration
        daily_size = workflow_spec.get("daily_batch", {}).get("size")
        if not daily_size:
            dph = workflow_spec.get("dials_per_hour", 200)
            window = workflow_spec.get("dialling_window", [9, 21])
            daily_size = dph * (window[1] - window[0])

        # High volume: 10% treatment share (bounds worst-case -10% variant drop to -1.0%)
        # Lower volume: 20% treatment share
        treat_share = 0.10 if daily_size >= 2000 else 0.20
        if primary_kpi == "engagement_rate":
            pickup_est = (conn / dials) if dials > 0 else 0.30
            daily_eval_units = daily_size * pickup_est
        else:
            daily_eval_units = daily_size

        max_duration_days = 3.0
        max_achievable_n = max(50, int(daily_eval_units * treat_share * max_duration_days))

        # Dynamic MDE derived per experiment from available volume, baseline rate, and power
        mde = compute_mde(baseline_rate=baseline_p, sample_size_per_arm=max_achievable_n, alpha=0.05, power=0.80)
        required_n = required_sample_size(baseline_rate=baseline_p, mde=mde, alpha=0.05, power=0.80)

        # Sizing feasibility check: if required n cannot be reached within max horizon
        # or if volume is so small that MDE exceeds 35% (statistically infeasible)
        if max_achievable_n < 100 or mde > 0.35:
            infeas_id = make_deterministic_key(wid, f"exp_infeas_{dim}", {"mde": round(mde, 3)}, base_version)
            with self.store.transaction() as cur:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO experiments (
                        experiment_id, workflow_id, cohort_id, dim, control_arm,
                        arms_json, status, primary_kpi, guardrail_kpi,
                        required_n_per_arm, alpha, power, mde, created_minute, concluded_minute
                    ) VALUES (?, ?, ?, ?, ?, ?, 'INFEASIBLE', ?, ?, ?, 0.05, 0.80, ?, ?, ?);
                    """,
                    (
                        infeas_id, wid, cohort_id, dim, control_arm,
                        json.dumps(all_arms), primary_kpi, guardrail_kpi,
                        required_n, round(mde, 4), sim_minute, sim_minute,
                    ),
                )
            self.store.record_decision(
                decision_id=make_deterministic_key(wid, f"dec_infeasible_{dim}", {"mde": round(mde, 3)}, base_version),
                sim_minute=sim_minute,
                ts=sim_ts,
                workflow_id=wid,
                decision_type="EXPERIMENT_SKIPPED",
                inputs_ref={"experiment_id": infeas_id, "dimension": dim, "candidate": candidate_treatments[0]},
                numbers={
                    "baseline_rate": round(baseline_p, 4),
                    "daily_eval_units": round(daily_eval_units, 1),
                    "treat_share": treat_share,
                    "max_achievable_n": max_achievable_n,
                    "required_n": required_n,
                    "dynamic_mde": round(mde, 4),
                    "max_duration_days": max_duration_days,
                },
                policy_clause="statistical_integrity.mde_feasibility",
                outcome=f"infeasible, skipped: required_n={required_n} > achievable={max_achievable_n} at MDE={mde:.3f}",
            )
            return False

        # Allocate control and treatment arms
        treat_weight = round(treat_share, 4)
        ctrl_weight = round(1.0 - treat_weight, 4)
        alloc_weights = {control_arm: ctrl_weight, candidate_treatments[0]: treat_weight}

        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg.setdefault("allocations", {}).setdefault(cohort_id, {})[dim] = alloc_weights

        exp_id = make_deterministic_key(wid, f"exp_{dim}", proposed_cfg, base_version)
        change_summary = f"A/B test on {wid} ({dim}): {control_arm} vs {candidate_treatments[0]}"
        rationale = (
            f"Testing candidate variant {candidate_treatments[0]} against control {control_arm}. "
            f"Target sample size: n={required_n} per arm based on baseline {baseline_p:.3f} and dynamic MDE {mde:.3f}."
        )

        dry_run = {
            "experiment_id": exp_id,
            "dimension": dim,
            "arms": all_arms,
            "weights": alloc_weights,
            "required_n": required_n,
            "baseline_rate": round(baseline_p, 4),
            "primary_kpi": primary_kpi,
            "guardrail_kpi": guardrail_kpi,
        }

        # Pre-record experiment in store as PENDING_APPLY so crash before submit returns is fully resilient
        with self.store.transaction() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO experiments (
                    experiment_id, workflow_id, cohort_id, dim, control_arm,
                    arms_json, status, primary_kpi, guardrail_kpi,
                    required_n_per_arm, alpha, power, mde, created_minute, applied_minute
                ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_APPLY', ?, ?, ?, 0.05, 0.80, ?, ?, NULL);
                """,
                (
                    exp_id, wid, cohort_id, dim, control_arm,
                    json.dumps(all_arms), primary_kpi, guardrail_kpi,
                    required_n, round(mde, 4), sim_minute,
                ),
            )

        # Submit via governance
        prop_id = self.governance.submit_proposal(
            workflow_spec=workflow_spec,
            base_version=base_version,
            proposed_config=proposed_cfg,
            change_summary=change_summary,
            rationale=rationale,
            affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
            impact_score=0.25,
            dry_run=dry_run,
            sim_minute=sim_minute,
            sim_ts=sim_ts,
        )

        if prop_id:
            exp = Experiment(
                experiment_id=exp_id,
                workflow_id=wid,
                cohort_id=cohort_id,
                dim=dim,
                control_arm=control_arm,
                arms=all_arms,
                weights=alloc_weights,
                primary_kpi=primary_kpi,
                guardrail_kpi=guardrail_kpi,
                required_n_per_arm=required_n,
                status="PENDING_APPLY",
                created_minute=sim_minute,
                applied_minute=None,
            )
            self._active_experiments[wid] = exp
            return True
        else:
            # Proposal failed or was blocked; clean up pre-recorded experiment
            with self.store.transaction() as cur:
                cur.execute("DELETE FROM experiments WHERE experiment_id = ? AND status = 'PENDING_APPLY';", (exp_id,))
            return False

    def evaluate_and_promote(
        self,
        workflow_spec: dict[str, Any],
        sim_minute: int,
        sim_ts: str,
    ) -> bool:
        """Evaluates active experiment for a workflow; promotes winner or declares inconclusive."""
        wid = workflow_spec["workflow_id"]
        exp = self._active_experiments.get(wid)
        if not exp or exp.status != "RUNNING":
            return False

        dim_col = "welcome_message" if exp.dim == "welcome_message" else "voice"

        # Determine start hour from when the experiment was applied (exclude pre-apply traffic)
        applied_minute = exp.applied_minute if exp.applied_minute is not None else exp.created_minute
        exp_start_hour = (applied_minute // 60) + (1 if (applied_minute % 60) != 0 else 0)
        cur = self.store._conn.cursor()
        if exp.applied_version is not None:
            cur.execute(
                f"""
                SELECT
                    {dim_col} AS arm,
                    SUM(dials) AS dials,
                    SUM(connected) AS connected,
                    SUM(engaged) AS engaged
                FROM events_agg
                WHERE workflow_id = ? AND config_version = ?
                GROUP BY {dim_col};
                """,
                (wid, exp.applied_version),
            )
        else:
            cur.execute(
                f"""
                SELECT
                    {dim_col} AS arm,
                    SUM(dials) AS dials,
                    SUM(connected) AS connected,
                    SUM(engaged) AS engaged
                FROM events_agg
                WHERE workflow_id = ? AND (sim_day * 24 + sim_hour) >= ?
                GROUP BY {dim_col};
                """,
                (wid, exp_start_hour),
            )
        rows = cur.fetchall()
        cur.close()

        arm_stats: dict[str, dict[str, int]] = {}
        for r in rows:
            arm_stats[r["arm"]] = {
                "dials": int(r["dials"] or 0),
                "connected": int(r["connected"] or 0),
                "engaged": int(r["engaged"] or 0),
            }

        eval_metric = "connected" if exp.primary_kpi == "engagement_rate" else "dials"
        ctrl = arm_stats.get(exp.control_arm, {"dials": 0, "connected": 0, "engaged": 0})
        ctrl_n = ctrl[eval_metric]
        treatment_arms = [a for a in exp.arms if a != exp.control_arm]

        # Wait until candidate treatment arm has received at least some dials
        if any(arm_stats.get(t, {}).get("dials", 0) == 0 for t in treatment_arms):
            return False

        # ----------------------------------------------------------------------
        # Early Harm-Stop: Conservative one-sided interim looks (alpha spending)
        # ----------------------------------------------------------------------
        for t in treatment_arms:
            t_stats = arm_stats.get(t, {"dials": 0, "connected": 0, "engaged": 0})
            n_t_interim = t_stats[eval_metric]
            # Check after accumulating minimal interim sample (n >= 60)
            if n_t_interim >= 60:
                if exp.primary_kpi == "engagement_rate":
                    k_ctrl_i, n_ctrl_i = ctrl["engaged"], ctrl["connected"]
                    k_t_i, n_t_i = t_stats["engaged"], t_stats["connected"]
                    k_ctrl_g, n_ctrl_g = ctrl["connected"], ctrl["dials"]
                    k_t_g, n_t_g = t_stats["connected"], t_stats["dials"]
                else:
                    k_ctrl_i, n_ctrl_i = ctrl["connected"], ctrl["dials"]
                    k_t_i, n_t_i = t_stats["connected"], t_stats["dials"]
                    k_ctrl_g, n_ctrl_g = ctrl["engaged"], ctrl["connected"]
                    k_t_g, n_t_g = t_stats["engaged"], t_stats["connected"]

                if n_ctrl_i > 0 and n_t_i > 0:
                    # One-sided interim look with alpha spending (alpha_interim = 0.01)
                    # 1. Inferiority on primary KPI (diff < -0.02 and p < 0.01)
                    _, p_harm_primary, diff_primary, _ = two_proportion_z_test(
                        k_ctrl_i, n_ctrl_i, k_t_i, n_t_i, alternative="less"
                    )
                    # 2. Guardrail breach (drop > max_drop and p < 0.01)
                    obj = workflow_spec.get("objective", {})
                    guardrail_spec = obj.get("guardrail", {})
                    max_drop = float(guardrail_spec.get(f"{exp.guardrail_kpi}_max_drop", 0.02)) if isinstance(guardrail_spec, dict) else 0.02
                    p_ctrl_g = (k_ctrl_g / n_ctrl_g) if n_ctrl_g > 0 else 0.0
                    p_t_g = (k_t_g / n_t_g) if n_t_g > 0 else 0.0
                    g_delta = p_t_g - p_ctrl_g
                    is_guardrail_harm = False
                    p_harm_g = 1.0
                    if g_delta < -max_drop and n_ctrl_g > 0 and n_t_g > 0:
                        _, p_harm_g, _, _ = two_proportion_z_test(
                            k_ctrl_g, n_ctrl_g, k_t_g, n_t_g, alternative="less"
                        )
                        if p_harm_g < 0.01:
                            is_guardrail_harm = True

                    is_primary_harm = (p_harm_primary < 0.01 and diff_primary < -0.02)

                    if is_primary_harm or is_guardrail_harm:
                        # Early harm-stop triggered!
                        rate_drop = abs(diff_primary) if is_primary_harm else abs(g_delta)
                        exploration_cost_actual = round(n_t_interim * rate_drop, 2)
                        exploration_cost_full = round(exp.required_n_per_arm * rate_drop, 2)
                        exploration_cost_saved = round(exploration_cost_full - exploration_cost_actual, 2)

                        self._close_experiment(exp, "HALTED", None, sim_minute)

                        cur_cfg_resp = self.governance.api.get_config(wid)
                        cur_cfg = cur_cfg_resp["config"]
                        base_ver = cur_cfg_resp["version"]
                        revert_cfg = copy.deepcopy(cur_cfg)
                        revert_cfg.setdefault("allocations", {}).setdefault(exp.cohort_id, {})[exp.dim] = {exp.control_arm: 1.0}

                        harm_desc = f"primary drop {diff_primary:+.3f} (p={p_harm_primary:.4f})" if is_primary_harm else f"guardrail drop {g_delta:+.3f} > max_drop {max_drop} (p={p_harm_g:.4f})"
                        change_summary = f"Early harm-stop: halt {t} on {wid} ({exp.dim}), restore {exp.control_arm}"
                        rationale = (
                            f"Early harm-stop at interim look (n={n_t_interim}): arm {t} halted due to {harm_desc}. "
                            f"Restoring 100% {exp.control_arm}. Exploration cost: actual={exploration_cost_actual} vs full={exploration_cost_full} (saved {exploration_cost_saved} units)."
                        )
                        dry_run = {
                            "action": "early_harm_stop",
                            "halted_arm": t,
                            "control_arm": exp.control_arm,
                            "interim_n": n_t_interim,
                            "exploration_cost_before": exploration_cost_full,
                            "exploration_cost_after": exploration_cost_actual,
                            "exploration_cost_saved": exploration_cost_saved,
                        }
                        self.governance.submit_proposal(
                            workflow_spec=workflow_spec,
                            base_version=base_ver,
                            proposed_config=revert_cfg,
                            change_summary=change_summary,
                            rationale=rationale,
                            affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
                            impact_score=0.70,
                            dry_run=dry_run,
                            sim_minute=sim_minute,
                            sim_ts=sim_ts,
                            is_incident=True,
                            experiment_id=exp.experiment_id,
                        )

                        self.store.record_decision(
                            decision_id=make_deterministic_key(wid, f"dec_harm_stop_{t}", {"interim_n": n_t_interim}, sim_minute),
                            sim_minute=sim_minute,
                            ts=sim_ts,
                            workflow_id=wid,
                            decision_type="EARLY_HARM_STOP",
                            inputs_ref={"experiment_id": exp.experiment_id, "halted_arm": t, "control_arm": exp.control_arm},
                            numbers={
                                "interim_n_treatment": n_t_interim,
                                "interim_n_control": ctrl_n,
                                "diff_primary": diff_primary,
                                "p_harm_primary": p_harm_primary,
                                "guardrail_delta": g_delta,
                                "p_harm_guardrail": p_harm_g,
                                "exploration_cost_before": exploration_cost_full,
                                "exploration_cost_after": exploration_cost_actual,
                                "exploration_cost_saved": exploration_cost_saved,
                            },
                            policy_clause="statistical_integrity.early_harm_stop",
                            outcome=f"halted_{t}_restored_{exp.control_arm}",
                        )
                        return False

        # Check timeout (experiment ran > 4 days without reaching required sample size)
        if sim_minute - exp.created_minute > (4 * 1440):
            self._close_experiment(exp, "TIMED_OUT", None, sim_minute)
            cur_cfg_resp = self.governance.api.get_config(wid)
            cur_cfg = cur_cfg_resp["config"]
            base_ver = cur_cfg_resp["version"]
            revert_cfg = copy.deepcopy(cur_cfg)
            revert_cfg.setdefault("allocations", {}).setdefault(exp.cohort_id, {})[exp.dim] = {exp.control_arm: 1.0}
            self.governance.submit_proposal(
                workflow_spec=workflow_spec,
                base_version=base_ver,
                proposed_config=revert_cfg,
                change_summary=f"Timeout: restore control {exp.control_arm} on {wid} ({exp.dim})",
                rationale=f"Experiment {exp.experiment_id} timed out before reaching planned sample size. Restoring control arm {exp.control_arm}.",
                affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
                impact_score=0.35,
                dry_run={"action": "timeout_revert", "control_arm": exp.control_arm},
                sim_minute=sim_minute,
                sim_ts=sim_ts,
                is_incident=True,
                experiment_id=exp.experiment_id,
            )
            return False

        # Check if control and all treatment arms reached required sample size
        if ctrl_n < exp.required_n_per_arm:
            return False

        for t in treatment_arms:
            t_n = arm_stats.get(t, {}).get(eval_metric, 0)
            if t_n < exp.required_n_per_arm:
                return False

        # 1. Sample Ratio Mismatch (SRM) check
        # With config_version filtering, only campaigns dialed under this experiment are included,
        # ensuring clean random assignment even on next_campaign_only workflows.
        observed_counts = [arm_stats.get(a, {}).get("dials", 0) for a in exp.arms]
        expected_weights = [exp.weights.get(a, 0.25) for a in exp.arms]
        is_srm = False
        srm_p_val = 1.0
        if min(observed_counts) >= 60:
            srm_p_val, is_srm = check_sample_ratio_mismatch(observed_counts, expected_weights)

        if is_srm:
            # Abort experiment due to allocation imbalance and restore control
            self._close_experiment(exp, "INVALIDATED", None, sim_minute)
            cur_cfg_resp = self.governance.api.get_config(wid)
            cur_cfg = cur_cfg_resp["config"]
            base_ver = cur_cfg_resp["version"]
            revert_cfg = copy.deepcopy(cur_cfg)
            revert_cfg.setdefault("allocations", {}).setdefault(exp.cohort_id, {})[exp.dim] = {exp.control_arm: 1.0}
            self.governance.submit_proposal(
                workflow_spec=workflow_spec,
                base_version=base_ver,
                proposed_config=revert_cfg,
                change_summary=f"SRM abort: restore control {exp.control_arm} on {wid} ({exp.dim})",
                rationale=f"SRM detected (p={srm_p_val:.4f}). Restoring 100% control arm {exp.control_arm}.",
                affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
                impact_score=0.50,
                dry_run={"action": "srm_revert", "control_arm": exp.control_arm},
                sim_minute=sim_minute,
                sim_ts=sim_ts,
                is_incident=True,
                experiment_id=exp.experiment_id,
            )
            self.store.record_decision(
                decision_id=make_deterministic_key(wid, "dec_srm", {"exp_id": exp.experiment_id}, sim_minute),
                sim_minute=sim_minute,
                ts=sim_ts,
                workflow_id=wid,
                decision_type="SRM_ABORT",
                inputs_ref={"experiment_id": exp.experiment_id},
                numbers={"srm_p_val": srm_p_val},
                policy_clause="statistical_integrity.srm",
                outcome="aborted_srm_detected",
            )
            return False

        # 2. Hypothesis testing across candidate treatment arms
        primary_p_values: list[float] = []
        treatment_results: list[dict[str, Any]] = []

        # Control metrics
        if exp.primary_kpi == "engagement_rate":
            k_ctrl = ctrl["engaged"]
            n_ctrl = ctrl["connected"]
            k_ctrl_g = ctrl["connected"]
            n_ctrl_g = ctrl["dials"]
        else:
            k_ctrl = ctrl["connected"]
            n_ctrl = ctrl["dials"]
            k_ctrl_g = ctrl["engaged"]
            n_ctrl_g = ctrl["connected"]

        obj = workflow_spec.get("objective", {})
        guardrail_spec = obj.get("guardrail", {})
        max_drop = float(guardrail_spec.get(f"{exp.guardrail_kpi}_max_drop", 0.02)) if isinstance(guardrail_spec, dict) else 0.02

        for t in treatment_arms:
            t_stats = arm_stats[t]
            if exp.primary_kpi == "engagement_rate":
                k_t = t_stats["engaged"]
                n_t = t_stats["connected"]
                k_t_g = t_stats["connected"]
                n_t_g = t_stats["dials"]
            else:
                k_t = t_stats["connected"]
                n_t = t_stats["dials"]
                k_t_g = t_stats["engaged"]
                n_t_g = t_stats["connected"]

            # Test primary KPI
            z, p_val, diff, ci = two_proportion_z_test(k_ctrl, n_ctrl, k_t, n_t, alternative="greater")
            primary_p_values.append(p_val)

            # Test guardrail KPI with policy-derived max_drop
            is_vetoed, g_delta, g_ci = guardrail_veto_check(k_ctrl_g, n_ctrl_g, k_t_g, n_t_g, max_drop=max_drop)

            treatment_results.append({
                "arm": t,
                "n_ctrl": n_ctrl,
                "k_ctrl": k_ctrl,
                "n_t": n_t,
                "k_t": k_t,
                "p_val": p_val,
                "diff": diff,
                "ci": ci,
                "is_vetoed": is_vetoed,
                "g_delta": g_delta,
            })

        # Apply Holm-Bonferroni correction across treatment arms
        significant_flags = holm_bonferroni_correction(primary_p_values, alpha=0.05)

        # Select winner: statistically significant, positive lift, and passes guardrail veto
        winner_arm: str | None = None
        best_lift = 0.0

        for res, is_sig in zip(treatment_results, significant_flags):
            arm = res["arm"]
            p_val = res["p_val"]
            diff = res["diff"]
            is_vetoed = res["is_vetoed"]
            g_delta = res["g_delta"]

            if is_vetoed:
                decision_str = "GUARDRAIL_VETO"
            elif is_sig and diff > 0.01:
                decision_str = "WINNER"
            elif diff < -0.02 and p_val > 0.95:
                decision_str = "INFERIOR"
            else:
                decision_str = "INCONCLUSIVE" if (getattr(exp, "mde", 0.08) > 0.20) else "NO_EFFECT" 

            # Record analysis row in SQLite
            analysis_id = make_deterministic_key(exp.experiment_id, "ana", {"arm": arm}, sim_minute)
            with self.store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO analyses (
                        analysis_id, experiment_id, sim_minute, arm,
                        n_control, k_control, n_treatment, k_treatment,
                        p_value_primary, p_value_guardrail, guardrail_delta,
                        srm_p_value, decision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.05, ?, ?, ?);
                    """,
                    (
                        analysis_id, exp.experiment_id, sim_minute, arm,
                        res["n_ctrl"], res["k_ctrl"], res["n_t"], res["k_t"],
                        p_val, g_delta, srm_p_val, decision_str,
                    ),
                )

            if is_sig and not is_vetoed and diff > best_lift:
                best_lift = diff
                winner_arm = arm

        if winner_arm:
            # Propose winner promotion via governance
            promoted = self._promote_winner(workflow_spec, exp, winner_arm, best_lift, sim_minute, sim_ts)
            if promoted:
                self._close_experiment(exp, "WINNER", winner_arm, sim_minute)
                return True
            return False
        else:
            # Inconclusive, guardrail vetoed, or no arm beat control: restore 100% control arm
            final_status = "INCONCLUSIVE" if (getattr(exp, "mde", 0.08) > 0.20) else "NO_EFFECT"
            self._close_experiment(exp, final_status, None, sim_minute)
            cur_cfg_resp = self.governance.api.get_config(wid)
            cur_cfg = cur_cfg_resp["config"]
            base_ver = cur_cfg_resp["version"]
            revert_cfg = copy.deepcopy(cur_cfg)
            revert_cfg.setdefault("allocations", {}).setdefault(exp.cohort_id, {})[exp.dim] = {exp.control_arm: 1.0}
            change_summary = f"Restore control {exp.control_arm} on {wid} ({exp.dim}): no arm beat control"
            rationale = f"Experiment {exp.experiment_id} concluded without a winning variant. Restoring 100% control arm {exp.control_arm}."
            dry_run = {"action": "revert_control", "control_arm": exp.control_arm}
            self.governance.submit_proposal(
                workflow_spec=workflow_spec,
                base_version=base_ver,
                proposed_config=revert_cfg,
                change_summary=change_summary,
                rationale=rationale,
                affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
                impact_score=0.40,
                dry_run=dry_run,
                sim_minute=sim_minute,
                sim_ts=sim_ts,
                is_incident=True,
                experiment_id=exp.experiment_id,
            )
            return False

    def _promote_winner(
        self,
        workflow_spec: dict[str, Any],
        exp: Experiment,
        winner: str,
        lift: float,
        sim_minute: int,
        sim_ts: str,
    ) -> bool:
        """Promotes winning arm while maintaining a 20% holdout control arm for decay monitoring."""
        wid = workflow_spec["workflow_id"]
        cur_cfg_resp = self.governance.api.get_config(wid)
        cur_cfg = cur_cfg_resp["config"]
        base_version = cur_cfg_resp["version"]

        # 80% winning arm, 20% holdout control arm
        new_alloc = {winner: 0.80, exp.control_arm: 0.20}
        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg.setdefault("allocations", {}).setdefault(exp.cohort_id, {})[exp.dim] = new_alloc

        change_summary = f"Promote winner {winner} on {wid} ({exp.dim}) with 20% holdout"
        rationale = f"Variant {winner} won with statistically significant lift +{lift*100:.1f}%. 20% control arm retained for decay detection."

        dry_run = {
            "winner": winner,
            "measured_lift": round(lift, 4),
            "holdout_share": 0.20,
        }

        prop_id = self.governance.submit_proposal(
            workflow_spec=workflow_spec,
            base_version=base_version,
            proposed_config=proposed_cfg,
            change_summary=change_summary,
            rationale=rationale,
            affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
            impact_score=min(1.0, lift * 3.0),
            dry_run=dry_run,
            sim_minute=sim_minute,
            sim_ts=sim_ts,
            experiment_id=exp.experiment_id,
        )

        if prop_id:
            self.store.record_promoted_variant(
                workflow_id=wid,
                cohort_id=exp.cohort_id,
                dim=exp.dim,
                winning_arm=winner,
                holdout_arm=exp.control_arm,
                promoted_minute=sim_minute,
                promoted_rate=lift,
            )
            self.store.record_decision(
                decision_id=make_deterministic_key(wid, "dec_promote", {"winner": winner}, base_version),
                sim_minute=sim_minute,
                ts=sim_ts,
                workflow_id=wid,
                decision_type="WINNER_PROMOTED",
                inputs_ref={"experiment_id": exp.experiment_id, "proposal_id": prop_id, "winner_arm": winner},
                numbers={"measured_lift": lift, "base_version": base_version},
                policy_clause="experiments.promotion",
                outcome=f"promoted_{winner}_with_holdout",
            )
            return True
        return False

    def check_decay_and_rollback(
        self,
        workflow_spec: dict[str, Any],
        sim_minute: int,
        sim_ts: str,
    ) -> bool:
        """Monitors active promoted variants against holdout arm; proposes rollback if decayed."""
        wid = workflow_spec["workflow_id"]
        active_promos = self.store.get_active_promoted_variants(workflow_id=wid)
        if not active_promos:
            return False

        sim_day = sim_minute // 1440
        rolled_back_any = False

        for promo in active_promos:
            dim = promo["dim"]
            winner = promo["winning_arm"]
            holdout = promo["holdout_arm"]
            cohort_id = promo["cohort_id"]
            promoted_min = promo["promoted_minute"]

            # Give at least 2 days of exposure before checking decay
            if sim_minute < promoted_min + (2 * 1440):
                continue

            dim_col = "welcome_message" if dim == "welcome_message" else "voice"
            cur = self.store._conn.cursor()
            cur.execute(
                f"""
                SELECT
                    {dim_col} AS arm,
                    SUM(dials) AS dials,
                    SUM(connected) AS connected,
                    SUM(engaged) AS engaged
                FROM events_agg
                WHERE workflow_id = ? AND sim_day >= ? AND {dim_col} IN (?, ?)
                GROUP BY {dim_col};
                """,
                (wid, sim_day - 1, winner, holdout),
            )
            rows = {r["arm"]: r for r in cur.fetchall()}
            cur.close()

            w_stats = rows.get(winner)
            h_stats = rows.get(holdout)
            if not w_stats or not h_stats:
                continue

            w_conn = int(w_stats["connected"] or 0)
            w_eng = int(w_stats["engaged"] or 0)
            h_conn = int(h_stats["connected"] or 0)
            h_eng = int(h_stats["engaged"] or 0)

            if w_conn < 100 or h_conn < 40:
                continue

            w_rate = w_eng / w_conn
            h_rate = h_eng / h_conn
            gap = w_rate - h_rate

            # Test if winning arm has decayed below holdout control arm
            z, p_val, diff, _ = two_proportion_z_test(h_eng, h_conn, w_eng, w_conn, alternative="less")

            if gap < -0.02 and p_val < 0.05:
                # Confirmed decay! Trigger rollback
                cur_cfg_resp = self.governance.api.get_config(wid)
                cur_cfg = cur_cfg_resp["config"]
                base_version = cur_cfg_resp["version"]

                proposed_cfg = copy.deepcopy(cur_cfg)
                proposed_cfg.setdefault("allocations", {}).setdefault(cohort_id, {})[dim] = {holdout: 1.0}

                change_summary = f"Rollback decaying variant {winner} on {wid} ({dim}) to baseline {holdout}"
                rationale = (
                    f"Decay detected on variant {winner}: engagement rate dropped to {w_rate:.3f} "
                    f"vs holdout {h_rate:.3f} (gap {gap:+.3f}, p={p_val:.4f}). Rolling back to {holdout}."
                )

                dry_run = {
                    "action": "decay_rollback",
                    "decayed_variant": winner,
                    "restored_baseline": holdout,
                    "measured_decay_gap": round(gap, 4),
                }

                prop_id = self.governance.submit_proposal(
                    workflow_spec=workflow_spec,
                    base_version=base_version,
                    proposed_config=proposed_cfg,
                    change_summary=change_summary,
                    rationale=rationale,
                    affected_leads=workflow_spec.get("daily_batch", {}).get("size", 1000),
                    impact_score=0.60,
                    dry_run=dry_run,
                    sim_minute=sim_minute,
                    sim_ts=sim_ts,
                )

                if prop_id:
                    self.store.mark_promoted_variant_rolled_back(wid, cohort_id, dim)
                    self.store.record_decision(
                        decision_id=make_deterministic_key(wid, "dec_rollback", {"variant": winner}, base_version),
                        sim_minute=sim_minute,
                        ts=sim_ts,
                        workflow_id=wid,
                        decision_type="ROLLBACK_DECAY",
                        inputs_ref={"decayed_variant": winner, "holdout_arm": holdout},
                        numbers={"decay_gap": gap, "p_val": p_val},
                        policy_clause="decay_rollback_discipline",
                        outcome=f"proposed_rollback_{winner}_to_{holdout}",
                    )
                    rolled_back_any = True

        return rolled_back_any

    def _close_experiment(
        self,
        exp: Experiment,
        status: str,
        winner: str | None,
        sim_minute: int,
    ) -> None:
        """Marks experiment closed in SQLite and clears active map."""
        with self.store.transaction() as cur:
            cur.execute(
                """
                UPDATE experiments
                SET status = ?, concluded_minute = ?
                WHERE experiment_id = ?;
                """,
                (status, sim_minute, exp.experiment_id),
            )
        self._active_experiments.pop(exp.workflow_id, None)

        # Candidate retirement: permanently retire inferior/no-effect candidates
        if status in ("NO_EFFECT", "INCONCLUSIVE", "TIMED_OUT"):
            for t in [a for a in exp.arms if a != exp.control_arm]:
                self.store.retire_candidate(exp.workflow_id, exp.dim, t, sim_minute, status)

        # Post-experiment cooldown: prevent relaunch on same (workflow, dim) after NO_EFFECT/INCONCLUSIVE/INVALIDATED/HALTED/TIMED_OUT
        if status in ("NO_EFFECT", "INCONCLUSIVE", "HALTED", "INVALIDATED", "TIMED_OUT"):
            cooldown_days = 3
            cooldown_min = sim_minute + (cooldown_days * 1440)
            self.store.set_cooldown(exp.workflow_id, exp.dim, cooldown_min, status)

    def notify_config_applied(self, workflow_id: str, version: int, minute: int) -> None:
        """Transitions pending experiment to RUNNING when controller's own config apply is confirmed."""
        exp = self._active_experiments.get(workflow_id)
        if exp and exp.status == "PENDING_APPLY":
            exp.status = "RUNNING"
            exp.applied_minute = minute
            exp.applied_version = version
            with self.store.transaction() as cur:
                cur.execute(
                    """
                    UPDATE experiments
                    SET status = 'RUNNING', applied_minute = ?, applied_version = ?
                    WHERE experiment_id = ?;
                    """,
                    (minute, version, exp.experiment_id),
                )

    def invalidate_for_workflow(
        self,
        workflow_id: str,
        reason: str,
        sim_minute: int,
        sim_ts: str = "",
    ) -> None:
        """Invalidates running experiment if a supervisor edit modified underlying configuration."""
        exp = self._active_experiments.pop(workflow_id, None)
        if exp:
            self._close_experiment(exp, "INVALIDATED", None, sim_minute)
            self.store.record_decision(
                decision_id=make_deterministic_key(workflow_id, "dec_inv", {"reason": reason}, sim_minute),
                sim_minute=sim_minute,
                ts=sim_ts or f"minute_{sim_minute}",
                workflow_id=workflow_id,
                decision_type="SUPERVISOR_EDIT_INVALIDATION",
                inputs_ref={"experiment_id": exp.experiment_id, "dim": exp.dim, "reason": reason},
                numbers={"created_minute": exp.created_minute, "invalidated_minute": sim_minute},
                policy_clause="governance.supervisor_preemption",
                outcome=f"experiment_{exp.experiment_id}_invalidated_by_{reason}",
            )

    def get_experiment_outcome_counts(self) -> dict[str, int]:
        """Tally of experiment outcomes: winner, no-effect, infeasible, timed-out, invalidated, halted."""
        counts = {
            "winner": 0,
            "no-effect": 0,
            "infeasible": 0,
            "timed-out": 0,
            "invalidated": 0,
            "halted": 0,
        }
        cur = self.store._conn.cursor()
        cur.execute("SELECT status, COUNT(*) AS cnt FROM experiments GROUP BY status;")
        for r in cur.fetchall():
            s = str(r["status"]).lower().replace("_", "-")
            if s in counts:
                counts[s] = int(r["cnt"])
            elif s == "concluded":
                counts["winner"] += int(r["cnt"])
        cur.close()
        return counts
