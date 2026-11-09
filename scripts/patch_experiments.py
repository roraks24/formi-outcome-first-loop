with open("controller/experiments.py", "r", encoding="utf-8") as f:
    exp_code = f.read()

# 1. Update design_and_launch to check has_open_incident and retire_candidate
t1 = '''        # Check cooldown: no relaunch on the same (workflow, dim) after NO_EFFECT/INVALIDATED/HALTED
        if self.store.is_in_cooldown(wid, dim, sim_minute):
            return False'''

r1 = '''        # Block experiment launches if workflow has an open incident
        if self.store.has_open_incident(wid):
            return False

        # Check cooldown: no relaunch on the same (workflow, dim) after NO_EFFECT/INVALIDATED/HALTED
        if self.store.is_in_cooldown(wid, dim, sim_minute):
            return False'''

assert t1 in exp_code, "t1 not found"
exp_code = exp_code.replace(t1, r1)

t2 = '''        # Select 1 candidate treatment arm at a time to eliminate multiple negative variant drag
        candidate_treatments = [o for o in opts if o != control_arm][:1]'''

r2 = '''        # Select 1 candidate treatment arm at a time that has not been retired
        candidate_treatments = [o for o in opts if o != control_arm and not self.store.is_candidate_retired(wid, dim, o)][:1]'''

assert t2 in exp_code, "t2 not found"
exp_code = exp_code.replace(t2, r2)

# 2. Update timeout in evaluate_and_promote to revert to control
t3 = '''        # Check timeout (experiment ran > 4 days without reaching required sample size)
        if sim_minute - exp.created_minute > (4 * 1440):
            self._close_experiment(exp, "TIMED_OUT", None, sim_minute)
            return False'''

r3 = '''        # Check timeout (experiment ran > 4 days without reaching required sample size)
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
            return False'''

assert t3 in exp_code, "t3 not found"
exp_code = exp_code.replace(t3, r3)

# 3. Update _close_experiment to retire candidates on NO_EFFECT and TIMED_OUT
t4 = '''        # Post-experiment cooldown: prevent relaunch on same (workflow, dim) after NO_EFFECT/INVALIDATED/HALTED/TIMED_OUT
        if status in ("NO_EFFECT", "HALTED", "INVALIDATED", "TIMED_OUT"):'''

r4 = '''        # Candidate retirement: permanently retire inferior/no-effect candidates
        if status in ("NO_EFFECT", "TIMED_OUT"):
            for t in [a for a in exp.arms if a != exp.control_arm]:
                self.store.retire_candidate(exp.workflow_id, exp.dim, t, sim_minute, status)

        # Post-experiment cooldown: prevent relaunch on same (workflow, dim) after NO_EFFECT/INVALIDATED/HALTED/TIMED_OUT
        if status in ("NO_EFFECT", "HALTED", "INVALIDATED", "TIMED_OUT"):'''

assert t4 in exp_code, "t4 not found"
exp_code = exp_code.replace(t4, r4)

with open("controller/experiments.py", "w", encoding="utf-8") as f:
    f.write(exp_code)

print("Successfully patched controller/experiments.py")

# Now patch controller/governance.py
with open("controller/governance.py", "r", encoding="utf-8") as f:
    gov_code = f.read()

t5 = '''        is_incident = (
            is_incident
            or (impact_score >= 0.70)
            or ("rotate" in change_summary.lower())
            or ("remediate" in change_summary.lower())
            or ("caller id" in change_summary.lower())
            or ("rollback" in change_summary.lower())
        )'''

r5 = '''        is_incident = (
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
                )'''

assert t5 in gov_code, "t5 not found"
gov_code = gov_code.replace(t5, r5)

t6 = '''if row and row["status"] in ("STALE", "APPLIED", "LAPSED", "EXPIRED"):'''
r6 = '''if row and row["status"] in ("STALE", "APPLIED", "LAPSED", "EXPIRED", "WITHDRAWN"):'''

assert t6 in gov_code, "t6 not found"
gov_code = gov_code.replace(t6, r6)

with open("controller/governance.py", "w", encoding="utf-8") as f:
    f.write(gov_code)

print("Successfully patched controller/governance.py")
