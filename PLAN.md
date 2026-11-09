# Outcome-First Agent Loop — Implementation Plan

This plan establishes the architecture, data schemas, statistical framework, and scope boundaries for the Formi Outcome-First Agent Loop controller.

---

## 1. System Contract & Ground Rules Summary

### 1.1 API Endpoints Found
- **State & Clocks**: `GET /health`, `GET /sim/state`, `POST /sim/advance` (step clock by positive multiple of 15 min).
- **Ingestion**: `GET /events/chunks` (list metadata), `GET /events/chunks/{n}` (NDJSON stream).
- **Workflows & Config**: `GET /workflows`, `GET /workflows/{wid}`, `GET /workflows/{wid}/config`, `GET /workflows/{wid}/config/history`.
- **Governance**:
  - `POST /approvals`: Submit formal proposal (idempotent via `proposal_id`).
  - `GET /approvals`, `GET /approvals/{aid}`: Query status (`pending`, `approved`, `rejected`, `expired`, `lapsed`, `applied`).
  - `POST /workflows/{wid}/apply`: Apply approved proposal within `apply_window_hours`.
  - `PUT /workflows/{wid}/config`: Auto-apply config if permitted by workflow policy (`max_shift`, `allowed_dims`).
- **LLM & Synthetic Validation (Credit-Bearing)**:
  - `POST /llm/suggest-fixes`: Cost 3 credits; returns up to 3 candidate prompt versions.
  - `POST /validation/runs`: Cost $\lceil n / 100 \rceil$ credits ($n \in [50, 2000]$); measures hallucination pass rate.

### 1.2 Event Stream & Ordering
- Events: `campaign_created`, `lead`, `call`, `config_changed`, `approval_decided`.
- `event_id` is globally strictly increasing; chunks are chunked up to 250k events.
- Ingestion must be backlog-safe, strictly deduplicated by `event_id`, and handle out-of-order/late delivery.

### 1.3 Mutable vs Off-Limits Config Surfaces
- **Mutable**: `cohorts` (up to 20), `allocations` (`welcome_message`, `voice`, `caller_id`), `prompt_version`, `schedule.hour_weights`, `schedule.lead_preferred_hour`.
- **Off-Limits**: `cadence` (retry attempts), `dialling_window` (e.g. 9-21 IST), `dials_per_hour` (dialer capacity), `daily_batch` parameters, `min_gap_minutes`.

### 1.4 Credit Budgeting
- Credits are shared across the scenario run. Running out triggers HTTP `402 out_of_credits`.
- 30% of credits must be reserved exclusively for incident remediation (e.g., prompt hallucination fixes).

---

## 2. Stakeholder Memo Contradictions vs Simulator Policy

