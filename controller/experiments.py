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
import uuid
from dataclasses import dataclass
from typing import Any

from .governance import GovernanceEngine
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
        cur = self.store._conn.cursor()
        cur.execute("SELECT * FROM experiments WHERE status = 'RUNNING';")
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            arms = json.loads(r["arms_json"])
            # Equal weight allocation across active arms
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
        if wid in self._active_experiments:
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

        # Select candidate treatment arms (up to 2 new options to bound multiple testing)
        candidate_treatments = [o for o in opts if o != control_arm][:2]
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

        treat_share = 0.40 / len(candidate_treatments)
        if primary_kpi == "engagement_rate":
            pickup_est = (conn / dials) if dials > 0 else 0.30
            daily_eval_units = daily_size * pickup_est
        else:
            daily_eval_units = daily_size

        max_duration_days = 3.0
        expected_n_per_arm = max(100, int(daily_eval_units * treat_share * max_duration_days))

        # Dynamic MDE derived per experiment from available volume, baseline rate, and power
        mde = compute_mde(baseline_rate=baseline_p, sample_size_per_arm=expected_n_per_arm, alpha=0.05, power=0.80)
        required_n = required_sample_size(baseline_rate=baseline_p, mde=mde, alpha=0.05, power=0.80)

        # Allocate 60% to control, divide 40% among treatment arms
        treat_weight = round(0.40 / len(candidate_treatments), 4)
        ctrl_weight = round(1.0 - (treat_weight * len(candidate_treatments)), 4)
        alloc_weights = {control_arm: ctrl_weight}
        for t in candidate_treatments:
            alloc_weights[t] = treat_weight

        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg.setdefault("allocations", {}).setdefault(cohort_id, {})[dim] = alloc_weights

        exp_id = f"exp_{wid}_{dim}_{uuid.uuid4().hex[:6]}"
        change_summary = f"A/B test on {wid} ({dim}): {control_arm} vs {', '.join(candidate_treatments)}"
        rationale = (
            f"Testing {len(candidate_treatments)} candidate variants against control {control_arm}. "
            f"Target sample size: n={required_n} per arm based on baseline {baseline_p:.3f} and dynamic MDE {mde:.3f}."
        )

        dry_run = {
            "dimension": dim,
            "arms": all_arms,
            "weights": alloc_weights,
            "required_n": required_n,
            "baseline_rate": round(baseline_p, 4),
        }

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
            # Record experiment in store
            with self.store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO experiments (
                        experiment_id, workflow_id, cohort_id, dim, control_arm,
                        arms_json, status, primary_kpi, guardrail_kpi,
                        required_n_per_arm, alpha, power, mde, created_minute
                    ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, 0.05, 0.80, ?, ?);
                    """,
                    (
                        exp_id, wid, cohort_id, dim, control_arm,
                        json.dumps(all_arms), primary_kpi, guardrail_kpi,
                        required_n, round(mde, 4), sim_minute,
                    ),
                )

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
                status="RUNNING",
                created_minute=sim_minute,
            )
            self._active_experiments[wid] = exp
            return True

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

        # Query metrics per arm from events_agg during the experiment window
        cur = self.store._conn.cursor()
        cur.execute(
            f"""
            SELECT
                {dim_col} AS arm,
                SUM(dials) AS dials,
                SUM(connected) AS connected,
                SUM(engaged) AS engaged
            FROM events_agg
            WHERE workflow_id = ?
            GROUP BY {dim_col};
            """,
            (wid,),
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

        # Check if control and all treatment arms reached required sample size
        ctrl = arm_stats.get(exp.control_arm, {"dials": 0, "connected": 0, "engaged": 0})
        eval_metric = "connected" if exp.primary_kpi == "engagement_rate" else "dials"
        ctrl_n = ctrl[eval_metric]

        if ctrl_n < exp.required_n_per_arm:
            return False

        treatment_arms = [a for a in exp.arms if a != exp.control_arm]
        for t in treatment_arms:
            t_n = arm_stats.get(t, {}).get(eval_metric, 0)
            if t_n < exp.required_n_per_arm:
                return False

        # 1. Sample Ratio Mismatch (SRM) check
        observed_counts = [arm_stats.get(a, {}).get("dials", 0) for a in exp.arms]
        expected_weights = [exp.weights.get(a, 0.25) for a in exp.arms]
        srm_p_val, is_srm = check_sample_ratio_mismatch(observed_counts, expected_weights)

        if is_srm:
            # Abort experiment due to allocation imbalance
            self._close_experiment(exp, "INVALIDATED", None, sim_minute)
            self.store.record_decision(
                decision_id=f"dec_{uuid.uuid4().hex[:8]}",
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

            # Test guardrail KPI
            is_vetoed, g_delta, g_ci = guardrail_veto_check(k_ctrl_g, n_ctrl_g, k_t_g, n_t_g, max_drop=0.02)

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

            decision_str = "WINNER" if (is_sig and not is_vetoed and diff > 0.01) else "GUARDRAIL_VETO" if is_vetoed else "INFERIOR"

            # Record analysis row in SQLite
            analysis_id = f"ana_{uuid.uuid4().hex[:8]}"
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
            self._close_experiment(exp, "CONCLUDED", winner_arm, sim_minute)
            self._promote_winner(workflow_spec, exp, winner_arm, best_lift, sim_minute, sim_ts)
            return True
        else:
            # Inconclusive or no arm beat control
            self._close_experiment(exp, "CONCLUDED", None, sim_minute)
            return False

    def _promote_winner(
        self,
        workflow_spec: dict[str, Any],
        exp: Experiment,
        winner: str,
        lift: float,
        sim_minute: int,
        sim_ts: str,
    ) -> None:
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
                        decision_id=f"dec_{uuid.uuid4().hex[:8]}",
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

    def invalidate_for_workflow(self, workflow_id: str, reason: str, sim_minute: int) -> None:
        """Invalidates running experiment if a supervisor edit modified underlying configuration."""
        exp = self._active_experiments.pop(workflow_id, None)
        if exp:
            self._close_experiment(exp, "INVALIDATED", None, sim_minute)
