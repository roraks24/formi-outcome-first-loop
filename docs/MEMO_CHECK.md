# Stakeholder Memo Verification & Conflict Analysis

This document examines the six stakeholder memos against the simulator contracts ([docs/SIMULATOR.md](SIMULATOR.md)), scenario configurations (`scenarios/*.yaml`), and platform constraints ([README.md](\.\./README.md)).

---

## 1. Nikhil Rao (VP Sales) — `stakeholders/01_sales.md`

### 1.1 What They Want
- A single blended lift figure per client to present at the BFSI summit ("the agent improved itself overnight").
- Real-time promotion: if a variant is winning by lunch, deploy to 100% of traffic by evening.
- Push winners straight through auto-apply for Kestrel Finance and Northwind KYC.
- Avoid "hesitant" governance (no requests every few hours).
- Roll out the "persuasive" prompt (P4) everywhere because it demoed well in another account.

### 1.2 Claims & Fact-Check

| Claim | Status | Evidence & Policy Analysis |
|---|---|---|
| *"One lift figure per client, ideally a single blended score"* | **FALSE / PROHIBITED** | [README.md §5 Constraint 3](\.\./README.md#L101-L102): *"Pickup and engagement stay separate KPIs. Optimise a workflow's primary KPI and respect its guardrail."* Blending obscures whether gains come from answering vs. engagement and risks violating regulatory guardrails. |
| *"If a variant is winning by lunch, it should be live for everyone by evening"* | **FALSE / DANGEROUS** | [README.md §5 Constraints 4 & 5](\.\./README.md#L103-L105): Winner selection must be statistical, deterministic, and reach minimum sample sizes. Half-day lunch samples suffer severe time-of-day bias (Simpson's paradox across morning vs evening leads) and high false-positive rates under unpeeking rules. |
| *"Kestrel has already turned on auto-apply for everything, so just push winners straight through there"* | **FALSE** | `scenarios/public_01_dev.yaml` line 108: `ks_cancel` governance sets `auto_apply.allowed_dims: [schedule, caller_id, welcome_message, voice]` and `max_shift: 0.2`. `prompt_version` and `cohorts` are **not permitted**, and allocation shifts are strictly capped at 20% total variation distance. 100% exploit shifts trigger HTTP `403 auto_apply_not_permitted`. |
| *"Northwind told me in our last QBR they're fine with auto-apply on the KYC flow too"* | **FALSE** | `scenarios/public_01_dev.yaml` line 73: `nw_kyc` governance sets `auto_apply: {enabled: false, allowed_dims: [], max_shift: 0}`. Calling auto-apply on `nw_kyc` immediately fails with `403 auto_apply_not_permitted`. In addition, Northwind Compliance explicitly demands named human approval for all customer-facing changes (`stakeholders/02_client_compliance.md`). |
| *"The 'persuasive' prompt tested well in another account's demo. I'd love to see it rolled out wherever it's available"* | **FALSE / RECKLESS** | Prompt catalog (`scenarios/public_01_dev.yaml` line 119, `scenarios/public_02_incidents.yaml` line 243) reveals prompt P4 ("Persuasive") carries a high hallucination rate (0.025, over 4x P1). In Northwind renewal, `min_validation_pass_rate` is 0.97; P4 fails validation and human review. In regulated BFSI, unverified prompt rollouts cause regulatory censure. |

### 1.3 Conflicts
- Directly conflicts with Meera Iyer (Compliance) on Northwind KYC auto-apply and prompt safety.
- Directly conflicts with Platform/SRE and Product on statistical rigor and separate KPIs.
- Directly conflicts with platform Non-Negotiable Constraints 1, 3, 4, 5.

---

## 2. Meera Iyer (Head of Compliance, Northwind Life) — `stakeholders/02_client_compliance.md`

### 2.1 What They Want
- Strict governance: no customer-facing change without named human approval (welcome message, voice, script).
- Operational adjustments (call timing, caller-ID rotation) allowed under signed policy, provided weekly summaries are provided.
- Script changes must carry validation evidence attached to the approval request.
- IRDAI auditability: ability to answer within 24 hours what any customer heard and why.
- Approver availability restricted to 10:00–19:00 (KYC: 10:00–18:00). Requests queued at night create friction.

### 2.2 Claims & Fact-Check

| Claim | Status | Evidence & Policy Analysis |
|---|---|---|
| *"No customer-facing change without a named human approval on our side"* | **TRUE** | Matches `nw_renewal` and `nw_kyc` policies: `nw_kyc` has `auto_apply.enabled: false`; `nw_renewal` only allows auto-apply on `[schedule, caller_id]`. |
| *"Script changes need validation evidence attached to the approval request"* | **TRUE** | [docs/SIMULATOR.md §4.1](SIMULATOR.md#L122-L124): The simulator approval engine automatically rejects any prompt change without `evidence.validation_run_id` passing `min_validation_pass_rate` (0.97). |
| *"Our approvers work 10:00–19:00 (KYC: 10:00–18:00). Requests sent at night sit in a queue"* | **TRUE** | Matches `approver_hours` in `nw_renewal` (`[10, 19]`) and `nw_kyc` (`[10, 18]`). Proposals sent at night do not get evaluated until the next morning and risk expiring if TTL is tight. |

### 2.3 Conflicts
- Rejects Nikhil Rao's request to auto-apply on KYC and to roll out prompt P4 without validation.
- Completely aligns with Platform Constraints 1, 6, and 7.

---

## 3. Kabir Menon (Platform & SRE Lead) — `stakeholders/03_platform_sre.md`

### 3.1 What They Want
- Crash survival: service must withstand `kill -9` at any moment and resume without double-applying or dropping approvals.
- Idempotency everywhere.
- Actionable on-call alerts only (no frivolous noise).
- Avoid new stateful infrastructure (existing stack: Postgres + Redis).
- Ingest and decide on 1 simulated day of `public_03_load` (< 10 wall-clock minutes on 4 vCPU / 8 GB).
- Loop health observability: ingest lag, decisions/hour, credit burn.

### 3.2 Claims & Fact-Check

| Claim | Status | Evidence & Policy Analysis |
|---|---|---|
| *"It must survive being killed at any moment... Idempotency everywhere"* | **TRUE** | Matches [README.md §5 Constraint 8](\.\./README.md#L108-L109). Proposal IDs must be idempotency keys; state transitions must be write-ahead logged. |
| *"Ingest and decide on one simulated day of public_03_load in under 10 wall-clock minutes on 4 vCPU / 8 GB"* | **TRUE** | Load scenario produces ~850k events/day (~10-15k events/min peak). Ingestion must achieve >1,500 events/second in batched SQLite WAL transactions. |
| *"I'd prefer no new stateful infrastructure"* | **QUALIFIED / DEFENSIBLE** | For the standalone evaluation runner, embedding SQLite with WAL mode requires zero operational overhead, zero external daemon processes, and guarantees single-process ACID durability. In production deployment, SQLite can be operated as a sidecar or migrated to Postgres. |

### 3.3 Conflicts
- High load requirement forces stream aggregation over unbounded raw lead storage, directly constraining how C1/C2/C10 are implemented.

---

## 4. Anita Desai (Operations Supervisor, Kestrel Finance) — `stakeholders/04_client_ops_supervisor.md`

### 4.1 What They Want
- Respect supervisor edits: she edits prompts during the day; controller must never overwrite them.
- Batching & fatigue: maximum 2 requests per day, never before 11:00 AM (due to standups), combined into a single digest.
- Transparency on running campaigns.
- One consolidated supervisor screen (active tests, winners, pending approvals, daily changes) rather than multiple alerts.
- Avoid verbatim compliance prompts that damage engagement (e.g. prompt P5).

### 4.2 Claims & Fact-Check

| Claim | Status | Evidence & Policy Analysis |
|---|---|---|
| *"I change prompts myself during the day... Please don't undo my changes behind my back"* | **TRUE** | [docs/SIMULATOR.md §4](SIMULATOR.md#L92-L95): Supervisor edits increment workflow `version`. Attempting to apply a proposal with an older base version returns `409 stale_base_version`. Constraint 1 prohibits silent overwriting. |
| *"Two requests a day, maximum, and never before 11:00"* | **PARTIALLY MISMATCHED WITH POLICY** | In `ks_cancel`, `max_requests_per_24h` is configured as 4, and `approver_hours` is `[10, 19]`. However, respecting her human constraints (<= 2/day, after 11:00 AM) prevents human rejections. |
| *"Last month a 'fix' ... made the bot read the compliance script word for word. Nobody hallucinated, and nobody stayed on the call either"* | **TRUE** | Prompt catalog shows P5 ("Strict: near-verbatim compliance script") has very low hallucination (0.003) but an engagement multiplier of 0.86 (a 14% drop in engagement). |

### 4.3 Conflicts
- Directly contradicts Nikhil Rao's claim that Kestrel wants rapid unthrottled auto-apply without human oversight.

---

## 5. Tara Kulkarni (Head of Product) — `stakeholders/05_product.md`

### 5.1 What They Want
- Strict separation of pickup and engagement KPIs.
- One shared Cohort object across detection, experiments, and supervisor views.
- Statistical cohort sizing and evaluation windows.
- Proposals as a formal product surface (summary, diff, affected leads, dry-run, honest impact score).
- Expired proposals treated as rejections (re-queue only with fresh data).
- Learning restricted strictly to actual runtime exposure (unapproved arms do not contribute synthetic evidence).
- Workflow isolation: winners apply only to their specific workflow.
- High-priority wishlist: per-person call-time (C4), supervisor view (C11), caller-ID health (C5).

### 5.2 Claims & Fact-Check

| Claim | Status | Evidence & Policy Analysis |
|---|---|---|
| *"Pickup and engagement are separate KPIs... Don't blend them"* | **TRUE** | Enforced by [README.md §5 Constraint 3](\.\./README.md#L101-L102). |
| *"A cohort is one shared object... same cohort with the same ID"* | **TRUE** | Required by C2 specification. Prevents inconsistent stratification across system modules. |
| *"An expired proposal is a rejection. Re-queue it with fresh data. Don't resubmit the stale one"* | **TRUE** | [docs/SIMULATOR.md §4.1](SIMULATOR.md#L128-L132): When TTL expires, base version or underlying cohort distribution may have shifted. |
| *"Wishlist, all of which I consider P0: per-person call-time... supervisor view... caller-ID health"* | **OVER-SCOPED** | Labeling all features P0 violates the reality of a 12–16 hour build budget. Per-person scheduling (C4) on up to 200k leads risks extreme variance and overfitting. Caller-ID health (C5) and supervisor digest (C11) take precedence. |

### 5.3 Conflicts
- Rebuffs Sales on blended metrics and cross-account prompt cloning.
- Conflicts with realistic time constraints; must be prioritized against core reliability.

---

## 6. Aryamann (CTO) — `stakeholders/06_cto.md`

### 6.1 What They Want
- Outcome explainability over unverified lift in regulated industries.
- Frugal credit spend: spend credits only where they change a decision; reserve budget for incident response.
- Architect for scale (hundreds of workflows, 650k dials/day); state what breaks first at 10x.
- Decisive scoping: state clear nos, document cuts, draft stakeholder responses.
- Production readiness: clean, maintainable, defensible architecture.
- Trust the simulator contract over human recollection.

### 6.2 Claims & Fact-Check
- All claims in the CTO memo are accurate and align 100% with the assignment specifications, simulator contracts, and engineering constraints.
