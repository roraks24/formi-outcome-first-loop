"""Backlog-safe, idempotent event stream ingestion and aggregation pipeline.

Streams NDJSON chunks line-by-line to maintain O(1) memory overhead, deduplicates
by event_id, and atomically updates hourly rollups and the ingestion cursor in SQLite WAL.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import defaultdict
from typing import Any, Callable

from .api import SimClient
from .store import Store

START_DATE = dt.date(2027, 1, 4)


def parse_sim_day_hour(ts: str) -> tuple[int, int]:
    """Extracts (sim_day, hour) from ISO timestamp (e.g. '2027-01-04T09:15:00+05:30')."""
    try:
        year = int(ts[:4])
        month = int(ts[5:7])
        mday = int(ts[8:10])
        hour = int(ts[11:13])
        if year == 2027 and month == 1:
            day = mday - 4
        else:
            day = (dt.date(year, month, mday) - START_DATE).days
        return day, hour
    except Exception:
        return 0, 0


class IngestPipeline:
    """Streams simulator chunks and atomically records hourly rollups and checkpoints."""

    def __init__(
        self,
        api: SimClient,
        store: Store,
        on_config_changed: Callable[[dict[str, Any]], None] | None = None,
        on_approval_decided: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.api = api
        self.store = store
        self.on_config_changed = on_config_changed
        self.on_approval_decided = on_approval_decided

    def ingest_available_chunks(
        self,
        kind: str = "events",
        sim_ts: str = "",
    ) -> dict[str, int]:
        """Polls for new chunks beyond current cursor and ingests them sequentially.

        Returns a dictionary of stats: {'chunks': N, 'events': M, 'dials': K}.
        """
        last_chunk, last_event_id = self.store.get_cursor(source_name=kind)
        chunks = self.api.list_chunks(kind=kind, after=last_chunk)

        total_chunks = 0
        total_events = 0
        total_dials = 0

        for ch in chunks:
            chunk_num = ch["chunk"]
            chunk_stats = self._ingest_single_chunk(chunk_num, kind, sim_ts)
            total_chunks += 1
            total_events += chunk_stats["events"]
            total_dials += chunk_stats["dials"]

        return {
            "chunks": total_chunks,
            "events": total_events,
            "dials": total_dials,
        }

    def _ingest_single_chunk(self, chunk_num: int, kind: str, sim_ts: str) -> dict[str, int]:
        """Streams a single chunk, deduplicating events and committing in one atomic transaction."""
        new_event_ids: list[int] = []
        # Key: (workflow_id, sim_day, sim_hour, cohort_id, caller_id, welcome_message, voice, prompt_version)
        # Value: [dials, connected, engaged, halluc_flags, duration_s_sum, turns_sum]
        agg_map: dict[tuple[str, int, int, str, str, str, str, str], list[int]] = defaultdict(
            lambda: [0, 0, 0, 0, 0, 0]
        )
        new_leads: list[tuple[Any, ...]] = []
        lead_outcomes: list[tuple[Any, ...]] = []
        max_event_id = -1
        current_sim_day = 0
        dials_count = 0

        for line in self.api.stream_chunk_lines(chunk_num):
            ev = json.loads(line)
            eid = ev.get("event_id")
            if eid is not None:
                if self.store.is_event_seen(eid):
                    continue
                new_event_ids.append(eid)
                if eid > max_event_id:
                    max_event_id = eid

            etype = ev.get("type")
            ts = ev.get("ts", "")
            s_day, s_hour = parse_sim_day_hour(ts)
            if s_day > current_sim_day:
                current_sim_day = s_day

            if etype == "call":
                dials_count += 1
                wid = ev.get("workflow_id", "")
                cohort_id = ev.get("cohort_id") or "default"
                var = ev.get("variant") or {}
                caller_id = var.get("caller_id", "")
                welcome = var.get("welcome_message", "")
                voice = var.get("voice", "")
                prompt_ver = var.get("prompt_version", "")
                conn = 1 if ev.get("connected") else 0
                eng = 1 if ev.get("engaged") else 0
                qa = ev.get("qa") or {}
                halluc = 1 if (qa and qa.get("hallucination_flag")) else 0
                dur = int(ev.get("duration_s") or 0)
                turns = int(ev.get("turns") or 0)

                key = (wid, s_day, s_hour, cohort_id, caller_id, welcome, voice, prompt_ver)
                entry = agg_map[key]
                entry[0] += 1
                entry[1] += conn
                entry[2] += eng
                entry[3] += halluc
                entry[4] += dur
                entry[5] += turns

            elif etype == "lead":
                lead_id = ev.get("lead_id", "")
                person_id = ev.get("person_id", "")
                wid = ev.get("workflow_id", "")
                campaign_id = ev.get("campaign_id", "")
                feats = ev.get("features") or {}
                lang = feats.get("lang", "")
                tier = str(feats.get("city_tier", ""))
                age = feats.get("age_band", "")
                val = feats.get("value_band", "")
                new_leads.append(
                    (lead_id, person_id, wid, campaign_id, lang, tier, age, val, s_day)
                )

            elif etype == "config_changed":
                if self.on_config_changed:
                    self.on_config_changed(ev)

            elif etype == "approval_decided":
                if self.on_approval_decided:
                    self.on_approval_decided(ev)

        # Convert aggregated counters to flat tuples for executemany
        agg_deltas = [
            (
                k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7],
                v[0], v[1], v[2], v[3], v[4], v[5],
            )
            for k, v in agg_map.items()
        ]

        # Prune lead cache older than trailing 2 days to keep memory strictly bounded
        prune_day = current_sim_day - 2 if current_sim_day >= 2 else None

        # Atomically commit chunk aggregations and advance cursor
        effective_last_event_id = max_event_id if max_event_id != -1 else chunk_num
        self.store.commit_ingest_batch(
            source_name=kind,
            chunk_id=chunk_num,
            last_event_id=effective_last_event_id,
            new_event_ids=new_event_ids,
            agg_deltas=agg_deltas,
            new_leads=new_leads,
            lead_outcomes=lead_outcomes,
            sim_ts=sim_ts or dt.datetime.now().isoformat(),
            prune_day_before=prune_day,
        )

        if os.environ.get("TEST_CRASH_ON_INGEST") == "1":
            os.environ["TEST_CRASH_ON_INGEST"] = "0"
            print("\n[TEST CRASH] Process killed mid-ingest after committing chunk...", file=sys.stderr)
            import os as _os; _os._exit(41)

        return {
            "events": len(new_event_ids),
            "dials": dials_count,
        }
