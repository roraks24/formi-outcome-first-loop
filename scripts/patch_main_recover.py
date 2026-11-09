with open('controller/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = """                                (
                                    key, aid, wid, base_ver, summary,
                                    json.dumps(diff), affected, json.dumps(dry_run),
                                    impact, rationale, json.dumps(evidence) if evidence else None,
                                    state["sim_minute"], state["sim_minute"] + int(ttl_hours * 60),
                                ),
                            )"""

addition = """
                        if dry_run and "dimension" in dry_run:
                            dim = dry_run["dimension"]
                            arms = dry_run.get("arms", [])
                            req_n = dry_run.get("required_n", 100)
                            exp_id = dry_run.get("experiment_id") or make_deterministic_key(wid, f"exp_{dim}", diff, base_ver)
                            w_spec = self.workflow_specs.get(wid, {})
                            p_kpi = w_spec.get("objective", {}).get("primary", "conclusion_rate")
                            g_kpi = w_spec.get("objective", {}).get("guardrail", "pickup_rate")
                            ctrl_arm = arms[0] if arms else "control"
                            with self.store.transaction() as cur:
                                cur.execute(
                                    \"\"\"
                                    INSERT OR IGNORE INTO experiments (
                                        experiment_id, workflow_id, cohort_id, dim, control_arm,
                                        arms_json, status, primary_kpi, guardrail_kpi,
                                        required_n_per_arm, alpha, power, mde, created_minute, applied_minute
                                    ) VALUES (?, ?, 'default', ?, ?, ?, 'PENDING_APPLY', ?, ?, ?, 0.05, 0.80, 0.08, ?, NULL);
                                    \"\"\",
                                    (
                                        exp_id, wid, dim, ctrl_arm,
                                        json.dumps(arms), p_kpi, g_kpi,
                                        req_n, state["sim_minute"],
                                    ),
                                )"""

if target in content:
    content = content.replace(target, target + addition)
    with open('controller/main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS")
else:
    print("Target not found!")