| Memo | Stakeholder Claim | Reality & Policy Evidence | Action / Pushback |
|---|---|---|---|
| **01 (Sales)** | Blended score for easy viewing | Forbidden by [README.md §5 Constraint 3](README.md#L101-L102). Primary and Guardrail KPIs must remain separate. | Refuse to blend. Report primary KPI lift with guardrail margin. |
| **01 (Sales)** | Promote winners by evening based on lunch data | Violates minimum sample size, power constraints, and introduces time-of-day bias. | Require fixed horizon tests with full day / multi-day sample sizes. |
| **01 (Sales)** | Auto-apply everything on Kestrel (`ks_cancel`) | Policy sets `auto_apply.allowed_dims: [schedule, caller_id, welcome_message, voice]` and `max_shift: 0.2`. `prompt_version` and `cohorts` are prohibited; shifts > 0.2 fail with HTTP 403. | Use auto-apply only within allowed dims and $\le 20\%$ shift; route prompt/cohort changes through `/approvals`. |
| **01 (Sales)** | Auto-apply on Northwind KYC (`nw_kyc`) | Policy sets `auto_apply.enabled: false`. Auto-apply calls return HTTP 403. | All KYC changes routed through human approval. |
| **01 (Sales)** | Roll out "Persuasive" prompt (P4) everywhere | P4 has 2.5% hallucination rate (over 4x baseline), failing Northwind's `min_validation_pass_rate: 0.97`. | Refuse cross-client prompt copying; validate all prompts before proposal. |
| **02 (Compliance)** | IRDAI auditability within 24h & named human approvals | Matches policy and IRDAI regulations. | Full audit store with `why` query tracing every change. |
| **03 (Platform)** | Crash safety, idempotency, <10 min on load scenario | Matches system reliability requirements. | Write-ahead intents, SQLite WAL mode, batched streaming ingestion. |
| **04 (Supervisor)** | Respect supervisor edits; max 2 requests/day after 11:00 AM | Matches `409 stale_base_version` mechanics; policy allows 4 requests/day, but capping at 2 prevents fatigue rejections. | Invalidate experiments on supervisor edit; batch proposals into 1 daily digest after 11:00 AM. |
| **05 (Product)** | One shared cohort object; separate KPIs; honest impact | Matches core architectural principles C2 and C10. | Unified `Cohort` abstraction shared across all components. |
| **05 (Product)** | P0 wishlist includes per-person call-time (C4) | Over-budget; per-person scheduling risks severe variance on 200k leads. | Cut per-person scheduling to stretch; prioritize floor capabilities. |
| **06 (CTO)** | Regulated industry reality; credits finite; clear cuts | Cites ground truth over stakeholder claims. | Rigorous statistical design, explicit scope cuts, reproducible decisions. |

---

## 3. System Architecture

```
                                  +-------------------+
                                  |     Simulator     |
                                  |    (HTTP API)     |
                                  +----+---------+----+
                                       |         ^
                       GET chunks      |         | POST /approvals, PUT /config,
                       POST /advance   |         | POST /apply, /llm, /validation
                                       v         |
+-----------------------------------------------------------------------------------+
| CONTROLLER PROCESS                                                                |
|                                                                                   |
|  +------------------+         +------------------+         +-------------------+  |
|  |    Ingestion     | ------> | Stream Aggregator| ------> |   SQLite (WAL)    |  |
|  |  (Cursor-based)  |         | (Hourly rollups) |         | (Events, Cursors, |  |
|  +------------------+         +------------------+         |  Intents, Audits) |  |
|                                                            +---------+---------+  |
|                                                                      |            |
|                                                                      v            |
|  +-----------------------------------------------------------------------------+  |
|  | CORE LEARNING LOOP                                                          |  |
|  |                                                                             |  |
|  |  +----------------+     +------------------+     +-----------------------+  |  |
|  |  |   Detectors    | --> |   Experiments    | --> |       Governance      |  |  |
|  |  | - Drift (CUSUM)|     | - Fixed horizon  |     | - State machine       |  |  |
|  |  | - Caller-ID    |     | - Two-prop z-test|     | - Write-ahead intent  |  |  |
|  |  | - Hallucination|     | - Holm-Bonferroni|     | - Approver fatigue/hr |  |  |
|  |  | - Supervisor   |     | - Guardrail veto |     | - Auto-apply / Stale  |  |  |
|  |  +----------------+     +------------------+     +-----------------------+  |  |
|  |                                                                             |  |
|  |  +----------------+     +------------------+     +-----------------------+  |  |
|  |  | Shared Cohort  |     |  Credit Budget   |     |    Audit / Explain    |  |  |
|  |  |  Abstraction   |     | (30% Reserve)    |     |   (`why` CLI tool)    |  |  |
|  |  +----------------+     +------------------+     +-----------------------+  |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

### Module Layout
```
controller/
  ├── __init__.py
  ├── main.py          # Process lifecycle, ticks, advance orchestrator, graceful shutdown
  ├── api.py           # HTTP client with retries, exponential backoff, jitter, timeouts
  ├── store.py         # SQLite WAL connection, schema migrations, transactional queries
  ├── ingest.py        # Stream chunk reader, deduplication, rollup aggregations
  ├── cohort.py        # Single shared Cohort object (definitions, matching, fallback)
  ├── detect.py        # CUSUM & Wilson drift detection, caller-ID peer ranking, prompt alerts
  ├── experiments.py   # Multi-arm experiment design, deterministic hashing, allocation
  ├── stats.py         # Pure statistical functions: sample size, z-test, Holm-Bonferroni, SRM
  ├── governance.py    # Proposal state machine, batching, fatigue throttle, stale-base reconciliation
  ├── incidents.py     # Incident response: caller-ID degradation & hallucination playbooks
  ├── audit.py         # Append-only decision store & `python -m controller.audit why` CLI
  └── budget.py        # Credit ledger, quota reservation, per-tenant accounting
```

---

## 4. SQLite Storage Schema (WAL Mode)

All tables reside in a single SQLite database (`controller.db`) operating in WAL mode (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`).

