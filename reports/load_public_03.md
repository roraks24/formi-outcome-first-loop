# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.337 | +0.1% | 0.244 | +0.2% | 0.496 | -0.1% | -5% | 370 (409) |
| arav_kyc_1 | 0.354 | -1.4% | 0.215 | -0.3% | 0.595 | -1.0% | 0% | 352 (352) |
| arav_renewal_0 | 0.413 | +1.8% | 0.263 | -0.2% | 0.558 | +1.9% | 10% | 447 (408) |
| kest_cancel_2 | 0.335 | -0.6% | 0.244 | +0.7% | 0.493 | -1.2% | -5% | 396 (418) |
| kest_kyc_1 | 0.356 | +0.0% | 0.213 | -0.4% | 0.598 | +0.1% | 0% | 338 (370) |
| kest_renewal_0 | 0.411 | +2.3% | 0.263 | -0.6% | 0.556 | +2.2% | 10% | 418 (403) |
| nort_cancel_2 | 0.339 | +0.0% | 0.245 | +0.3% | 0.497 | -0.2% | 0% | 381 (368) |
| nort_kyc_1 | 0.360 | +1.1% | 0.214 | -0.6% | 0.604 | +1.4% | 8% | 338 (318) |
| nort_renewal_0 | 0.419 | +3.3% | 0.265 | +0.4% | 0.561 | +2.4% | 10% | 438 (419) |
| tide_cancel_2 | 0.339 | -1.3% | 0.243 | -0.4% | 0.497 | -0.7% | -5% | 407 (401) |
| tide_kyc_1 | 0.361 | +1.2% | 0.215 | -0.0% | 0.605 | +1.1% | 6% | 378 (376) |
| tide_renewal_0 | 0.412 | +1.9% | 0.265 | +0.2% | 0.554 | +1.5% | 10% | 411 (425) |
| ALL | 0.370 | +0.8% | 0.241 | -0.1% | 0.549 | +0.7% | 4% | 4674 (4667) |

## Changes applied by the controller

17 applied — neutral: 7, beneficial: 7, harmful: 3

- nort_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- kest_cancel_2 v2 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_cancel_2 v2 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- arav_cancel_2 v2 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['voice'] engageΔ=+0.028 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- kest_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- tide_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nort_kyc_1 v2 (controller_approved) dims=['voice'] engageΔ=+0.028 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- tide_cancel_2 v3 (controller_approved) dims=['welcome_message'] engageΔ=-0.014 didΔ=+0.000 → **harmful**
- nort_renewal_0 v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- kest_renewal_0 v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v3 (controller_approved) dims=['welcome_message'] engageΔ=-0.014 didΔ=+0.000 → **harmful**
- arav_cancel_2 v3 (controller_approved) dims=['welcome_message'] engageΔ=-0.014 didΔ=+0.000 → **harmful**
- nort_kyc_1 v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.031 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 21,
  "decisions": {
    "approved": 17,
    "rejected": 4,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 4
  },
  "applied_via_approval": 17,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_renewal_0": 1,
    "nort_kyc_1": 1,
    "nort_cancel_2": 1,
    "kest_renewal_0": 1,
    "kest_kyc_1": 1,
    "kest_cancel_2": 1,
    "arav_renewal_0": 1,
    "arav_kyc_1": 1,
    "arav_cancel_2": 1,
    "tide_renewal_0": 1,
    "tide_kyc_1": 1,
    "tide_cancel_2": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0
## Load & Scalability Benchmark Metrics

- **Total Events Ingested**: 4,200,098
- **Total Wall-Clock Time**: 147.87 seconds (29.57 s / simulated day)
- **Throughput**: 28,404.5 events/second
- **Peak Controller RSS**: 120.79 MB (strictly bounded O(1) in RAM via hourly rollups)
- **Lag**: 0 ticks (processed synchronously per tick)
- **Database Size**: 152.26 MB

### 10x Scale Bottleneck Analysis
At 10x load (~6.5M dials/day, ~32M events/week):
1. **SQLite Single-Writer Lock Contention**: While SQLite WAL allows concurrent readers, multiple parallel ingestion threads would serialize on write transactions. Solution: partitioned sharding by workflow_id or DuckDB/PostgreSQL.
2. **Python NDJSON Streaming CPU Ceiling**: Parsing JSON strings in single-threaded Python hits a throughput ceiling around 80k-100k events/sec. Solution: `orjson` or C-accelerated chunk parser with batch insert.
3. **Rollup Cardinality Boundedness**: Ingestion memory remains O(1) in total events, but scales with distinct (workflow, cohort, caller_id, hour) tuples. Even with 100 workflows, 24 hours x 100 x 4 = ~9,600 rows/day, which easily fits in a few megabytes of RAM.
