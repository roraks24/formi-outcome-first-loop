# Simulator HTTP API & Protocol Contract

This document records the exact interface and behavioral contract exposed by the simulator to any controller, citing the simulator documentation ([docs/SIMULATOR.md](SIMULATOR.md)), scenario specifications, and ground rules ([README.md](\.\./README.md)).

---

## 1. HTTP Endpoints

All communication between the controller and the simulator occurs over HTTP ([docs/SIMULATOR.md §0, §7](SIMULATOR.md#L4-L6)).
No authentication headers are required. All JSON payloads are standard `application/json` unless noted.

### 1.1 `GET /health`
- **Citation**: [docs/SIMULATOR.md §7](SIMULATOR.md#L181-L184)
- **Request**: None.
- **Response**: `200 OK` `{"ok": true}`.
- **Credit Cost**: 0.
- **Errors**: Standard network/server unreachable.

### 1.2 `GET /sim/state`
- **Citation**: [docs/SIMULATOR.md §1, §5, §7](SIMULATOR.md#L147-L150)
- **Request**: None.
- **Response**: `200 OK`
  ```json
  {
    "scenario": "public_01_dev",
    "sim_minute": 0,
    "ts": "2027-01-04T00:00:00+05:30",
    "end_minute": 11520,
    "finished": false,
    "tick_minutes": 15,
    "credits_remaining": 200.0,
    "chunks": 1,
    "last_event_id": 42
  }
  ```
- **Credit Cost**: 0.

### 1.3 `POST /sim/advance`
- **Citation**: [docs/SIMULATOR.md §1, §6](SIMULATOR.md#L21-L28)
- **Request**: `{"minutes": <int>}`. Must be a positive multiple of `tick_minutes` (15 min).
- **Response**: `200 OK`
  ```json
  {
    "sim_minute": 60,
    "ts": "2027-01-04T01:00:00+05:30",
    "finished": false,
    "new_chunks": [
      {
        "chunk": 1,
        "path": "events/000001.jsonl",
        "kind": "events",
        "events": 250,
        "first_event_id": 1,
        "last_event_id": 250,
        "until_minute": 60,
        "until_ts": "2027-01-04T01:00:00+05:30"
      }
    ]
  }
  ```
- **Credit Cost**: 0.
- **Error Codes**:
  - `400 Bad Request` (`bad_minutes`): If `minutes <= 0` or `minutes % tick_minutes != 0`.
  - `409 Conflict` (`finished`): When `sim_minute >= end_minute` (scenario has ended).

### 1.4 `GET /events/chunks`
- **Citation**: [docs/SIMULATOR.md §6](SIMULATOR.md#L157-L161)
- **Query Params**:
  - `kind`: `"events"` (default) or `"history"`.
  - `after`: `<int>` (default `-1`). Returns chunks with `chunk > after`.
- **Response**: `200 OK` `list[ChunkMetadata]` matching the schema in `POST /sim/advance`.
- **Credit Cost**: 0.

### 1.5 `GET /events/chunks/{n}`
- **Citation**: [docs/SIMULATOR.md §6](SIMULATOR.md#L157-L160)
- **Request**: `n` (chunk index integer).
- **Response**: `200 OK`, media-type `application/x-ndjson`. Stream of JSON objects separated by `\n`.
- **Credit Cost**: 0.
- **Error Codes**: `404 Not Found` if chunk `n` does not exist.

### 1.6 `GET /workflows`
- **Citation**: [docs/SIMULATOR.md §2, §7](SIMULATOR.md#L31-L35)
- **Request**: None.
- **Response**: `200 OK` `list[WorkflowPublic]`.
- **Credit Cost**: 0.

### 1.7 `GET /workflows/{wid}`
- **Citation**: [docs/SIMULATOR.md §2, §7](SIMULATOR.md#L31-L35)
- **Response**: `200 OK`
  ```json
  {
    "workflow_id": "nw_renewal",
    "tenant_id": "northwind_life",
    "use_case": "life-insurance renewal premium reminder (persistency)",
    "languages": ["hi", "en", "ta"],
    "dialling_window": [9, 21],
    "dials_per_hour": 300,
    "cadence": [2, 3, 3],
    "min_gap_minutes": 180,
    "daily_batch": {"size": 1100, "at_hour": 8, "returning_share": 0.4},
    "mid_flight": "allowed",
    "objective": {
      "primary": "engagement_rate",
      "guardrail": {"pickup_rate_max_drop": 0.02}
    },
    "options": {
      "welcome_message": ["W1", "W2", "W3"],
      "voice": ["V1", "V2", "V3", "V4"],
      "caller_id": ["D1", "D2", "D3", "D4"],
      "prompt_version": ["P1", "P3", "P4", "P5"]
    },
    "prompt_catalog": {
      "P1": "Current production prompt",
      "P3": "Grounded: benefit claims restricted to policy-schedule fields..."
    },
    "governance": {
      "auto_apply": {"enabled": true, "allowed_dims": ["schedule", "caller_id"], "max_shift": 0.5},
      "approver_hours": [10, 19],
      "approver_latency_minutes": [30, 240],
      "proposal_ttl_hours": 24,
      "apply_window_hours": 2,
      "max_requests_per_24h": 3,
      "base_reject_rate": 0.1,
      "min_validation_pass_rate": 0.97
    },
    "current_version": 1
  }
  ```
- **Credit Cost**: 0.
- **Error Codes**: `404 Not Found` (`unknown_workflow`).

### 1.8 `GET /workflows/{wid}/config`
- **Citation**: [docs/SIMULATOR.md §3, §7](SIMULATOR.md#L49-L87)
- **Response**: `200 OK`
  ```json
  {
    "workflow_id": "nw_renewal",
    "version": 1,
    "actor": "business_default",
    "applied_at": null,
    "config": {
      "cohorts": [],
      "allocations": {
        "default": {
          "welcome_message": {"W1": 1.0},
          "voice": {"V1": 1.0},
          "caller_id": {"D1": 0.5, "D2": 0.5}
        }
      },
      "prompt_version": "P1",
      "schedule": {
        "hour_weights": {},
        "lead_preferred_hour": {}
      }
    }
  }
  ```
- **Credit Cost**: 0.
- **Error Codes**: `404 Not Found`.

### 1.9 `GET /workflows/{wid}/config/history`
- **Citation**: [docs/SIMULATOR.md §7](SIMULATOR.md#L181-L184)
- **Response**: `200 OK` `list[ConfigHistoryEntry]` showing `version`, `actor`, `changed_dims`, `applied_at`, `proposal_id`, `approval_id`.
- **Credit Cost**: 0.

### 1.10 `PUT /workflows/{wid}/config` (Auto-apply)
- **Citation**: [docs/SIMULATOR.md §4.2](SIMULATOR.md#L140-L146)
- **Request Body**:
  ```json
  {
    "proposal_id": "auto_prop_001",
    "base_version": 1,
    "proposed_config": { ... },
    "rationale": "non-empty explanation",
    "evidence": { ... } // optional
  }
  ```
- **Response**: `200 OK` `{"workflow_id": "nw_renewal", "version": 2, "changed_dims": ["caller_id"]}`
- **Credit Cost**: 0.
- **Error Codes**:
  - `409 Conflict` (`duplicate_proposal_id`): If `proposal_id` was already used.
  - `409 Conflict` (`stale_base_version`): If `base_version != current_version`.
  - `403 Forbidden` (`auto_apply_not_permitted`): If auto-apply disabled, dims not permitted, total variation shift > `max_shift`, no change, or missing rationale.
  - `422 Unprocessable Entity` (`invalid_config`): Schema validation errors.

### 1.11 `POST /approvals` (Submit Proposal)
- **Citation**: [docs/SIMULATOR.md §4.1](SIMULATOR.md#L99-L127)
- **Request Body**:
  ```json
  {
    "proposal_id": "prop_unique_123",
    "workflow_id": "nw_renewal",
    "base_version": 1,
    "proposed_config": { ... },
    "change_summary": "One line human summary",
    "diff": { ... },
    "affected_leads": 1250,
    "dry_run": { ... },
    "impact_score": 0.42,
    "rationale": "Detailed statistical reason",
    "evidence": {"validation_run_id": "val_00001"},
    "expires_in_hours": 12
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "approval_id": "apr_00001",
    "proposal_id": "prop_unique_123",
    "workflow_id": "nw_renewal",
    "status": "pending",
    "base_version": 1,
    "changed_dims": ["voice"],
    "created_at": "2027-01-04T10:00:00+05:30",
    "expires_at": "2027-01-04T22:00:00+05:30"
  }
  ```
- **Credit Cost**: 0.
- **Error Codes**:
  - `422 Unprocessable Entity` (`missing_fields`): Any required field missing.
  - `409 Conflict` (`duplicate_proposal_id`): `proposal_id` already used globally.
  - `409 Conflict` (`stale_base_version`): `base_version != current_version`.
  - `422 Unprocessable Entity` (`no_change`): If `proposed_config` is identical to current config.
  - `422 Unprocessable Entity` (`invalid_config`): Bad config structure or disallowed options.

### 1.12 `GET /approvals`
- **Citation**: [docs/SIMULATOR.md §4.1](SIMULATOR.md#L136-L137)
- **Query Params**: `workflow_id` (optional), `status` (optional).
- **Response**: `200 OK` `list[ApprovalPublic]`.
- **Credit Cost**: 0.

### 1.13 `GET /approvals/{aid}`
- **Citation**: [docs/SIMULATOR.md §4.1](SIMULATOR.md#L136-L137)
- **Response**: `200 OK`
  ```json
  {
    "approval_id": "apr_00001",
    "proposal_id": "prop_unique_123",
    "workflow_id": "nw_renewal",
    "status": "approved", // or "rejected", "expired", "pending", "applied", "lapsed"
    "base_version": 1,
    "changed_dims": ["voice"],
    "created_at": "2027-01-04T10:00:00+05:30",
    "expires_at": "2027-01-04T22:00:00+05:30",
    "decided_at": "2027-01-04T11:15:00+05:30",
    "apply_by": "2027-01-04T13:15:00+05:30",
    "reasons": []
  }
  ```
- **Credit Cost**: 0.
- **Error Codes**: `404 Not Found` (`not_found`).

### 1.14 `POST /workflows/{wid}/apply`
- **Citation**: [docs/SIMULATOR.md §4.1](SIMULATOR.md#L133-L135)
- **Request Body**: `{"approval_id": "apr_00001"}`
- **Response**: `200 OK` `{"workflow_id": "nw_renewal", "version": 2, "changed_dims": ["voice"]}`
- **Credit Cost**: 0.
- **Error Codes**:
  - `404 Not Found` (`not_found`): If `approval_id` does not exist or belongs to another workflow.
  - `409 Conflict` (`not_approved`): If status is not `"approved"`.
  - `410 Gone` (`approval_lapsed`): If sim time > `decided_at + apply_window_hours * 60`.
  - `409 Conflict` (`stale_base_version`): If workflow current version changed after proposal creation.

### 1.15 `POST /llm/suggest-fixes`
- **Citation**: [docs/SIMULATOR.md §5](SIMULATOR.md#L151-L153)
- **Request Body**: `{"workflow_id": "nw_renewal", "problem": "hallucination spike"}`
- **Response**: `200 OK`
  ```json
  {
    "suggestions": [
      {"prompt_version": "P3", "summary": "Grounded..."},
      {"prompt_version": "P5", "summary": "Strict..."}
    ],
    "credits_remaining": 197.0
  }
  ```
- **Credit Cost**: Fixed 3 credits per call.
- **Error Codes**:
  - `402 Payment Required` (`out_of_credits`): When credits < 3.
  - `422 Unprocessable Entity` (`missing_fields`): Missing or empty `problem`.

### 1.16 `POST /validation/runs`
- **Citation**: [docs/SIMULATOR.md §5](SIMULATOR.md#L154-L155)
- **Request Body**: `{"workflow_id": "nw_renewal", "prompt_version": "P3", "n": 200}`
  - Note: `n` must be integer in `[50, 2000]`.
- **Response**: `200 OK`
  ```json
  {
    "validation_run_id": "val_00001",
    "workflow_id": "nw_renewal",
    "prompt_version": "P3",
    "n": 200,
    "failures": 1,
    "pass_rate": 0.995,
    "ts": "2027-01-04T10:00:00+05:30",
    "credits_remaining": 198.0
  }
  ```
- **Credit Cost**: `ceil(n / 100)` credits.
- **Error Codes**:
  - `402 Payment Required` (`out_of_credits`): Insufficient credits.
  - `422 Unprocessable Entity` (`bad_prompt_version`): Prompt not in workflow options.
  - `422 Unprocessable Entity` (`bad_n`): `n` outside `[50, 2000]`.

---

## 2. Event Stream Contract

### 2.1 Event Types and Schemas
- **Citation**: [docs/SIMULATOR.md §6](SIMULATOR.md#L157-L180)

1. `campaign_created`:
   `{"type": "campaign_created", "ts": "...", "tenant_id": "...", "workflow_id": "...", "campaign_id": "nw_renewal:c0001", "leads": 1100, "config_version_at_creation": 1, "event_id": 1}`
2. `lead`:
   `{"type": "lead", "ts": "...", "workflow_id": "...", "campaign_id": "...", "lead_id": "nw_renewal:l00000001", "person_id": "nw_renewal:p0000001", "features": {"lang": "hi", "city_tier": "2", "age_band": "46-60", "value_band": "mid"}, "event_id": 2}`
3. `call`:
   `{"type": "call", "ts": "...", "tenant_id": "...", "workflow_id": "...", "campaign_id": "...", "lead_id": "...", "person_id": "...", "attempt_no": 1, "campaign_day_index": 0, "config_version": 1, "cohort_id": "senior_ta", "variant": {"welcome_message": "W1", "voice": "V1", "caller_id": "D1", "prompt_version": "P1"}, "connected": true, "engaged": false, "disposition": "short_hangup", "duration_s": 14, "turns": 2, "qa": {"hallucination_flag": false, "flow_adherence": 0.94}, "event_id": 3}`
   - `qa` is `None` (or absent) if `connected == false`.
   - `disposition` is one of: `no_answer`, `voicemail`, `short_hangup`, `concluded_interested`, `concluded_not_interested`.
4. `config_changed`:
   `{"type": "config_changed", "ts": "...", "workflow_id": "...", "version": 2, "actor": "supervisor|controller_approved|controller_auto", "changed_dims": ["prompt_version"], "proposal_id": "...", "approval_id": "...", "event_id": 4}`
5. `approval_decided`:
   `{"type": "approval_decided", "ts": "...", "approval_id": "apr_00001", "workflow_id": "...", "status": "approved|rejected|expired", "reasons": [], "event_id": 5}`

### 2.2 Guarantees, Ordering, Duplicates, and Delivery Mechanics
- `event_id` is an integer strictly increasing within a scenario run.
- Chunks are numbered sequentially (`chunk: 0, 1, 2, ...`).
- Each chunk contains up to 250,000 events in NDJSON format.
- `POST /sim/advance` yields `new_chunks`. Events can also be retrieved by chunk index.
- Historical data generated before D0 is in `history` chunks (`GET /events/chunks?kind=history`).
- Because a network failure or process restart can happen mid-ingestion, ingestion MUST be idempotent using `event_id` as primary key or deduplication anchor.

### 2.3 Advancing Time and Scenario Completion
- Time starts at `D0 00:00` (`2027-01-04T00:00:00+05:30`).
- Calling `POST /sim/advance {"minutes": N}` steps time forward by `N` minutes.
- When `sim_minute >= end_minute`, `state.finished == true` and further calls to advance return `409 finished`.

---

## 3. Workflow Config Surface: Mutable vs Off-Limits

- **Citation**: [docs/SIMULATOR.md §2, §3](SIMULATOR.md#L31-L86), [README.md §5](\.\./README.md#L99-L100)

### 3.1 Mutable Fields (Under Controller Governance)
1. `cohorts`: List of up to 20 cohorts evaluated in order. Each cohort has `id` and `where` (AND across features, OR within feature list). Features: `lang`, `city_tier`, `age_band`, `value_band`.
2. `allocations`: Map of cohort `id` (and `"default"`) to probability distribution over `welcome_message`, `voice`, and `caller_id`. Weights must sum > 0 and only use options declared in workflow options.
3. `prompt_version`: Exactly one prompt version per workflow across all cohorts, chosen from workflow `options.prompt_version`.
4. `schedule.hour_weights`: Weight table per cohort (or `"default"`) mapping hour-string (`"9"`, `"10"`, ...) to non-negative weight. Governs *who* is dialed within the dialling window.
5. `schedule.lead_preferred_hour`: Map of `person_id` to preferred hour (int within dialling window). Multiplies lead weight by 6 in that hour and 0.25 in other hours. Capped at 200,000 entries.

### 3.2 Strictly Off-Limits Fields (Business-Owned)
- **Citation**: [README.md §5 Constraint 2](\.\./README.md#L99-L100)
- `cadence`: `[attempts_per_day, ...]` (e.g. `[2, 3, 3]`).
- `dialling_window`: `[start_hour, end_hour]` (e.g. `[9, 21]`).
- `dials_per_hour` (dialler capacity).
- `daily_batch` (`size`, `at_hour`, `returning_share`).
- `min_gap_minutes`.

---

## 4. Per-Workflow Governance Policy Object

- **Citation**: [docs/SIMULATOR.md §4](SIMULATOR.md#L88-L146)
Each workflow defines `governance`:
- `auto_apply`:
  - `enabled`: `boolean`
  - `allowed_dims`: list from `["cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule"]`
  - `max_shift`: maximum total-variation distance (`0.5 * sum(|old - new|)`) allowed across any cohort allocation for agent dims.
- `approver_hours`: `[start_hour, end_hour]` (e.g. `[10, 19]`). Approver only decides during these hours IST.
- `approver_latency_minutes`: `[min_latency, max_latency]` (e.g. `[30, 240]`). Approver takes this long within working hours to decide.
- `proposal_ttl_hours`: Proposal lifetime before expiring (e.g. 12 or 24 hours).
- `apply_window_hours`: Window after approval decision (`decided_minute`) within which `/apply` must be called, otherwise the approval `lapses` (HTTP 410).
- `max_requests_per_24h`: Maximum number of approval requests in a trailing 24-hour window per workflow. Exceeding this triggers immediate rejection with reason: `"approver fatigue: more than N requests in 24h for this workflow — batch your changes"`.
- `base_reject_rate`: Random human judgement rejection probability (e.g. 0.05 to 0.15).
- `min_validation_pass_rate`: Required synthetic pass rate for prompt version changes (e.g. 0.97).

### Supervisor Edit Semantics
- A client supervisor can edit the workflow config directly at any simulated minute (`config_changed` event with `actor: "supervisor"`).
- Edits increment `config_version`.
- Any pending proposal or unapplied approval that had `base_version` equal to the old version will immediately fail with `409 stale_base_version`.
- Controllers must NEVER overwrite supervisor edits silently; the controller must detect the version change, cancel or re-propose against the new base.

---

## 5. Governance Proposal Lifecycle

```
       +------------+
       |   DRAFT    |
       +-----+------+
             |
             v
      +--------------+
      |  SUBMITTED   |  (via POST /approvals)
      +------+-------+
             |
     +-------+-------+-------------+
     |               |             |
     v               v             v
+----------+   +----------+   +---------+
| APPROVED |   | REJECTED |   | EXPIRED |
+----+-----+   +----------+   +---------+
     |
     +---------------+
     |               |
     v               v
+---------+     +---------+
| APPLIED |     |  LAPSED | (if not applied within apply_window_hours)
+----+----+     +---------+
     |
     v
+----------+
| VERIFIED | / ROLLED_BACK (on decay/degradation)
+----------+
```
- Proposal required fields: `proposal_id`, `workflow_id`, `base_version`, `proposed_config`, `change_summary`, `diff`, `affected_leads`, `dry_run`, `impact_score`, `rationale`.
- For prompt changes: must include `evidence.validation_run_id` where `pass_rate >= min_validation_pass_rate`.

---

## 6. Credit Model

- **Citation**: [docs/SIMULATOR.md §5](SIMULATOR.md#L147-L156)
- Budget is per-scenario run (visible in `GET /sim/state` `credits_remaining`).
- Credits do NOT refill automatically unless configured.
- Costs:
  - `POST /llm/suggest-fixes`: 3.0 credits per call.
  - `POST /validation/runs`: `ceil(n / 100)` credits per call (n ∈ [50, 2000]).
- Exceeding budget returns `402 out_of_credits`.
- All other endpoints (advance, chunks, approvals, applies, config reads) cost 0 credits.

---

## 7. Hidden Ground Truth (What the API Does NOT Expose)

- **Citation**: [README.md §5 Rule 1](\.\./README.md#L84-L87), [docs/SIMULATOR.md §0](SIMULATOR.md#L4-L6)
The API strictly does NOT expose:
- `base_pickup` and `base_engage` parameters.
- `did_base_health` or true DID spam reputation levels.
- Underlying variant effect multipliers (`effects` table in scenario YAMLs).
- Variant decay schedules (`after_exposure_days`, `to_mult`, `over_days`).
- True person archetypes and baseline probabilities (`p0`, `e0`, `interest`).
- True prompt hallucination rates and multiplier values.
- Incident injection schedule (when DIDs get flagged, when supervisor edits will strike).
- The controller must infer drift, degradation, and performance purely from ingested events and HTTP API responses.