```sql
-- Cursors for exactly-once ingestion checkpointing
CREATE TABLE IF NOT EXISTS cursors (
    source_name TEXT PRIMARY KEY,
    last_chunk INTEGER NOT NULL,
    last_event_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

-- Hourly aggregated event counters (O(1) memory footprint relative to lead volume)
CREATE TABLE IF NOT EXISTS events_agg (
    workflow_id TEXT NOT NULL,
    sim_day INTEGER NOT NULL,
    sim_hour INTEGER NOT NULL,
    cohort_id TEXT NOT NULL,
    caller_id TEXT NOT NULL,
    welcome_message TEXT NOT NULL,
    voice TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    dials INTEGER NOT NULL DEFAULT 0,
    connected INTEGER NOT NULL DEFAULT 0,
    engaged INTEGER NOT NULL DEFAULT 0,
    halluc_flags INTEGER NOT NULL DEFAULT 0,
    duration_s_sum INTEGER NOT NULL DEFAULT 0,
    turns_sum INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (workflow_id, sim_day, sim_hour, cohort_id, caller_id, welcome_message, voice, prompt_version)
);

-- Lead cache: stores active leads per campaign for dry-run backtesting and cohort evaluation
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

-- Shared cohort registry
CREATE TABLE IF NOT EXISTS cohorts (
    cohort_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    where_json TEXT NOT NULL,
    priority INTEGER NOT NULL,
    PRIMARY KEY (workflow_id, cohort_id)
);

-- Detection records: logged BEFORE any action is taken
CREATE TABLE IF NOT EXISTS detections (
    detection_id TEXT PRIMARY KEY,
    sim_minute INTEGER NOT NULL,
    ts TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    detector_type TEXT NOT NULL,  -- 'kpi_drift', 'caller_id_degraded', 'hallucination_spike', 'supervisor_edit'
    target_dim TEXT,
    target_value TEXT,
    metric_name TEXT NOT NULL,
    baseline_val REAL NOT NULL,
    current_val REAL NOT NULL,
    p_value REAL,
    evidence_json TEXT NOT NULL
);

-- Active and historical experiments
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    cohort_id TEXT NOT NULL,
    dim TEXT NOT NULL,            -- 'welcome_message', 'voice'
    control_arm TEXT NOT NULL,
    arms_json TEXT NOT NULL,      -- list of variant strings
    status TEXT NOT NULL,         -- 'DESIGNED', 'RUNNING', 'CONCLUDED', 'INVALIDATED'
    primary_kpi TEXT NOT NULL,
    guardrail_kpi TEXT NOT NULL,
    required_n_per_arm INTEGER NOT NULL,
    alpha REAL NOT NULL DEFAULT 0.05,
    power REAL NOT NULL DEFAULT 0.80,
    mde REAL NOT NULL DEFAULT 0.08,
    created_minute INTEGER NOT NULL,
    concluded_minute INTEGER
);

-- Experiment evaluations and statistical decisions
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
    decision TEXT NOT NULL,       -- 'WINNER', 'INFERIOR', 'INCONCLUSIVE', 'GUARDRAIL_VETO', 'SRM_FAILED'
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);

-- Write-ahead intent records for crash safety
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE NOT NULL,
    action TEXT NOT NULL,         -- 'request_approval', 'apply_approval', 'auto_apply', 'validate'
    workflow_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,         -- 'PENDING', 'EXECUTED', 'FAILED'
    created_at TEXT NOT NULL,
    executed_at TEXT
);

-- Proposal state machine
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    approval_id TEXT,
    workflow_id TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    status TEXT NOT NULL,         -- 'DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'EXPIRED', 'APPLIED', 'LAPSED', 'STALE'
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

-- Append-only decision audit store
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    sim_minute INTEGER NOT NULL,
    ts TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,  -- 'EXPERIMENT_LAUNCH', 'PROPOSAL_SUBMITTED', 'AUTO_APPLY', 'ROLLBACK', 'DID_ROTATION'
    inputs_ref_json TEXT NOT NULL, -- references to detection_id, experiment_id, analysis_id, proposal_id
    numbers_json TEXT NOT NULL,
    policy_clause TEXT NOT NULL,
    outcome TEXT NOT NULL
);

-- Snapshot of configuration versions
CREATE TABLE IF NOT EXISTS config_snapshots (
    workflow_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    actor TEXT NOT NULL,
    applied_at_minute INTEGER,
    config_json TEXT NOT NULL,
    PRIMARY KEY (workflow_id, version)
);

-- Credit ledger
CREATE TABLE IF NOT EXISTS credit_ledger (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_minute INTEGER NOT NULL,
    endpoint TEXT NOT NULL,
    cost REAL NOT NULL,
    credits_remaining REAL NOT NULL,
    purpose TEXT NOT NULL,
    tenant_id TEXT NOT NULL
);
```

