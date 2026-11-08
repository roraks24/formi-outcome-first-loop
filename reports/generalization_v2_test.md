# Grade report — test_generalization_v2

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| apex_collections | 0.414 | +58.2% | 0.255 | +47.3% | 0.560 | +1.9% | 30% | 67 (41) |
| horizon_intake | 0.550 | +11.9% | 0.288 | +1.8% | 0.614 | +10.1% | 43% | 172 (302) |
| ALL | 0.468 | +32.4% | 0.269 | +23.2% | 0.584 | +5.4% | 35% | 239 (343) |

## Changes applied by the controller

6 applied — beneficial: 6

- horizon_intake v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.074 → **beneficial**
- apex_collections v2 (controller_approved) dims=['voice'] engageΔ=+0.030 didΔ=+0.000 → **beneficial**
- apex_collections v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.773 → **beneficial**
- horizon_intake v4 (controller_approved) dims=['voice'] engageΔ=+0.024 didΔ=+0.000 → **beneficial**
- horizon_intake v5 (controller_approved) dims=['prompt_version'] engageΔ=+0.174 didΔ=+0.000 → **beneficial**
- apex_collections v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 10,
  "decisions": {
    "approved": 7,
    "rejected": 3,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 3
  },
  "applied_via_approval": 6,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "horizon_intake": 3,
    "apex_collections": 2
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "apex_collections",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.256,
      "3": 0.0,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "horizon_intake",
    "at": "D3 11:30",
    "mttr_minutes": null,
    "fixed_by": null
  },
  {
    "type": "supervisor_edit",
    "workflow": "apex_collections",
    "at": "D4 14:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 10 · validation runs: 2 · LLM fix calls: 2
