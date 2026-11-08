"""Unit tests for ingestion idempotency, cursor atomicity, and out-of-order tolerance."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from controller.ingest import IngestPipeline
from controller.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    db_file = tmp_path / "test_store.db"
    return Store(db_file)


def _make_call_event(
    event_id: int,
    ts: str = "2027-01-04T10:15:00+05:30",
    workflow_id: str = "nw_renewal",
    caller_id: str = "D1",
    connected: bool = True,
    engaged: bool = False,
) -> str:
    return json.dumps({
        "type": "call",
        "event_id": event_id,
        "ts": ts,
        "workflow_id": workflow_id,
        "campaign_id": f"{workflow_id}:c0001",
        "lead_id": f"{workflow_id}:l{event_id:07d}",
        "person_id": f"{workflow_id}:p{event_id:06d}",
        "cohort_id": "default",
        "variant": {
            "welcome_message": "W1",
            "voice": "V1",
            "caller_id": caller_id,
            "prompt_version": "P1",
        },
        "connected": connected,
        "engaged": engaged,
        "disposition": "short_hangup" if connected else "no_answer",
        "duration_s": 15 if connected else 0,
        "turns": 2 if connected else 0,
    })


def test_ingest_idempotency_replay_chunk(store: Store):
    """Replaying the same chunk must not duplicate dials, connects, or engages."""
    mock_api = MagicMock()
    pipeline = IngestPipeline(api=mock_api, store=store)

    events_chunk_0 = [
        _make_call_event(1, connected=True, engaged=True),
        _make_call_event(2, connected=True, engaged=False),
        _make_call_event(3, connected=False, engaged=False),
    ]

    mock_api.stream_chunk_lines.return_value = events_chunk_0
    stats1 = pipeline._ingest_single_chunk(0, "events", "2027-01-04T10:15:00+05:30")
    assert stats1["events"] == 3
    assert stats1["dials"] == 3

    kpis_1 = store.get_kpi_totals("nw_renewal")["nw_renewal"]
    assert kpis_1["dials"] == 3
    assert kpis_1["connected"] == 2
    assert kpis_1["engaged"] == 1

    # Now replay the exact same chunk
    mock_api.stream_chunk_lines.return_value = events_chunk_0
    stats2 = pipeline._ingest_single_chunk(0, "events", "2027-01-04T10:15:00+05:30")
    assert stats2["events"] == 0  # All 3 events were already seen
    assert stats2["dials"] == 0

    # Ensure totals are completely unchanged
    kpis_2 = store.get_kpi_totals("nw_renewal")["nw_renewal"]
    assert kpis_2["dials"] == 3
    assert kpis_2["connected"] == 2
    assert kpis_2["engaged"] == 1


def test_cursor_atomicity_on_failure(store: Store):
    """If an error occurs before batch completion, no partial data or cursor advance is committed."""
    mock_api = MagicMock()
    pipeline = IngestPipeline(api=mock_api, store=store)

    # Initial cursor check
    last_chunk, last_event = store.get_cursor("events")
    assert last_chunk == -1
    assert last_event == -1

    # Chunk with invalid JSON in second line
    mock_api.stream_chunk_lines.return_value = [
        _make_call_event(100),
        "INVALID JSON LINE CANNOT PARSE",
    ]

    with pytest.raises(Exception):
        pipeline._ingest_single_chunk(5, "events", "2027-01-04T10:15:00+05:30")

    # Cursor must remain at -1
    c_chunk, c_event = store.get_cursor("events")
    assert c_chunk == -1
    assert c_event == -1

    # Database must have 0 events recorded
    kpis = store.get_kpi_totals("nw_renewal")
    assert kpis.get("nw_renewal", {}).get("dials", 0) == 0
    assert not store.is_event_seen(100)


def test_late_and_out_of_order_event_handling(store: Store):
    """Tolerates events arriving out of chronological order into distinct hourly buckets."""
    mock_api = MagicMock()
    pipeline = IngestPipeline(api=mock_api, store=store)

    events = [
        # Hour 14 event arrives first
        _make_call_event(201, ts="2027-01-04T14:30:00+05:30", connected=True, engaged=True),
        # Hour 09 event arrives late
        _make_call_event(202, ts="2027-01-04T09:15:00+05:30", connected=True, engaged=False),
        # Next day event arrives
        _make_call_event(203, ts="2027-01-05T10:00:00+05:30", connected=False, engaged=False),
    ]

    mock_api.stream_chunk_lines.return_value = events
    pipeline._ingest_single_chunk(1, "events", "2027-01-05T10:00:00+05:30")

    # Query aggregates directly from events_agg table
    cur = store._conn.cursor()
    cur.execute("SELECT sim_day, sim_hour, dials, connected, engaged FROM events_agg ORDER BY sim_day, sim_hour;")
    rows = cur.fetchall()
    cur.close()

    assert len(rows) == 3
    # Day 0, Hour 9
    assert rows[0]["sim_day"] == 0
    assert rows[0]["sim_hour"] == 9
    assert rows[0]["dials"] == 1
    assert rows[0]["connected"] == 1
    assert rows[0]["engaged"] == 0

    # Day 0, Hour 14
    assert rows[1]["sim_day"] == 0
    assert rows[1]["sim_hour"] == 14
    assert rows[1]["dials"] == 1
    assert rows[1]["connected"] == 1
    assert rows[1]["engaged"] == 1

    # Day 1, Hour 10
    assert rows[2]["sim_day"] == 1
    assert rows[2]["sim_hour"] == 10
    assert rows[2]["dials"] == 1
    assert rows[2]["connected"] == 0