---

## 5. Statistical Framework & Governance Rules

### 5.1 Sample Size & Power Calculation
For two-proportion testing between control rate $p_1$ and treatment rate $p_2 = p_1(1 + MDE)$:
$$n = \frac{\left(z_{\alpha/2}\sqrt{2\bar{p}(1-\bar{p})} + z_\beta\sqrt{p_1(1-p_1) + p_2(1-p_2)}\right)^2}{(p_2 - p_1)^2}$$
- Significance level: $\alpha = 0.05 \implies z_{\alpha/2} = 1.96$.
- Statistical power: $1 - \beta = 0.80 \implies z_\beta = 0.84$.
- Minimum Detectable Effect (MDE): $8\%$ relative lift on baseline rate.
- Fixed horizon: No peeking before reaching $n$ to avoid inflated false-positive error rates.

### 5.2 Hypothesis Testing & Multiple Comparisons
- **Two-proportion z-test**: Standard normal approximation for large samples ($n \ge 100$, $np \ge 5$).
- **Sample Ratio Mismatch (SRM)**: Pearson's $\chi^2$ goodness-of-fit on observed vs expected arm sample sizes. If $p < 0.001$, abort experiment (indicates traffic leak or allocation bug).
- **Multiple Testing Correction**: Holm-Bonferroni step-down correction across all candidate arms within an experiment to bound Family-Wise Error Rate (FWER) $\le 0.05$.
- **Guardrail Veto**: Guardrail KPI drop must be within policy tolerance:
  $$\Delta_{guardrail} = \hat{p}_{treatment} - \hat{p}_{control} \ge -\delta_{margin}$$
  If the lower bound of the $95\%$ CI drops below $-\delta_{margin}$ (e.g. $-0.02$), the variant is vetoed regardless of primary KPI lift.

### 5.3 Winner Promotion, Decay Detection & Rollback
- Promotion: Propose winner allocation through `/approvals` or auto-apply if within policy bounds.
- Holdout Arm: Keep 10% traffic in control arm or historical cohort baseline to monitor post-promotion performance.
- Decay Detection: Monitor trailing 48-hour performance using CUSUM or Wilson score confidence bounds. If performance drops below baseline for 2 consecutive windows, propose rollback to `known_good` baseline config.

### 5.4 Governance State Machine & Timing Rules
- **Fatigue Protection**: Cap proposals at $\le 2$ per 24 hours per workflow (even if policy allows up to 4). Batch related changes into a single daily digest.
- **Approver Hours**: Submit proposals strictly within approver working hours (e.g., 10:00–18:00 IST), avoiding early morning standups (Anita Desai: after 11:00 AM).
- **Stale Base Reconciliation**: If live `version > base_version` (due to supervisor edit), mark proposal `STALE`, cancel, re-evaluate against new base, and re-propose.

