"""Audit store query CLI and explanation engine.

Answers: 'Why did workflow X change at time T, who approved it, and what happened next?'
Traces the full causal chain: detection -> experiment -> analysis -> proposal -> apply -> outcome.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .store import Store


def parse_time_filter(at_str: str) -> int | None:
    """Parses time string like 'D3 14:00' or ISO timestamp into sim_minute."""
    # Match 'D<day> <hour>:<minute>'
    m = re.match(r"D(\d+)\s+(\d{1,2}):(\d{2})", at_str.strip())
    if m:
        day = int(m.group(1))
        hour = int(m.group(2))
        minute = int(m.group(3))
        return day * 1440 + hour * 60 + minute

    # Match ISO timestamp (e.g. 2027-01-06T14:00:00+05:30)
    try:
        from .ingest import parse_sim_day_hour
        day, hour = parse_sim_day_hour(at_str)
        return day * 1440 + hour * 60
    except Exception:
        return None


def explain_causal_chain(
    store: Store,
    workflow_id: str,
    at_str: str | None = None,
) -> None:
    """Searches decisions and prints the complete audit trail and causal reasoning."""
    target_min = parse_time_filter(at_str) if at_str else None

    cur = store._conn.cursor()
    query = "SELECT * FROM decisions WHERE workflow_id = ?"
    params: list[Any] = [workflow_id]

    if target_min is not None:
        query += " AND sim_minute BETWEEN ? AND ?"
        params.extend([target_min - 720, target_min + 720])  # +/- 12 hour window

    query += " ORDER BY sim_minute ASC;"
    cur.execute(query, params)
    decisions = cur.fetchall()

    if not decisions:
        print(f"\n[AUDIT] No decisions found for workflow '{workflow_id}' matching criteria.")
        # Fall back to listing all decisions for this workflow
        cur.execute("SELECT * FROM decisions WHERE workflow_id = ? ORDER BY sim_minute ASC LIMIT 10;", (workflow_id,))
        decisions = cur.fetchall()
        if not decisions:
            print(f"[AUDIT] No decision history exists for workflow '{workflow_id}'.")
            cur.close()
            return
        print(f"[AUDIT] Showing nearest decisions for '{workflow_id}':\n")

    for dec in decisions:
        dec_id = dec["decision_id"]
        dec_type = dec["decision_type"]
        ts = dec["ts"]
        minute = dec["sim_minute"]
        day, mod = divmod(minute, 1440)
        hour = mod // 60
        policy_clause = dec["policy_clause"]
        outcome = dec["outcome"]

        inputs = json.loads(dec["inputs_ref_json"] or "{}")
        numbers = json.loads(dec["numbers_json"] or "{}")

        print("=" * 80)
        print(f"DECISION RECORD: {dec_id} | Type: {dec_type}")
        print(f"Timestamp: {ts} (D{day} {hour:02d}:{mod%60:02d} IST) | Workflow: {workflow_id}")
        print(f"Policy Clause: {policy_clause}")
        print(f"Outcome: {outcome}")
        print("-" * 80)

        # 1. Trace Detection Trigger
        det_id = inputs.get("detection_id")
        if det_id:
            cur.execute("SELECT * FROM detections WHERE detection_id = ?;", (det_id,))
            det = cur.fetchone()
            if det:
                print("1. TRIGGER / ANOMALY DETECTION:")
                print(f"   - Detector: {det['detector_type']} (metric: {det['metric_name']})")
                print(f"   - Dimension: {det['target_dim']} = {det['target_value']}")
                print(f"   - Baseline Rate: {det['baseline_val']:.4f} -> Current Rate: {det['current_val']:.4f}")
                print(f"   - Significance: p = {det['p_value']}")
                print(f"   - Evidence: {det['evidence_json']}")
            else:
                print(f"1. TRIGGER: Detection ID {det_id}")
        elif dec_type == "SUPERVISOR_EDIT_INVALIDATION":
            print("1. TRIGGER: Supervisor Edit Preemption")
            print(f"   - Supervisor updated configuration mid-experiment at sim_minute {numbers.get('invalidated_minute', minute)}.")
            print(f"   - Preempted Dimension: {inputs.get('dim')}")
            print(f"   - Invalidation Reason: {inputs.get('reason')}")
            print(f"   - Action: Terminated active experiment {inputs.get('experiment_id')} without overwriting supervisor changes.")
        else:
            print("1. TRIGGER: Routine Optimization / Scheduled Experiment")

        # 2. Trace Experiment & Analysis
        exp_id = inputs.get("experiment_id")
        if exp_id:
            cur.execute("SELECT * FROM experiments WHERE experiment_id = ?;", (exp_id,))
            exp = cur.fetchone()
            if exp:
                print("\n2. EXPERIMENTAL DESIGN:")
                print(f"   - Experiment ID: {exp['experiment_id']} on Cohort '{exp['cohort_id']}'")
                print(f"   - Dimension: {exp['dim']} | Control: {exp['control_arm']} | Arms: {exp['arms_json']}")
                print(f"   - Sizing: n={exp['required_n_per_arm']} per arm (alpha={exp['alpha']}, power={exp['power']})")

            if dec_type == "EARLY_HARM_STOP":
                print("\n3. EARLY HARM-STOP EVALUATION (INTERIM LOOK):")
                print(f"   - Halted Arm: {inputs.get('halted_arm')} (restored {inputs.get('control_arm')})")
                print(f"   - Interim Samples: Treatment n={numbers.get('interim_n_treatment')}, Control n={numbers.get('interim_n_control')}")
                if numbers.get('diff_primary') is not None:
                    print(f"   - Primary Metric Drop: {numbers.get('diff_primary'):+.4f} (p={numbers.get('p_harm_primary')})")
                if numbers.get('guardrail_delta') is not None:
                    print(f"   - Guardrail Delta: {numbers.get('guardrail_delta'):+.4f} (p={numbers.get('p_harm_guardrail')})")
                print(f"   - Exploration Cost (Conversions Lost): Actual={numbers.get('exploration_cost_after')} vs Full Horizon Planned={numbers.get('exploration_cost_before')}")
                print(f"   - Exploration Cost Avoided/Saved: {numbers.get('exploration_cost_saved')} units")
            else:
                cur.execute("SELECT * FROM analyses WHERE experiment_id = ? ORDER BY sim_minute DESC LIMIT 1;", (exp_id,))
                ana = cur.fetchone()
                if ana:
                    print("\n3. STATISTICAL EVALUATION:")
                    print(f"   - Winning Arm: {ana['arm']}")
                    print(f"   - Control Sample: {ana['k_control']}/{ana['n_control']} ({ana['k_control']/max(ana['n_control'],1):.3f})")
                    print(f"   - Treatment Sample: {ana['k_treatment']}/{ana['n_treatment']} ({ana['k_treatment']/max(ana['n_treatment'],1):.3f})")
                    print(f"   - Primary KPI p-value: {ana['p_value_primary']:.5f}")
                    print(f"   - Guardrail Non-inferiority: delta={ana['guardrail_delta']:+.4f} (p={ana['p_value_guardrail']:.4f})")
                    print(f"   - SRM Check p-value: {ana['srm_p_value']:.4f}")
                    print(f"   - Verdict: {ana['decision']}")

        # 3. Trace Governance Proposal
        prop_id = inputs.get("proposal_id")
        if prop_id:
            cur.execute("SELECT * FROM proposals WHERE proposal_id = ?;", (prop_id,))
            prop = cur.fetchone()
            if prop:
                print("\n4. GOVERNANCE & APPROVAL:")
                print(f"   - Proposal ID: {prop['proposal_id']} (Approval ID: {prop['approval_id']})")
                print(f"   - Base Version: v{prop['base_version']} -> Applied Version: v{prop['applied_version']}")
                print(f"   - Status: {prop['status']} | Auto-Apply: {bool(prop['is_auto_apply'])}")
                print(f"   - Change Summary: {prop['change_summary']}")
                print(f"   - Diff: {prop['diff_json']}")
                print(f"   - Impact Score: {prop['impact_score']} | Affected Leads: {prop['affected_leads']}")
                print(f"   - Rationale: {prop['rationale']}")
                if prop["rejection_reasons_json"]:
                    print(f"   - Reasons / Feedback: {prop['rejection_reasons_json']}")

        # 4. Config Snapshot
        cur.execute(
            "SELECT version, actor, applied_at_minute FROM config_snapshots WHERE workflow_id = ? ORDER BY version DESC LIMIT 2;",
            (workflow_id,),
        )
        snaps = cur.fetchall()
        if snaps:
            print("\n5. APPLIED CONFIGURATION STATUS:")
            for s in snaps:
                print(f"   - Version v{s['version']} by {s['actor']} at minute {s['applied_at_minute']}")

        print("=" * 80 + "\n")

    cur.close()


def main():
    parser = argparse.ArgumentParser(description="Formi Audit & Causal Explanation CLI")
    parser.add_argument("--db", default=None, help="Path to controller SQLite database")
    subparsers = parser.add_subparsers(dest="cmd")

    why_p = subparsers.add_parser("why", help="Explain why a workflow changed at a specific time")
    why_p.add_argument("--workflow", required=True, help="Workflow identifier")
    why_p.add_argument("--at", required=False, help="Time specifier (e.g. 'D3 14:00' or ISO string)")
    why_p.add_argument("--db", dest="why_db", default=None, help="Path to controller SQLite database")

    args = parser.parse_args()

    db_path = getattr(args, "why_db", None) or getattr(args, "db", None) or "data/controller.db"
    store = Store(db_path)
    if args.cmd == "why":
        explain_causal_chain(store, workflow_id=args.workflow, at_str=args.at)
    else:
        # Default action: list recent decisions across all workflows
        cur = store._conn.cursor()
        cur.execute("SELECT decision_id, sim_minute, ts, workflow_id, decision_type, outcome FROM decisions ORDER BY sim_minute DESC LIMIT 20;")
        rows = cur.fetchall()
        cur.close()
        if not rows:
            print("[AUDIT] No decisions recorded in audit store yet.")
        else:
            print(f"{'Decision ID':<18} {'Sim Minute':<12} {'Timestamp':<25} {'Workflow':<15} {'Type':<20} {'Outcome'}")
            print("-" * 105)
            for r in rows:
                print(f"{r['decision_id']:<18} {r['sim_minute']:<12} {r['ts']:<25} {r['workflow_id']:<15} {r['decision_type']:<20} {r['outcome']}")


if __name__ == "__main__":
    main()
