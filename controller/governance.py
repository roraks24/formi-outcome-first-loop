"""Governance engine managing proposal lifecycles, approver timing, fatigue, and auto-apply.

State Machine:
  DRAFT -> SUBMITTED -> (APPROVED | REJECTED | EXPIRED | LAPSED | STALE) -> APPLIED -> (VERIFIED | ROLLED_BACK)

Enforces:
  1. Write-ahead intents before calling API endpoints (crash-safety & idempotency).
  2. Approver fatigue protection (throttles to <= max_requests_per_24h, reserving the last slot for incidents).
  3. Working hours synchronization (submits between 11:00 and 17:00 IST to respect standups).
  4. Stale-base detection & safe re-proposal.
  5. Policy-permitted auto-apply with total-variation shift checking.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .api import (
    AutoApplyNotPermittedError,
    DuplicateProposalIdError,
    SimApiError,
    SimClient,
    StaleBaseVersionError,
)
from .store import Store


def make_deterministic_key(
    workflow_id: str,
    change_class: str,
    target_config: dict[str, Any],
    base_version: int,
) -> str:
    """Generates a deterministic idempotency key.

    Formula: hash(workflow_id, change_class, target_config_canonical, base_version)
    Ensures identical crash retries reuse the same key, while:
      - STALE base version bumps base_version -> new key
      - Rollback produces a distinct target_config -> new key
    """
    canonical_json = json.dumps(target_config, sort_keys=True, separators=(",", ":"))
    raw = f"{workflow_id}:{change_class}:{canonical_json}:{base_version}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"{change_class}_{workflow_id}_v{base_version}_{digest}"


def total_variation_shift(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> float:
    """Calculates maximum total-variation distance across all cohort allocations."""
    keys = {"default"}
    for c in old_cfg.get("cohorts", []) + new_cfg.get("cohorts", []):
        keys.add(c["id"])

    worst = 0.0
    dims = ("welcome_message", "voice", "caller_id")

    for k in keys:
        for d in dims:
            old_dist = (old_cfg.get("allocations", {}).get(k) or {}).get(d) or old_cfg.get("allocations", {}).get("default", {}).get(d, {})
            new_dist = (new_cfg.get("allocations", {}).get(k) or {}).get(d) or new_cfg.get("allocations", {}).get("default", {}).get(d, {})
            all_opts = set(old_dist) | set(new_dist)
            tv = 0.5 * sum(abs(old_dist.get(o, 0.0) - new_dist.get(o, 0.0)) for o in all_opts)
            worst = max(worst, tv)
    return worst


def compute_config_diff(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> dict[str, Any]:
    """Generates structured diff between old and new configuration."""
    diff: dict[str, Any] = {}
    if old_cfg.get("prompt_version") != new_cfg.get("prompt_version"):
        diff["prompt_version"] = {
            "old": old_cfg.get("prompt_version"),
            "new": new_cfg.get("prompt_version"),
        }

    for k in set(old_cfg.keys()) | set(new_cfg.keys()):
        if k in ("allocations", "schedule", "prompt_version"):
            continue
        if old_cfg.get(k) != new_cfg.get(k):
            diff[k] = {"old": old_cfg.get(k), "new": new_cfg.get(k)}

    for cohort_key in set(old_cfg.get("allocations", {})) | set(new_cfg.get("allocations", {})):
        old_alloc = old_cfg.get("allocations", {}).get(cohort_key, {})
        new_alloc = new_cfg.get("allocations", {}).get(cohort_key, {})
        for dim in ("welcome_message", "voice", "caller_id"):
            if old_alloc.get(dim) != new_alloc.get(dim):
                diff[f"allocations.{cohort_key}.{dim}"] = {
                    "old": old_alloc.get(dim),
                    "new": new_alloc.get(dim),
                }

    if old_cfg.get("schedule") != new_cfg.get("schedule"):
        diff["schedule"] = {"status": "updated"}

    return diff


class GovernanceEngine:
    """Manages policy compliance, proposal submission, approvals, and application."""

    def __init__(self, api: SimClient, store: Store):
        self.api = api
        self.store = store

    def can_auto_apply(
        self,
        workflow_spec: dict[str, Any],
        old_cfg: dict[str, Any],
        new_cfg: dict[str, Any],
    ) -> tuple[bool, str]:
        """Validates if proposed configuration qualifies for policy-permitted auto-apply."""
        gov = workflow_spec.get("governance", {})
        auto_pol = gov.get("auto_apply", {})
        if not auto_pol.get("enabled", False):
            return False, "auto_apply_disabled"

        allowed_dims = set(auto_pol.get("allowed_dims", []))
        max_shift = float(auto_pol.get("max_shift", 0.0))

        # Check modified dimensions
        diff = compute_config_diff(old_cfg, new_cfg)
        if not diff:
            return False, "no_change"

        for key in diff:
            if key.startswith("allocations."):
                dim = key.split(".")[-1]
                if dim not in allowed_dims:
                    return False, f"dimension_{dim}_not_in_allowed_dims"
            elif key == "prompt_version" and "prompt_version" not in allowed_dims:
                return False, "prompt_version_not_in_allowed_dims"
            elif key == "schedule" and "schedule" not in allowed_dims:
                return False, "schedule_not_in_allowed_dims"

        # Check shift
        shift = total_variation_shift(old_cfg, new_cfg)
        if shift > max_shift + 1e-6:
            return False, f"shift_{shift:.3f}_exceeds_max_{max_shift:.3f}"

        return True, "permitted"

    def has_pending_proposal(self, workflow_id: str, dim: str | None = None) -> bool:
        """Checks if there is an active SUBMITTED/PENDING proposal for this workflow."""
        cur = self.store._conn.cursor()
        if dim:
            cur.execute(
                """
                SELECT change_summary, diff_json FROM proposals
                WHERE workflow_id = ? AND status IN ('SUBMITTED', 'PENDING');
                """,
                (workflow_id,),
            )
            rows = cur.fetchall()
            cur.close()
            for r in rows:
                diff_str = str(r["diff_json"] or "")
                summary_str = str(r["change_summary"] or "")
                if dim in diff_str or dim in summary_str:
                    return True
            return False
        else:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM proposals
                WHERE workflow_id = ? AND status IN ('SUBMITTED', 'PENDING');
                """,
                (workflow_id,),
            )
            row = cur.fetchone()
            cur.close()
            return bool(row and row["cnt"] > 0)

    def is_within_approver_hours(
        self,
        workflow_spec: dict[str, Any],
        current_hour: int,
    ) -> bool:
        """Checks if current time is within approver hours from workflow governance policy."""
        gov = workflow_spec.get("governance", {})
        hours = gov.get("approver_hours", [0, 24])
        start_h = int(hours[0])
        end_h = int(hours[1])
        return start_h <= current_hour < end_h

    def is_approver_fatigued(
        self,
        workflow_spec: dict[str, Any],
        sim_minute: int,
        is_incident: bool = False,
    ) -> bool:
        """Throttles requests to max_requests_per_24h defined in workflow governance policy.

        Reserves approver capacity for incidents: experiments cannot take the last slot in
        the 24h window. Incident proposals jump the queue and can use all remaining slots.
        """
        wid = workflow_spec["workflow_id"]
        gov = workflow_spec.get("governance", {})
        max_requests = int(gov.get("max_requests_per_24h", 3))
        cur = self.store._conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS cnt FROM proposals
            WHERE workflow_id = ? AND is_auto_apply = 0 AND created_minute >= ?;
            """,
            (wid, sim_minute - 1440),
        )
        row = cur.fetchone()
        cur.close()
        count = int(row["cnt"] or 0)
        if is_incident:
            return count >= max_requests
        else:
            return count >= max(1, max_requests - 1)

    def submit_proposal(
        self,
        workflow_spec: dict[str, Any],
        base_version: int,
        proposed_config: dict[str, Any],
        change_summary: str,
        rationale: str,
        affected_leads: int,
        impact_score: float,
        dry_run: dict[str, Any],
        sim_minute: int,
        sim_ts: str,
        evidence: dict[str, Any] | None = None,
        is_incident: bool = False,
        detection_id: str | None = None,
        experiment_id: str | None = None,
    ) -> str | None:
        """Executes proposal submission via either auto-apply or /approvals with full crash safety."""
        wid = workflow_spec["workflow_id"]
        gov = (workflow_spec or {}).get("governance", {})
        old_cfg = self.api.get_config(wid)["config"]
        diff = compute_config_diff(old_cfg, proposed_config)
        if not diff:
            # No changes to propose or apply
            return None

        is_incident = (
            is_incident
            or (impact_score >= 0.70)
            or ("rotate" in change_summary.lower())
            or ("remediate" in change_summary.lower())
            or ("caller id" in change_summary.lower())
            or ("rollback" in change_summary.lower())
        )

        if is_incident:
            # Withdraw/supersede any pending non-incident experiment proposals for this workflow
            with self.store.transaction() as cur:
                cur.execute(
                    """
                    UPDATE proposals 
                    SET status = 'WITHDRAWN' 
                    WHERE workflow_id = ? AND status IN ('SUBMITTED', 'PENDING')
                      AND impact_score < 0.70;
                    """,
                    (wid,),
                )

        # 1. Determine if proposal can be auto-applied
        can_auto, auto_reason = self.can_auto_apply(workflow_spec, old_cfg, proposed_config)

        if can_auto:
            auto_prop_id = make_deterministic_key(wid, "auto", proposed_config, base_version)
            cur = self.store._conn.cursor()
            cur.execute("SELECT status FROM intents WHERE idempotency_key = ?;", (auto_prop_id,))
            i_row = cur.fetchone()
            cur.close()
            if i_row:
                return auto_prop_id

            # Auto-apply path
            body = {
                "proposal_id": auto_prop_id,
                "base_version": base_version,
                "proposed_config": proposed_config,
                "rationale": rationale,
            }
            if evidence:
                body["evidence"] = evidence

            # Write-ahead intent before dispatching API call
            self.store.record_intent(
                intent_id=f"int_{auto_prop_id}",
                idempotency_key=auto_prop_id,
                action="auto_apply",
                workflow_id=wid,
                payload=body,
                sim_ts=sim_ts,
            )

            try:
                res = self.api.auto_apply(wid, body)
                new_ver = res.get("version", base_version + 1)
                self.store.mark_intent_executed(auto_prop_id, sim_ts, res)

                # Record proposal state
                with self.store.transaction() as cur:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO proposals (
                            proposal_id, approval_id, workflow_id, base_version,
                            status, change_summary, diff_json, affected_leads,
                            dry_run_json, impact_score, rationale, evidence_json,
                            is_auto_apply, created_minute, decided_minute, expires_minute,
                            applied_minute, applied_version
                        ) VALUES (?, NULL, ?, ?, 'APPLIED', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?);
                        """,
                        (
                            auto_prop_id, wid, base_version, change_summary,
                            json.dumps(diff), affected_leads, json.dumps(dry_run),
                            impact_score, rationale, json.dumps(evidence) if evidence else None,
                            sim_minute, sim_minute, sim_minute + 1440, sim_minute, new_ver,
                        ),
                    )

                inputs_ref_auto: dict[str, Any] = {"proposal_id": auto_prop_id}
                if detection_id:
                    inputs_ref_auto["detection_id"] = detection_id
                if experiment_id:
                    inputs_ref_auto["experiment_id"] = experiment_id

                self.store.record_decision(
                    decision_id=f"dec_{auto_prop_id}",
                    sim_minute=sim_minute,
                    ts=sim_ts,
                    workflow_id=wid,
                    decision_type="AUTO_APPLY",
                    inputs_ref=inputs_ref_auto,
                    numbers={"impact_score": impact_score, "affected_leads": affected_leads},
                    policy_clause=f"auto_apply_permitted:{auto_reason}",
                    outcome=f"auto_applied_v{new_ver}",
                )
                return auto_prop_id

            except StaleBaseVersionError:
                self.store.mark_intent_failed(auto_prop_id, sim_ts, "stale_base_version")
                return None
            except AutoApplyNotPermittedError as e:
                self.store.mark_intent_failed(auto_prop_id, sim_ts, f"auto_apply_forbidden: {e.detail}")
                # Fall through to approval path below with fresh proposal id
            except DuplicateProposalIdError:
                cfg = self.api.get_config(wid)
                new_ver = cfg.get("version", base_version + 1)
                self.store.mark_intent_executed(auto_prop_id, sim_ts, cfg)
                return auto_prop_id

        # 2. Human Approval Path
        current_hour = (sim_minute % 1440) // 60
        if not self.is_within_approver_hours(workflow_spec, current_hour):
            # Defer submission until approver is online
            return None

        if self.is_approver_fatigued(workflow_spec, sim_minute, is_incident=is_incident):
            # Throttle to avoid fatigue rejection; experiments reserve final slot for incidents
            return None

        ttl_hours = min(float(gov.get("proposal_ttl_hours", 24)), 24.0)
        base_proposal_key = make_deterministic_key(wid, "prop", proposed_config, base_version)
        current_attempt = self.store.get_or_create_proposal_attempt(base_proposal_key, sim_ts)
        appr_proposal_id = f"{base_proposal_key}_att{current_attempt}"

        # Idempotency check: if this attempt was already recorded locally
        cur = self.store._conn.cursor()
        cur.execute("SELECT status FROM proposals WHERE proposal_id = ?;", (appr_proposal_id,))
        p_row = cur.fetchone()
        cur.execute("SELECT status FROM intents WHERE idempotency_key = ?;", (appr_proposal_id,))
        i_row = cur.fetchone()
        cur.close()
        if p_row or i_row:
            status = p_row["status"].lower() if p_row else (i_row["status"].lower() if i_row else "")
            if status in ("submitted", "pending", "approved", "applied"):
                return appr_proposal_id
            elif status == "rejected" and is_incident:
                # Prior incident proposal rejected on judgement call, but incident remains unmitigated.
                # Advance persisted attempt counter (independent of sim_minute).
                current_attempt = self.store.increment_proposal_attempt(base_proposal_key, sim_ts)
                appr_proposal_id = f"{base_proposal_key}_att{current_attempt}"
                cur2 = self.store._conn.cursor()
                cur2.execute("SELECT status FROM proposals WHERE proposal_id = ?;", (appr_proposal_id,))
                r_row = cur2.fetchone()
                cur2.close()
                if r_row:
                    return appr_proposal_id if r_row["status"].lower() in ("submitted", "pending") else None
            else:
                return None

        body = {
            "proposal_id": appr_proposal_id,
            "workflow_id": wid,
            "base_version": base_version,
            "proposed_config": proposed_config,
            "change_summary": change_summary,
            "diff": diff,
            "affected_leads": int(affected_leads),
            "dry_run": dry_run,
            "impact_score": float(round(impact_score, 4)),
            "rationale": rationale,
            "expires_in_hours": ttl_hours,
        }
        if evidence:
            body["evidence"] = evidence

        # Write-ahead intent
        self.store.record_intent(
            intent_id=f"int_{appr_proposal_id}",
            idempotency_key=appr_proposal_id,
            action="request_approval",
            workflow_id=wid,
            payload=body,
            sim_ts=sim_ts,
        )

        try:
            res = self.api.request_approval(body)
            aid = res.get("approval_id")
            self.store.mark_intent_executed(appr_proposal_id, sim_ts, res)

            with self.store.transaction() as cur:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO proposals (
                        proposal_id, approval_id, workflow_id, base_version,
                        status, change_summary, diff_json, affected_leads,
                        dry_run_json, impact_score, rationale, evidence_json,
                        is_auto_apply, created_minute, decided_minute, expires_minute
                    ) VALUES (?, ?, ?, ?, 'SUBMITTED', ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?);
                    """,
                    (
                        appr_proposal_id, aid, wid, base_version, change_summary,
                        json.dumps(diff), affected_leads, json.dumps(dry_run),
                        impact_score, rationale, json.dumps(evidence) if evidence else None,
                        sim_minute, sim_minute + int(ttl_hours * 60),
                    ),
                )

            inputs_ref_submit: dict[str, Any] = {"proposal_id": appr_proposal_id, "approval_id": aid}
            if detection_id:
                inputs_ref_submit["detection_id"] = detection_id
            if experiment_id:
                inputs_ref_submit["experiment_id"] = experiment_id

            self.store.record_decision(
                decision_id=f"dec_{appr_proposal_id}",
                sim_minute=sim_minute,
                ts=sim_ts,
                workflow_id=wid,
                decision_type="PROPOSAL_SUBMITTED",
                inputs_ref=inputs_ref_submit,
                numbers={"impact_score": impact_score, "affected_leads": affected_leads},
                policy_clause="governance.approvals",
                outcome=f"submitted_approval_{aid}",
            )
            return appr_proposal_id

        except DuplicateProposalIdError:
            # Simulator already accepted this proposal during a pre-crash dispatch
            approvals = self.api.list_approvals(workflow_id=wid)
            match = next((a for a in approvals if a.get("proposal_id") == appr_proposal_id), None)
            if match:
                aid = match.get("approval_id")
                self.store.mark_intent_executed(appr_proposal_id, sim_ts, match)
                with self.store.transaction() as cur:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO proposals (
                            proposal_id, approval_id, workflow_id, base_version,
                            status, change_summary, diff_json, affected_leads,
                            dry_run_json, impact_score, rationale, evidence_json,
                            is_auto_apply, created_minute, decided_minute, expires_minute
                        ) VALUES (?, ?, ?, ?, 'SUBMITTED', ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?);
                        """,
                        (
                            appr_proposal_id, aid, wid, base_version, change_summary,
                            json.dumps(diff), affected_leads, json.dumps(dry_run),
                            impact_score, rationale, json.dumps(evidence) if evidence else None,
                            sim_minute, sim_minute + int(ttl_hours * 60),
                        ),
                    )
                return appr_proposal_id
            return None

        except StaleBaseVersionError:
            self.store.mark_intent_failed(appr_proposal_id, sim_ts, "stale_base_version")
            return None
        except SimApiError as e:
            self.store.mark_intent_failed(appr_proposal_id, sim_ts, f"approval_error: {e.detail}")
            return None

    def reconcile_and_apply_approved(
        self,
        sim_minute: int,
        sim_ts: str,
    ) -> int:
        """Polls approved proposals and applies them within their apply window."""
        approvals = self.api.list_approvals(status="approved")
        applied_count = 0

        for ap in approvals:
            aid = ap["approval_id"]
            wid = ap["workflow_id"]
            base_ver = ap["base_version"]
            pid = ap.get("proposal_id", "")

            # Check if live config is still on base_version
            cur_cfg = self.api.get_config(wid)
            if cur_cfg["version"] != base_ver:
                # Check if we already recorded this proposal as STALE locally
                cur = self.store._conn.cursor()
                cur.execute("SELECT status FROM proposals WHERE approval_id = ?;", (aid,))
                row = cur.fetchone()
                cur.close()
                if row and row["status"] in ("STALE", "APPLIED", "LAPSED", "EXPIRED", "WITHDRAWN"):
                    continue

                # Live config shifted (e.g. supervisor edit); mark proposal STALE
                with self.store.transaction() as cur:
                    cur.execute("UPDATE proposals SET status = 'STALE' WHERE approval_id = ?;", (aid,))
                self.store.record_decision(
                    decision_id=make_deterministic_key(wid, "dec_stale", {"approval_id": aid}, base_ver),
                    sim_minute=sim_minute,
                    ts=sim_ts,
                    workflow_id=wid,
                    decision_type="STALE_BASE_DETECTED",
                    inputs_ref={"approval_id": aid, "proposal_id": pid},
                    numbers={"base_version": base_ver, "live_version": cur_cfg["version"]},
                    policy_clause="stale_base_handling",
                    outcome="marked_stale_skipped_apply",
                )
                continue

            # Apply approval
            intent_key = make_deterministic_key(wid, "apply", {"approval_id": aid}, base_ver)
            cur = self.store._conn.cursor()
            cur.execute("SELECT status FROM intents WHERE idempotency_key = ?;", (intent_key,))
            i_row = cur.fetchone()
            cur.close()
            if i_row and i_row["status"] == "EXECUTED":
                continue

            self.store.record_intent(
                intent_id=f"int_{intent_key}",
                idempotency_key=intent_key,
                action="apply_approval",
                workflow_id=wid,
                payload={"approval_id": aid},
                sim_ts=sim_ts,
            )

            try:
                res = self.api.apply_approval(wid, aid)
                new_ver = res.get("version")
                self.store.mark_intent_executed(intent_key, sim_ts, res)

                with self.store.transaction() as cur:
                    cur.execute(
                        """
                        UPDATE proposals
                        SET status = 'APPLIED', applied_minute = ?, applied_version = ?
                        WHERE approval_id = ?;
                        """,
                        (sim_minute, new_ver, aid),
                    )
                    cur.execute(
                        """
                        UPDATE experiments
                        SET created_minute = ?
                        WHERE workflow_id = ? AND status = 'RUNNING';
                        """,
                        (sim_minute, wid),
                    )

                self.store.record_decision(
                    decision_id=make_deterministic_key(wid, "dec_apply", {"approval_id": aid}, base_ver),
                    sim_minute=sim_minute,
                    ts=sim_ts,
                    workflow_id=wid,
                    decision_type="APPROVAL_APPLIED",
                    inputs_ref={"approval_id": aid, "proposal_id": pid},
                    numbers={"new_version": new_ver},
                    policy_clause="governance.apply",
                    outcome=f"applied_version_v{new_ver}",
                )
                applied_count += 1

            except StaleBaseVersionError:
                self.store.mark_intent_failed(intent_key, sim_ts, "stale_base_version")
                with self.store.transaction() as cur:
                    cur.execute("UPDATE proposals SET status = 'STALE' WHERE approval_id = ?;", (aid,))
            except SimApiError as e:
                self.store.mark_intent_failed(intent_key, sim_ts, str(e.detail))

        return applied_count