---

## 6. Scope Decisions & Cuts

### Floor (Mandatory Core — Fully Implemented)
- **C1 (Ingestion & Rollups)**: Idempotent chunk reading, deduplication by `event_id`, bounded hourly aggregations, zero memory leaks on `public_03_load`.
- **C2 (Shared Cohort Object)**: One unified `Cohort` model across detection, experimentation, dry-runs, and audit trails.
- **C5 (Caller-ID Health)**: Peer pickup rate analysis controlling for hour-of-day; safe rotation away from degraded DIDs.
- **C6 (Variant Experimentation)**: Rigorous A/B testing on welcome messages and voices with deterministic lead-level hashing, sample size discipline, SRM checks, and Holm-Bonferroni correction.
- **C7 (Winner Promotion, Decay & Rollback)**: Policy-compliant promotion, holdout tracking, decay detection, and automated rollback to last-known-good configuration.
- **C9 (Governance Client)**: Complete proposal lifecycle state machine, write-ahead intents, fatigue batching, timing synchronization with approver working hours, and stale-base handling.
- **C12 (Audit Store & `why` CLI)**: SQLite-backed decision store and command-line explanation tool tracing detection $\to$ experiment $\to$ analysis $\to$ proposal $\to$ apply $\to$ post-verification.
- **C13 (Crash Recovery)**: Atomic multi-step actions (intent $\to$ side-effect $\to$ record), demonstrated crash-recovery test script.

### Stretch Items (Added in Priority Order)
- **C8 (Hallucination Response)**: Detect prompt hallucination spike $\to$ call `POST /llm/suggest-fixes` $\to$ validate via `POST /validation/runs` ($pass\_rate \ge 0.97$) $\to$ propose with `validation_run_id`.
- **C10 (Honest Impact & Dry-Run)**: Replay targeting rules over historical lead cache; compute expected KPI delta with confidence intervals.
- **C11 (Supervisor View)**: Read-only CLI status summary consolidating active experiments, pending approvals, and daily config changes.
- **C4 (Call-Time Optimization)**: Cohort-level hour weights optimization within dialling window.

### Explicitly Cut (With Architectural Rationale)
- **C3 (History Bootstrapping before D0)**: Cut because pre-D0 historical data reflects default business configuration without multi-variant exploration. Online randomized experimentation provides unbiased causal inference.
- **C14 (Full Observability Stack)**: Cut external metrics daemons (Prometheus/Grafana); replaced with clean structured logging and 3 actionable on-call alerts.
- **C15 (Multi-Tenant Credit Auction)**: Cut full multi-tenant credit bidding engine; implemented a strict credit reservation ledger (30% emergency reserve floor, quota per workflow).

---

## 7. Key Risks & Mitigations

1. **Risk: Memory Exhaustion on `public_03_load` (~850k events/day)**
   - *Mitigation*: Stream events directly from chunk files without loading full chunks into memory; aggregate into SQLite `events_agg` hourly rollups in batched transactions (`executemany`).
2. **Risk: Stale Base Version Rejection (`409 stale_base_version`)**
   - *Mitigation*: Track `version` on every tick; verify `base_version` immediately before submitting or applying; on supervisor edit, cancel pending proposals and invalidate affected experiments.
3. **Risk: Approver Fatigue Rejections (`max_requests_per_24h`)**
   - *Mitigation*: Maintain internal 24-hour proposal counter per workflow; throttle submissions to max 2/day; batch multi-attribute changes into single proposals.
4. **Risk: Credit Exhaustion Mid-Incident**
   - *Mitigation*: Hard budget ceiling: reserve 30% of total credits solely for incident resolution (`POST /llm/suggest-fixes` and validation runs). Never call LLM APIs in routine experimentation.
5. **Risk: Crash Mid-Apply / Double Apply**
   - *Mitigation*: Write intent to SQLite with deterministic idempotency key before dispatching HTTP request; on startup, reconcile intents against simulator state (`GET /approvals`, `GET /workflows/{wid}/config`).
