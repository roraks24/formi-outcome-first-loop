with open("controller/store.py", "r", encoding="utf-8") as f:
    content = f.read()

migration_target = '''    CREATE TABLE IF NOT EXISTS experiment_cooldowns (
        workflow_id TEXT NOT NULL,
        dim TEXT NOT NULL,
        cooldown_until_minute INTEGER NOT NULL,
        reason TEXT NOT NULL,
        PRIMARY KEY (workflow_id, dim)
    );
    """,
]'''

migration_replacement = '''    CREATE TABLE IF NOT EXISTS experiment_cooldowns (
        workflow_id TEXT NOT NULL,
        dim TEXT NOT NULL,
        cooldown_until_minute INTEGER NOT NULL,
        reason TEXT NOT NULL,
        PRIMARY KEY (workflow_id, dim)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS retired_candidates (
        workflow_id TEXT NOT NULL,
        dim TEXT NOT NULL,
        candidate TEXT NOT NULL,
        retired_at_minute INTEGER NOT NULL,
        reason TEXT NOT NULL,
        PRIMARY KEY (workflow_id, dim, candidate)
    );
    """,
]'''

assert migration_target in content, "migration_target not found"
content = content.replace(migration_target, migration_replacement)

methods_target = '''        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO experiment_cooldowns (workflow_id, dim, cooldown_until_minute, reason)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(workflow_id, dim) DO UPDATE SET
                    cooldown_until_minute = excluded.cooldown_until_minute,
                    reason = excluded.reason;
                """,
                (workflow_id, dim, cooldown_until_minute, reason),
            )'''

methods_replacement = '''        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO experiment_cooldowns (workflow_id, dim, cooldown_until_minute, reason)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(workflow_id, dim) DO UPDATE SET
                    cooldown_until_minute = excluded.cooldown_until_minute,
                    reason = excluded.reason;
                """,
                (workflow_id, dim, cooldown_until_minute, reason),
            )

    # --------------------------------------------------------------------------
    # Candidate Retirement & Incident Querying
    # --------------------------------------------------------------------------

    def retire_candidate(
        self,
        workflow_id: str,
        dim: str,
        candidate: str,
        sim_minute: int,
        reason: str,
    ) -> None:
        """Permanently retires an inferior/non-effective candidate from future trials."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO retired_candidates (workflow_id, dim, candidate, retired_at_minute, reason)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id, dim, candidate) DO UPDATE SET
                    retired_at_minute = excluded.retired_at_minute,
                    reason = excluded.reason;
                """,
                (workflow_id, dim, candidate, sim_minute, reason),
            )

    def is_candidate_retired(
        self,
        workflow_id: str,
        dim: str,
        candidate: str,
    ) -> bool:
        """Checks if a specific candidate variant has been retired on this workflow dimension."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM retired_candidates
            WHERE workflow_id = ? AND dim = ? AND candidate = ?;
            """,
            (workflow_id, dim, candidate),
        )
        row = cur.fetchone()
        cur.close()
        return row is not None

    def get_retired_candidates(
        self,
        workflow_id: str,
        dim: str,
    ) -> set[str]:
        """Returns all retired candidates for a workflow dimension."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT candidate FROM retired_candidates
            WHERE workflow_id = ? AND dim = ?;
            """,
            (workflow_id, dim),
        )
        rows = cur.fetchall()
        cur.close()
        return {r[0] for r in rows}

    def has_open_incident(self, workflow_id: str | None = None) -> bool:
        """Checks if there is an active unhandled detection or pending incident proposal."""
        cur = self._conn.cursor()
        if workflow_id:
            cur.execute(
                """
                SELECT 1 FROM proposals 
                WHERE workflow_id = ? AND status IN ('SUBMITTED', 'PENDING') AND impact_score >= 0.70;
                """,
                (workflow_id,),
            )
        else:
            cur.execute(
                """
                SELECT 1 FROM proposals 
                WHERE status IN ('SUBMITTED', 'PENDING') AND impact_score >= 0.70;
                """
            )
        row = cur.fetchone()
        cur.close()
        return row is not None'''

assert methods_target in content, "methods_target not found"
content = content.replace(methods_target, methods_replacement)

with open("controller/store.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Successfully patched controller/store.py")
