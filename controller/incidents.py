"""Incident response playbooks: Caller-ID rotation, hallucination remediation, and supervisor edits.

Playbooks:
  1. Caller-ID Degradation (C5): Rotates traffic away from spam-flagged DIDs to healthy peers.
  2. Hallucination Spike (C8 Floor): Detects unsafe prompt -> requests LLM suggestions ->
     synthetically validates (pass_rate >= min_validation_pass_rate) -> proposes replacement with evidence.
  3. Supervisor Edits: Invalidates active experiments and re-proposes against new base.
"""
from __future__ import annotations

import copy
import uuid
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
            impact_score=0.40,
            dry_run=dry_run,
            sim_minute=sim_minute,
            sim_ts=sim_ts,
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
        min_pass_rate = float(gov.get("min_validation_pass_rate", 0.95))
        available_prompts = spec.get("options", {}).get("prompt_version", [])
        baseline_prompt = available_prompts[0] if available_prompts else cur_cfg.get("prompt_version")

        # Check credits remaining from sim state
        state = self.governance.api.get_state()
        credits_rem = float(state.get("credits_remaining", 0.0))
        if credits_rem < 8.0:
            # Credit reserve floor: cannot spend on LLM calls; rollback to baseline prompt directly
            self._rollback_prompt_safe(spec, base_version, cur_cfg, baseline_prompt, sim_minute, sim_ts, det)
            return

        # 1. Ask LLM for suggestions (Costs 3 credits)
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

        chosen_prompt = None
        chosen_val_id = None
        chosen_pass_rate = 0.0

        # 2. Run synthetic validation on candidate suggestions (Costs 2 credits each)
        for s in suggestions:
            cand = s["prompt_version"]
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
                if pass_rate >= min_pass_rate:
                    chosen_prompt = cand
                    chosen_val_id = val_id
                    chosen_pass_rate = pass_rate
                    break
            except Exception:
                continue

        # If no suggestion passed, validate baseline prompt
        if not chosen_prompt:
            try:
                val_res = self.governance.api.run_validation(
                    workflow_id=wid,
                    prompt_version=baseline_prompt,
                    n=200,
                    sim_minute=sim_minute,
                    sim_ts=sim_ts,
                    tenant_id=spec.get("tenant_id", ""),
                )
                if float(val_res.get("pass_rate", 0.0)) >= min_pass_rate:
                    chosen_prompt = baseline_prompt
                    chosen_val_id = val_res.get("validation_run_id")
                    chosen_pass_rate = float(val_res.get("pass_rate", 0.0))
            except Exception:
                pass

        if not chosen_prompt or not chosen_val_id:
            return

        if chosen_prompt == cur_cfg.get("prompt_version"):
            return

        proposed_cfg = copy.deepcopy(cur_cfg)
        proposed_cfg["prompt_version"] = chosen_prompt

        change_summary = f"Remediate hallucination on {wid}: replace {bad_prompt} with validated {chosen_prompt}"
        rationale = (
            f"Hallucination spike on {bad_prompt} reached {det.current_val*100:.1f}%. "
            f"Replacement {chosen_prompt} validated with pass rate {chosen_pass_rate*100:.1f}% "
            f"(run {chosen_val_id}) meeting compliance threshold {min_pass_rate*100:.1f}%."
        )

        dry_run = {
            "action": "prompt_remediation",
            "bad_prompt": bad_prompt,
            "chosen_prompt": chosen_prompt,
            "validation_pass_rate": chosen_pass_rate,
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
            evidence={"validation_run_id": chosen_val_id},
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
