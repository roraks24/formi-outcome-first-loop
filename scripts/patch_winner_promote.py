with open('controller/experiments.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = """        if winner_arm:
            # Propose winner promotion via governance
            self._close_experiment(exp, "WINNER", winner_arm, sim_minute)
            self._promote_winner(workflow_spec, exp, winner_arm, best_lift, sim_minute, sim_ts)
            return True"""

replacement = """        if winner_arm:
            # Propose winner promotion via governance
            promoted = self._promote_winner(workflow_spec, exp, winner_arm, best_lift, sim_minute, sim_ts)
            if promoted:
                self._close_experiment(exp, "WINNER", winner_arm, sim_minute)
                return True
            return False"""

target_func = """    def _promote_winner(
        self,
        workflow_spec: dict[str, Any],
        exp: Experiment,
        winner: str,
        lift: float,
        sim_minute: int,
        sim_ts: str,
    ) -> None:"""

replacement_func = """    def _promote_winner(
        self,
        workflow_spec: dict[str, Any],
        exp: Experiment,
        winner: str,
        lift: float,
        sim_minute: int,
        sim_ts: str,
    ) -> bool:"""

target_ret = """        if prop_id:
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
            )"""

replacement_ret = """        if prop_id:
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
        return False"""

assert target in text, "Target 1 not found"
assert target_func in text, "Target func not found"
assert target_ret in text, "Target ret not found"

text = text.replace(target, replacement, 1)
text = text.replace(target_func, replacement_func, 1)
text = text.replace(target_ret, replacement_ret, 1)

with open('controller/experiments.py', 'w', encoding='utf-8', newline='') as f:
    f.write(text)

print('Successfully patched experiments.py!')
