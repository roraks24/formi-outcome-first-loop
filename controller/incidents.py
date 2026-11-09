"""Incident response playbooks: Caller-ID rotation, hallucination remediation, and supervisor edits.

Playbooks:
  1. Caller-ID Degradation (C5): Rotates traffic away from spam-flagged DIDs to healthy peers.
  2. Hallucination Spike (C8 Floor): Detects unsafe prompt -> requests LLM suggestions ->
     synthetically validates (pass_rate >= min_validation_pass_rate) -> proposes replacement with evidence.
  3. Supervisor Edits: Invalidates active experiments and re-proposes against new base.
"""
from __future__ import annotations

import copy
from typing import Any

from .detect import Detection
from .governance import GovernanceEngine
from .store import Store


class IncidentHandler:
    """Dispatches playbooks in response to confirmed anomaly detections."""

    def __init__(self, governance: GovernanceEngine, store: Store):
        self.governance = governance
        self.store = store
        self._handled_detections: set[str] = set()

    def handle_detections(
        self,
        detections: list[Detection],
        workflow_specs: dict[str, dict[str, Any]],
        sim_minute: int,
        sim_ts: str,
        on_supervisor_edit: Any | None = None,
    ) -> None:
        """Processes each detection through its designated playbook."""
        for det in detections:
            if det.detection_id in self._handled_detections:
                continue

            wid = det.workflow_id
            spec = workflow_specs.get(wid)
            if not spec:
                continue

            if det.detector_type == "caller_id_degraded":
                self._handle_caller_id_degradation(det, spec, sim_minute, sim_ts)
                self._handled_detections.add(det.detection_id)
            elif det.detector_type == "hallucination_spike":
                self._handle_hallucination_spike(det, spec, sim_minute, sim_ts)
                self._handled_detections.add(det.detection_id)

    # --------------------------------------------------------------------------
    # Caller-ID Degradation Playbook (C5)
    # --------------------------------------------------------------------------

    def _handle_caller_id_degradation(
        self,
        det: Detection,
        spec: dict[str, Any],
        sim_minute: int,
        sim_ts: str,
    ) -> None:
        """Rotates traffic away from degraded caller ID to healthy options."""
        wid = det.workflow_id
        bad_did = det.target_value
        cur_cfg_resp = self.governance.api.get_config(wid)
        cur_cfg = cur_cfg_resp["config"]
        base_version = cur_cfg_resp["version"]

        cur_alloc = cur_cfg.get("allocations", {}).get("default", {}).get("caller_id", {})
        # If bad_did is already at 0% weight (or not in cur_alloc), traffic is already rotated away
        if cur_alloc.get(bad_did, 0.0) == 0.0:
            return

        # If a proposal modifying caller_id is already pending approval, do not duplicate
        if self.governance.has_pending_proposal(wid, dim="caller_id"):
            return

        all_dids = spec["options"].get("caller_id", [])
        healthy_dids = [d for d in all_dids if d != bad_did]
        if not healthy_dids:
            return

        # Distribute equally across healthy DIDs
        w = round(1.0 / len(healthy_dids), 4)
        new_alloc: dict[str, float] = {}
        for i, d in enumerate(healthy_dids):
            if i == len(healthy_dids) - 1:
                new_alloc[d] = round(1.0 - sum(new_alloc.values()), 4)
            else:
                new_alloc[d] = w

        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg.setdefault("allocations", {}).setdefault("default", {})["caller_id"] = new_alloc

        change_summary = f"Rotate caller ID on {wid}: disable degraded {bad_did}, use {', '.join(healthy_dids)}"
        rationale = (
            f"Pickup rate on {bad_did} dropped to {det.current_val:.3f} vs peer baseline {det.baseline_val:.3f} "
            f"(p={det.p_value}). Rotating traffic to healthy DIDs {healthy_dids}."
        )

        dry_run = {
            "action": "caller_id_rotation",
            "degraded_did": bad_did,
            "new_allocation": new_alloc,
        }

        self.governance.submit_proposal(
            workflow_spec=spec,
            base_version=base_version,
            proposed_config=proposed_cfg,
            change_summary=change_summary,
            rationale=rationale,
            affected_leads=spec.get("daily_batch", {}).get("size", 1000),
            impact_score=0.80,
            dry_run=dry_run,
            sim_minute=sim_minute,
            sim_ts=sim_ts,
            is_incident=True,
            detection_id=det.detection_id,
        )

    # --------------------------------------------------------------------------
    # Hallucination Spike Remediation Playbook (C8 Floor)
    # --------------------------------------------------------------------------

    def _handle_hallucination_spike(
        self,
        det: Detection,
        spec: dict[str, Any],
        sim_minute: int,
        sim_ts: str,
    ) -> None:
        """Remediates hallucination spike by requesting LLM suggestions, validating, and proposing fix."""
        wid = det.workflow_id
        bad_prompt = det.target_value
        cur_cfg_resp = self.governance.api.get_config(wid)
        cur_cfg = cur_cfg_resp["config"]
        base_version = cur_cfg_resp["version"]

        # If live config is already off bad_prompt, incident is already addressed
        if cur_cfg.get("prompt_version") != bad_prompt:
            return

        # If a prompt proposal is already pending approval, do not duplicate
        if self.governance.has_pending_proposal(wid, dim="prompt_version"):
            return

        gov = spec.get("governance", {})
        min_pass_rate = float(gov.get("min_validation_pass_rate") or 0.97)
        available_prompts = spec.get("options", {}).get("prompt_version", [])
        prim_metric = spec.get("objective", {}).get("primary", "engagement_rate")
        
        # 1. Identify last known-good prompt from history and evaluate rollback baseline
        cur = self.store._conn.cursor()
        cur.execute(
            """
            SELECT prompt_version, SUM(connected) AS conn, SUM(engaged) AS eng, SUM(halluc_flags) AS halluc
            FROM events_agg
            WHERE workflow_id = ? AND prompt_version != ?
            GROUP BY prompt_version;
            """,
            (wid, bad_prompt),
        )
        history_prompts = cur.fetchall()
        cur.close()

        hist_performance: dict[str, dict[str, Any]] = {}
        for hp in history_prompts:
            pv = hp["prompt_version"]
            c = int(hp["conn"] or 0)
            e = int(hp["eng"] or 0)
            h = int(hp["halluc"] or 0)
            rate = (e / c) if c > 0 else None
            hist_performance[pv] = {
                "conn": c,
                "eng": e,
                "halluc": h,
                "halluc_rate": (h / c) if c > 0 else 0.0,
                "primary_rate": rate,
            }

        last_good_prompt = None
        last_good_primary_rate = None
        last_good_n = 0
        for pv, perf in hist_performance.items():
            if perf["conn"] >= 50 and perf["halluc_rate"] <= 0.02:
                last_good_prompt = pv
                last_good_primary_rate = perf["primary_rate"]
                last_good_n = perf["conn"]
                break

        if not last_good_prompt:
            last_good_prompt = available_prompts[0] if available_prompts else "P1"

        # Check credits remaining from sim state
        state = self.governance.api.get_state()
        credits_rem = float(state.get("credits_remaining", 0.0))
        if credits_rem < 8.0:
            # Credit reserve floor: cannot spend on LLM calls; rollback to baseline prompt directly
            self._rollback_prompt_safe(spec, base_version, cur_cfg, last_good_prompt, sim_minute, sim_ts, det)
            return

        # Validate last known-good rollback option
        rollback_val_id = None
        rollback_pass_rate = 0.0
        try:
            rb_res = self.governance.api.run_validation(
                workflow_id=wid,
                prompt_version=last_good_prompt,
                n=200,
                sim_minute=sim_minute,
                sim_ts=sim_ts,
                tenant_id=spec.get("tenant_id", ""),
            )
            rollback_pass_rate = float(rb_res.get("pass_rate", 0.0))
            rollback_val_id = rb_res.get("validation_run_id")
        except Exception:
            pass

        # 2. Ask LLM for suggestions (Costs 3 credits)
        try:
            fixes_resp = self.governance.api.suggest_fixes(
                workflow_id=wid,
                problem=f"hallucination spike on prompt {bad_prompt} (rate={det.current_val:.3f})",
                sim_minute=sim_minute,
                sim_ts=sim_ts,
                tenant_id=spec.get("tenant_id", ""),
            )
            suggestions = fixes_resp.get("suggestions", [])
        except Exception:
            suggestions = []

        # 3. Run synthetic validation on candidate suggestions (Costs 2 credits each)
        candidate_evals: list[dict[str, Any]] = []
        for s in suggestions:
            cand = s["prompt_version"]
            if cand == bad_prompt:
                continue
            try:
                val_res = self.governance.api.run_validation(
                    workflow_id=wid,
                    prompt_version=cand,
                    n=200,
                    sim_minute=sim_minute,
                    sim_ts=sim_ts,
                    tenant_id=spec.get("tenant_id", ""),
                )
                pass_rate = float(val_res.get("pass_rate", 0.0))
                val_id = val_res.get("validation_run_id")
                cand_perf = hist_performance.get(cand, {})
                candidate_evals.append({
                    "prompt_version": cand,
                    "validation_run_id": val_id,
                    "validation_pass_rate": pass_rate,
                    "is_compliant": pass_rate >= min_pass_rate,
                    "measured_primary_kpi": cand_perf.get("primary_rate"),
                    "measured_n": cand_perf.get("conn", 0),
                    "summary": s.get("summary", ""),
                })
            except Exception:
                continue

        # 4. Remediation Selection Policy:
        # Choose candidate with best expected PRIMARY KPI among those meeting min_validation_pass_rate.
        # Prefer known-good rollback (measured data) over LLM candidates unless a candidate has validated
        # evidence of better primary KPI.
        rollback_compliant = rollback_pass_rate >= min_pass_rate
        chosen_prompt = None
        chosen_val_id = None
        chosen_pass_rate = 0.0
        policy_reason = ""

        compliant_cands = [c for c in candidate_evals if c["is_compliant"]]

        if rollback_compliant:
            better_candidate = None
            if last_good_primary_rate is not None:
                for c in compliant_cands:
                    if c["measured_primary_kpi"] is not None and c["measured_n"] >= 50:
                        if c["measured_primary_kpi"] > last_good_primary_rate:
                            better_candidate = c
                            break

            if better_candidate:
                chosen_prompt = better_candidate["prompt_version"]
                chosen_val_id = better_candidate["validation_run_id"]
                chosen_pass_rate = better_candidate["validation_pass_rate"]
                policy_reason = (
                    f"Candidate {chosen_prompt} selected: demonstrated superior validated primary KPI "
                    f"({better_candidate['measured_primary_kpi']:.3f} vs rollback {last_good_primary_rate:.3f}) "
                    f"while meeting compliance threshold ({chosen_pass_rate*100:.1f}% >= {min_pass_rate*100:.1f}%)."
                )
            else:
                chosen_prompt = last_good_prompt
                chosen_val_id = rollback_val_id
                chosen_pass_rate = rollback_pass_rate
                cands_summary_str = ", ".join([f"{c['prompt_version']}(pass={c['validation_pass_rate']*100:.1f}%, primary=unmeasured)" for c in compliant_cands]) or "none"
                meas_kpi_str = f"{last_good_primary_rate:.3f} (N={last_good_n})" if last_good_primary_rate is not None else "baseline"
                policy_reason = (
                    f"Known-good rollback {last_good_prompt} selected: meets compliance ({rollback_pass_rate*100:.1f}% >= {min_pass_rate*100:.1f}%) "
                    f"and has verified production performance ({prim_metric}={meas_kpi_str}). "
                    f"Compliant LLM candidates [{cands_summary_str}] lack validated evidence of superior primary KPI; "
                    f"policy strictly prefers measured rollback over unverified LLM suggestions."
                )
        else:
            if compliant_cands:
                best_cand = max(
                    compliant_cands,
                    key=lambda c: (c["measured_primary_kpi"] if c["measured_primary_kpi"] is not None else -1.0, c["validation_pass_rate"]),
                )
                chosen_prompt = best_cand["prompt_version"]
                chosen_val_id = best_cand["validation_run_id"]
                chosen_pass_rate = best_cand["validation_pass_rate"]
                meas_str = f", primary={best_cand['measured_primary_kpi']:.3f}" if best_cand["measured_primary_kpi"] is not None else ", primary=unmeasured"
                policy_reason = (
                    f"Rollback {last_good_prompt} breached compliance threshold (pass rate {rollback_pass_rate*100:.1f}% < {min_pass_rate*100:.1f}%). "
                    f"Compliant candidate {chosen_prompt} selected (pass rate {chosen_pass_rate*100:.1f}% >= {min_pass_rate*100:.1f}%{meas_str})."
                )
            elif rollback_val_id:
                chosen_prompt = last_good_prompt
                chosen_val_id = rollback_val_id
                chosen_pass_rate = rollback_pass_rate
                policy_reason = (
                    f"No candidate met compliance threshold {min_pass_rate*100:.1f}%. Falling back to rollback {last_good_prompt} "
                    f"(pass rate {rollback_pass_rate*100:.1f}%)."
                )

        if not chosen_prompt or not chosen_val_id:
            return

        if chosen_prompt == cur_cfg.get("prompt_version"):
            return

        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg["prompt_version"] = chosen_prompt

        change_summary = f"Remediate hallucination on {wid}: replace {bad_prompt} with validated {chosen_prompt}"
        rationale = (
            f"Hallucination spike on {bad_prompt} reached {det.current_val*100:.1f}%. "
            f"{policy_reason}"
        )

        dry_run = {
            "action": "prompt_remediation",
            "bad_prompt": bad_prompt,
            "chosen_prompt": chosen_prompt,
            "validation_pass_rate": chosen_pass_rate,
            "rollback_option": last_good_prompt,
            "rollback_pass_rate": rollback_pass_rate,
            "policy_decision": "rollback_preferred_over_unmeasured_llm_candidates" if chosen_prompt == last_good_prompt else "compliant_llm_candidate_selected",
        }

        self.governance.submit_proposal(
            workflow_spec=spec,
            base_version=base_version,
            proposed_config=proposed_cfg,
            change_summary=change_summary,
            rationale=rationale,
            affected_leads=spec.get("daily_batch", {}).get("size", 1000),
            impact_score=0.85,
            dry_run=dry_run,
            sim_minute=sim_minute,
            sim_ts=sim_ts,
            is_incident=True,
            detection_id=det.detection_id,
            evidence={
                "validation_run_id": chosen_val_id,
                "rollback_validation_run_id": rollback_val_id,
            },
        )

    def _rollback_prompt_safe(
        self,
        spec: dict[str, Any],
        base_version: int,
        cur_cfg: dict[str, Any],
        target_prompt: str,
        sim_minute: int,
        sim_ts: str,
        det: Detection,
    ) -> None:
        """Rolls back to safe baseline prompt when credit budget is tight."""
        wid = spec["workflow_id"]
        if target_prompt == cur_cfg.get("prompt_version"):
            return
        # Validate target baseline prompt with minimal n=100 (1 credit)
        try:
            val_res = self.governance.api.run_validation(
                workflow_id=wid,
                prompt_version=target_prompt,
                n=100,
                sim_minute=sim_minute,
                sim_ts=sim_ts,
                tenant_id=spec.get("tenant_id", ""),
            )
            val_id = val_res.get("validation_run_id")
            pass_rate = float(val_res.get("pass_rate", 0.0))
        except Exception:
            return

        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg["prompt_version"] = target_prompt

        self.governance.submit_proposal(
            workflow_spec=spec,
            base_version=base_version,
            proposed_config=proposed_cfg,
            change_summary=f"Emergency rollback to baseline prompt {target_prompt} on {wid}",
            rationale=f"Hallucination spike on {det.target_value}. Rolled back to safe baseline {target_prompt}.",
            affected_leads=spec.get("daily_batch", {}).get("size", 1000),
            impact_score=0.75,
            dry_run={"action": "emergency_rollback", "target": target_prompt},
            sim_minute=sim_minute,
            sim_ts=sim_ts,
            evidence={"validation_run_id": val_id},
        )
