"""SQLite-based persistent store for the Formi Outcome-First Agent Loop controller.

Operates in SQLite WAL mode with a single writer connection, explicit transactions,
schema migrations, and crash-resilient write-ahead intents.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable


MIGRATIONS: list[str] = [
    # Migration 1: Base schema for ingestion, rollups, governance, and audit
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS cursors (
        source_name TEXT PRIMARY KEY,
        last_chunk INTEGER NOT NULL,
        last_event_id INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );

    -- Deduplication tracker for exact-event idempotency across chunk retries
    CREATE TABLE IF NOT EXISTS events_seen (
        event_id INTEGER PRIMARY KEY
    );

    -- Hourly aggregated counters: keeps memory bounded at O(1) in total event count
    CREATE TABLE IF NOT EXISTS events_agg (
        workflow_id TEXT NOT NULL,
        sim_day INTEGER NOT NULL,
        sim_hour INTEGER NOT NULL,
        cohort_id TEXT NOT NULL,
        caller_id TEXT NOT NULL,
        welcome_message TEXT NOT NULL,
        voice TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        config_version INTEGER NOT NULL DEFAULT 1,
        dials INTEGER NOT NULL DEFAULT 0,
        connected INTEGER NOT NULL DEFAULT 0,
        engaged INTEGER NOT NULL DEFAULT 0,
        halluc_flags INTEGER NOT NULL DEFAULT 0,
        duration_s_sum INTEGER NOT NULL DEFAULT 0,
        turns_sum INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (workflow_id, sim_day, sim_hour, cohort_id, caller_id, welcome_message, voice, prompt_version, config_version)
    );

    -- Bounded lead cache: retained only for recent campaigns (last 2 days) to power dry-runs
    CREATE TABLE IF NOT EXISTS leads_cache (
        lead_id TEXT PRIMARY KEY,
        person_id TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        lang TEXT NOT NULL,
        city_tier TEXT NOT NULL,
        age_band TEXT NOT NULL,
        value_band TEXT NOT NULL,
        created_at_day INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_leads_cache_wf_day ON leads_cache(workflow_id, created_at_day);

    -- Lead-level experiment outcomes: guarantees lead-level unit of analysis
    CREATE TABLE IF NOT EXISTS experiment_lead_outcomes (
        experiment_id TEXT NOT NULL,
        lead_id TEXT NOT NULL,
        arm TEXT NOT NULL,
        dials INTEGER NOT NULL DEFAULT 0,
        connected INTEGER NOT NULL DEFAULT 0,
        engaged INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (experiment_id, lead_id)
    );

    -- Write-ahead intents for external API calls
    CREATE TABLE IF NOT EXISTS intents (
        intent_id TEXT PRIMARY KEY,
        idempotency_key TEXT UNIQUE NOT NULL,
        action TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,  -- PENDING, EXECUTED, FAILED
        created_at TEXT NOT NULL,
        executed_at TEXT,
        response_json TEXT
    );

    -- Credit ledger tracking every credit expenditure
    CREATE TABLE IF NOT EXISTS credit_ledger (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sim_minute INTEGER NOT NULL,
        endpoint TEXT NOT NULL,
        cost REAL NOT NULL,
        credits_remaining REAL NOT NULL,
        purpose TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        ts TEXT NOT NULL
    );

    -- Proposals state machine
    CREATE TABLE IF NOT EXISTS proposals (
        proposal_id TEXT PRIMARY KEY,
        approval_id TEXT,
        workflow_id TEXT NOT NULL,
        base_version INTEGER NOT NULL,
        status TEXT NOT NULL,  -- DRAFT, SUBMITTED, APPROVED, REJECTED, EXPIRED, APPLIED, LAPSED, STALE
        change_summary TEXT NOT NULL,
        diff_json TEXT NOT NULL,
        affected_leads INTEGER NOT NULL,
        dry_run_json TEXT NOT NULL,
        impact_score REAL NOT NULL,
        rationale TEXT NOT NULL,
        evidence_json TEXT,
        is_auto_apply INTEGER NOT NULL DEFAULT 0,
        created_minute INTEGER NOT NULL,
        decided_minute INTEGER,
        expires_minute INTEGER NOT NULL,
        applied_minute INTEGER,
        applied_version INTEGER,
        rejection_reasons_json TEXT
    );

    -- Persisted attempt counter for retry/idempotency keys (crash/restart safe across minutes)
    CREATE TABLE IF NOT EXISTS proposal_attempts (
        base_proposal_key TEXT PRIMARY KEY,
        attempt_count INTEGER NOT NULL DEFAULT 1,
        last_updated_ts TEXT NOT NULL
    );

    -- Configuration snapshot history
    CREATE TABLE IF NOT EXISTS config_snapshots (
        workflow_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        actor TEXT NOT NULL,
        applied_at_minute INTEGER,
        config_json TEXT NOT NULL,
        PRIMARY KEY (workflow_id, version)
    );

    -- Detections log
    CREATE TABLE IF NOT EXISTS detections (
        detection_id TEXT PRIMARY KEY,
        sim_minute INTEGER NOT NULL,
        ts TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        detector_type TEXT NOT NULL,
        target_dim TEXT,
        target_value TEXT,
        metric_name TEXT NOT NULL,
        baseline_val REAL NOT NULL,
        current_val REAL NOT NULL,
        p_value REAL,
        evidence_json TEXT NOT NULL
    );

    -- Experiments
    CREATE TABLE IF NOT EXISTS experiments (
        experiment_id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        cohort_id TEXT NOT NULL,
        dim TEXT NOT NULL,
        control_arm TEXT NOT NULL,
        arms_json TEXT NOT NULL,
        status TEXT NOT NULL,
        primary_kpi TEXT NOT NULL,
        guardrail_kpi TEXT NOT NULL,
        required_n_per_arm INTEGER NOT NULL,
        alpha REAL NOT NULL DEFAULT 0.05,
        power REAL NOT NULL DEFAULT 0.80,
        mde REAL NOT NULL DEFAULT 0.08,
        created_minute INTEGER NOT NULL,
        applied_minute INTEGER,
        applied_version INTEGER,
        concluded_minute INTEGER
    );

    -- Statistical analyses
    CREATE TABLE IF NOT EXISTS analyses (
        analysis_id TEXT PRIMARY KEY,
        experiment_id TEXT NOT NULL,
        sim_minute INTEGER NOT NULL,
        arm TEXT NOT NULL,
        n_control INTEGER NOT NULL,
        k_control INTEGER NOT NULL,
        n_treatment INTEGER NOT NULL,
        k_treatment INTEGER NOT NULL,
        p_value_primary REAL NOT NULL,
        p_value_guardrail REAL NOT NULL,
        guardrail_delta REAL NOT NULL,
        srm_p_value REAL NOT NULL,
        decision TEXT NOT NULL,
        FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
    );

    -- Append-only decision audit store
    CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        sim_minute INTEGER NOT NULL,
        ts TEXT NOT NULL,
        workflow_id TEXT NOT NULL,
        decision_type TEXT NOT NULL,
        inputs_ref_json TEXT NOT NULL,
        numbers_json TEXT NOT NULL,
        policy_clause TEXT NOT NULL,
        outcome TEXT NOT NULL
    );
    """,
    # Migration 2: Promoted variants tracking for decay detection and rollback
    """
    CREATE TABLE IF NOT EXISTS promoted_variants (
        workflow_id TEXT NOT NULL,
        cohort_id TEXT NOT NULL,
        dim TEXT NOT NULL,
        winning_arm TEXT NOT NULL,
        holdout_arm TEXT NOT NULL,
        promoted_minute INTEGER NOT NULL,
        promoted_rate REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, ROLLED_BACK
        PRIMARY KEY (workflow_id, cohort_id, dim)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment_cooldowns (
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
]


class Store:
    """Manages SQLite storage with WAL mode and thread-safe single-writer access."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,  # Autocommit mode; we manage transactions explicitly
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._setup_pragmas()
        self._run_migrations()

    def _setup_pragmas(self) -> None:
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute("PRAGMA busy_timeout = 30000;")
        cur.close()

    def _run_migrations(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        cur.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
        applied = {row[0] for row in cur.fetchall()}
        for ver, sql in enumerate(MIGRATIONS, start=1):
            if ver not in applied:
                cur.executescript(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'));",
                    (ver,),
                )
        cur.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Provides an atomic transaction scope. Commits on exit, rolls back on exception."""
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        try:
            yield cur
            cur.execute("COMMIT;")
        except Exception:
            cur.execute("ROLLBACK;")
            raise
        finally:
            cur.close()

    def close(self) -> None:
        self._conn.close()

    # --------------------------------------------------------------------------
    # Cursors
    # --------------------------------------------------------------------------

    def get_cursor(self, source_name: str = "events") -> tuple[int, int]:
        """Returns (last_chunk, last_event_id). Defaults to (-1, -1) if uninitialized."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT last_chunk, last_event_id FROM cursors WHERE source_name = ?;",
            (source_name,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return (-1, -1)
        return (int(row["last_chunk"]), int(row["last_event_id"]))

    def is_event_seen(self, event_id: int) -> bool:
        cur = self._conn.cursor()
        cur.execute("SELECT 1 FROM events_seen WHERE event_id = ?;", (event_id,))
        seen = cur.fetchone() is not None
        cur.close()
        return seen

    # --------------------------------------------------------------------------
    # Ingestion Batch Commit (Atomic: events_seen + events_agg + leads + cursor)
    # --------------------------------------------------------------------------

    def commit_ingest_batch(
        self,
        source_name: str,
        chunk_id: int,
        last_event_id: int,
        new_event_ids: list[int],
        agg_deltas: list[tuple[Any, ...]],
        new_leads: list[tuple[Any, ...]],
        lead_outcomes: list[tuple[Any, ...]],
        sim_ts: str,
        prune_day_before: int | None = None,
    ) -> None:
        """Atomically writes all batch items and advances cursor in ONE transaction."""
        with self.transaction() as cur:
            # 1. Track newly seen events for exact deduplication
            if new_event_ids:
                cur.executemany(
                    "INSERT OR IGNORE INTO events_seen (event_id) VALUES (?);",
                    [(eid,) for eid in new_event_ids],
                )

            # 2. Upsert hourly aggregated counters
            if agg_deltas:
                normalized_deltas = [
                    (d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7], 1, d[8], d[9], d[10], d[11], d[12], d[13])
                    if len(d) == 14 else d
                    for d in agg_deltas
                ]
                cur.executemany(
                    """
                    INSERT INTO events_agg (
                        workflow_id, sim_day, sim_hour, cohort_id, caller_id,
                        welcome_message, voice, prompt_version, config_version,
                        dials, connected, engaged, halluc_flags,
                        duration_s_sum, turns_sum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workflow_id, sim_day, sim_hour, cohort_id, caller_id, welcome_message, voice, prompt_version, config_version)
                    DO UPDATE SET
                        dials = dials + excluded.dials,
                        connected = connected + excluded.connected,
                        engaged = engaged + excluded.engaged,
                        halluc_flags = halluc_flags + excluded.halluc_flags,
                        duration_s_sum = duration_s_sum + excluded.duration_s_sum,
                        turns_sum = turns_sum + excluded.turns_sum;
                    """,
                    normalized_deltas,
                )

            # 3. Store active leads for dry-run backtesting
            if new_leads:
                cur.executemany(
                    """
                    INSERT OR IGNORE INTO leads_cache (
                        lead_id, person_id, workflow_id, campaign_id,
                        lang, city_tier, age_band, value_band, created_at_day
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    new_leads,
                )

            # 4. Update lead-level experiment outcomes if applicable
            if lead_outcomes:
                cur.executemany(
                    """
                    INSERT INTO experiment_lead_outcomes (
                        experiment_id, lead_id, arm, dials, connected, engaged
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(experiment_id, lead_id)
                    DO UPDATE SET
                        dials = dials + excluded.dials,
                        connected = MAX(connected, excluded.connected),
                        engaged = MAX(engaged, excluded.engaged);
                    """,
                    lead_outcomes,
                )

            # 5. Prune old lead cache entries beyond recent campaigns
            if prune_day_before is not None and prune_day_before > 0:
                cur.execute(
                    "DELETE FROM leads_cache WHERE created_at_day < ?;",
                    (prune_day_before,),
                )

            # 6. Update cursor in the same atomic transaction
            cur.execute(
                """
                INSERT INTO cursors (source_name, last_chunk, last_event_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_chunk = excluded.last_chunk,
                    last_event_id = excluded.last_event_id,
                    updated_at = excluded.updated_at;
                """,
                (source_name, chunk_id, last_event_id, sim_ts),
            )

    # --------------------------------------------------------------------------
    # Queries & KPI Aggregates
    # --------------------------------------------------------------------------

    def get_kpi_totals(self, workflow_id: str | None = None) -> dict[str, dict[str, int]]:
        """Returns aggregated KPI counts per workflow or overall from events_agg."""
        cur = self._conn.cursor()
        query = """
            SELECT
                workflow_id,
                SUM(dials) AS total_dials,
                SUM(connected) AS total_connected,
                SUM(engaged) AS total_engaged,
                SUM(halluc_flags) AS total_halluc_flags
            FROM events_agg
        """
        params: list[Any] = []
        if workflow_id:
            query += " WHERE workflow_id = ?"
            params.append(workflow_id)
        query += " GROUP BY workflow_id;"

        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()

        res: dict[str, dict[str, int]] = {}
        all_dials = all_conn = all_eng = all_halluc = 0
        for r in rows:
            wid = r["workflow_id"]
            d = int(r["total_dials"] or 0)
            c = int(r["total_connected"] or 0)
            e = int(r["total_engaged"] or 0)
            h = int(r["total_halluc_flags"] or 0)
            res[wid] = {
                "dials": d,
                "connected": c,
                "engaged": e,
                "halluc_flags": h,
            }
            all_dials += d
            all_conn += c
            all_eng += e
            all_halluc += h

        res["ALL"] = {
            "dials": all_dials,
            "connected": all_conn,
            "engaged": all_eng,
            "halluc_flags": all_halluc,
        }
        return res

    # --------------------------------------------------------------------------
    # Write-Ahead Intents & Idempotency
    # --------------------------------------------------------------------------

    def record_intent(
        self,
        intent_id: str,
        idempotency_key: str,
        action: str,
        workflow_id: str,
        payload: dict[str, Any],
        sim_ts: str,
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO intents (
                    intent_id, idempotency_key, action, workflow_id,
                    payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?);
                """,
                (intent_id, idempotency_key, action, workflow_id, json.dumps(payload), sim_ts),
            )

    def mark_intent_executed(
        self,
        idempotency_key: str,
        sim_ts: str,
        response: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE intents
                SET status = 'EXECUTED', executed_at = ?, response_json = ?
                WHERE idempotency_key = ?;
                """,
                (sim_ts, json.dumps(response) if response else None, idempotency_key),
            )

    def mark_intent_failed(self, idempotency_key: str, sim_ts: str, error_detail: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE intents
                SET status = 'FAILED', executed_at = ?, response_json = ?
                WHERE idempotency_key = ?;
                """,
                (sim_ts, json.dumps({"error": error_detail}), idempotency_key),
            )

    def get_pending_intents(self) -> list[sqlite3.Row]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM intents WHERE status = 'PENDING' ORDER BY created_at ASC;")
        rows = cur.fetchall()
        cur.close()
        return rows

    def get_or_create_proposal_attempt(self, base_key: str, sim_ts: str) -> int:
        """Returns the current attempt count for a base proposal key."""
        with self.transaction() as cur:
            cur.execute("SELECT attempt_count FROM proposal_attempts WHERE base_proposal_key = ?;", (base_key,))
            row = cur.fetchone()
            if row:
                return int(row["attempt_count"])
            cur.execute(
                "INSERT OR IGNORE INTO proposal_attempts (base_proposal_key, attempt_count, last_updated_ts) VALUES (?, 1, ?);",
                (base_key, sim_ts),
            )
            return 1

    def increment_proposal_attempt(self, base_key: str, sim_ts: str) -> int:
        """Increments and returns the next attempt count for a rejected proposal."""
        with self.transaction() as cur:
            cur.execute("SELECT attempt_count FROM proposal_attempts WHERE base_proposal_key = ?;", (base_key,))
            row = cur.fetchone()
            if row:
                new_count = int(row["attempt_count"]) + 1
                cur.execute(
                    "UPDATE proposal_attempts SET attempt_count = ?, last_updated_ts = ? WHERE base_proposal_key = ?;",
                    (new_count, sim_ts, base_key),
                )
            else:
                new_count = 2
                cur.execute(
                    "INSERT INTO proposal_attempts (base_proposal_key, attempt_count, last_updated_ts) VALUES (?, 2, ?);",
                    (base_key, sim_ts),
                )
            return new_count

    # --------------------------------------------------------------------------
    # Config Snapshots
    # --------------------------------------------------------------------------

    def save_config_snapshot(
        self,
        workflow_id: str,
        version: int,
        actor: str,
        applied_at_minute: int | None,
        config: dict[str, Any],
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO config_snapshots (
                    workflow_id, version, actor, applied_at_minute, config_json
                ) VALUES (?, ?, ?, ?, ?);
                """,
                (workflow_id, version, actor, applied_at_minute, json.dumps(config)),
            )

    def get_latest_config(self, workflow_id: str) -> tuple[int, dict[str, Any]] | None:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT version, config_json
            FROM config_snapshots
            WHERE workflow_id = ?
            ORDER BY version DESC LIMIT 1;
            """,
            (workflow_id,),
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return (int(row["version"]), json.loads(row["config_json"]))

    # --------------------------------------------------------------------------
    # Credit Accounting
    # --------------------------------------------------------------------------

    def record_credit_spend(
        self,
        sim_minute: int,
        endpoint: str,
        cost: float,
        credits_remaining: float,
        purpose: str,
        tenant_id: str,
        ts: str,
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO credit_ledger (
                    sim_minute, endpoint, cost, credits_remaining, purpose, tenant_id, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (sim_minute, endpoint, cost, credits_remaining, purpose, tenant_id, ts),
            )

    def get_total_credits_spent(self) -> float:
        cur = self._conn.cursor()
        cur.execute("SELECT SUM(cost) AS total FROM credit_ledger;")
        row = cur.fetchone()
        cur.close()
        return float(row["total"] or 0.0)

    # --------------------------------------------------------------------------
    # Decisions Audit Log
    # --------------------------------------------------------------------------

    def record_decision(
        self,
        decision_id: str,
        sim_minute: int,
        ts: str,
        workflow_id: str,
        decision_type: str,
        inputs_ref: dict[str, Any],
        numbers: dict[str, Any],
        policy_clause: str,
        outcome: str,
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO decisions (
                    decision_id, sim_minute, ts, workflow_id, decision_type,
                    inputs_ref_json, numbers_json, policy_clause, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    decision_id,
                    sim_minute,
                    ts,
                    workflow_id,
                    decision_type,
                    json.dumps(inputs_ref),
                    json.dumps(numbers),
                    policy_clause,
                    outcome,
                ),
            )

    # --------------------------------------------------------------------------
    # Promoted Variants & Decay Tracking (C7)
    # --------------------------------------------------------------------------

    def record_promoted_variant(
        self,
        workflow_id: str,
        cohort_id: str,
        dim: str,
        winning_arm: str,
        holdout_arm: str,
        promoted_minute: int,
        promoted_rate: float,
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO promoted_variants (
                    workflow_id, cohort_id, dim, winning_arm, holdout_arm,
                    promoted_minute, promoted_rate, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE');
                """,
                (
                    workflow_id, cohort_id, dim, winning_arm, holdout_arm,
                    promoted_minute, promoted_rate,
                ),
            )

    def get_active_promoted_variants(self, workflow_id: str | None = None) -> list[sqlite3.Row]:
        cur = self._conn.cursor()
        query = "SELECT * FROM promoted_variants WHERE status = 'ACTIVE'"
        params: list[Any] = []
        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def mark_promoted_variant_rolled_back(
        self,
        workflow_id: str,
        cohort_id: str,
        dim: str,
    ) -> None:
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE promoted_variants
                SET status = 'ROLLED_BACK'
                WHERE workflow_id = ? AND cohort_id = ? AND dim = ?;
                """,
                (workflow_id, cohort_id, dim),
            )

    # --------------------------------------------------------------------------
    # Experiment Cooldowns
    # --------------------------------------------------------------------------

    def is_in_cooldown(self, workflow_id: str, dim: str, sim_minute: int) -> bool:
        """Checks if a workflow dimension is under active post-experiment cooldown."""
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT cooldown_until_minute FROM experiment_cooldowns
            WHERE workflow_id = ? AND dim = ?;
            """,
            (workflow_id, dim),
        )
        row = cur.fetchone()
        cur.close()
        if row and int(row["cooldown_until_minute"]) > sim_minute:
            return True
        return False

    def set_cooldown(
        self,
        workflow_id: str,
        dim: str,
        cooldown_until_minute: int,
        reason: str,
    ) -> None:
        """Sets or updates a cooldown on (workflow_id, dim)."""
        with self.transaction() as cur:
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
        return row is not None

