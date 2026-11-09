# Grade report — test_generalization_v2

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| apex_collections | 0.405 | +54.8% | 0.247 | +42.4% | 0.570 | +3.8% | 32% | 59 (41) |
| horizon_intake | 0.542 | +10.2% | 0.289 | +2.0% | 0.600 | +7.5% | 37% | 55 (302) |
| ALL | 0.460 | +30.0% | 0.264 | +21.0% | 0.583 | +5.3% | 34% | 114 (343) |

## Changes applied by the controller

13 applied — beneficial: 9, harmful: 3, neutral: 1

- horizon_intake v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.076 → **beneficial**
- apex_collections v2 (controller_approved) dims=['voice'] engageΔ=+0.030 didΔ=+0.000 → **beneficial**
- horizon_intake v3 (controller_approved) dims=['voice'] engageΔ=+0.024 didΔ=+0.000 → **beneficial**
- apex_collections v3 (controller_approved) dims=['voice'] engageΔ=-0.029 didΔ=+0.000 → **harmful**
- apex_collections v4 (controller_approved) dims=['voice'] engageΔ=+0.030 didΔ=+0.000 → **beneficial**
- horizon_intake v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- apex_collections v5 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.760 → **beneficial**
- horizon_intake v6 (controller_approved) dims=['prompt_version'] engageΔ=+0.120 didΔ=+0.000 → **beneficial**
- apex_collections v6 (controller_approved) dims=['voice'] engageΔ=-0.029 didΔ=+0.000 → **harmful**
- apex_collections v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- apex_collections v9 (controller_approved) dims=['welcome_message'] engageΔ=-0.019 didΔ=+0.000 → **harmful**
- apex_collections v10 (controller_approved) dims=['welcome_message'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- apex_collections v11 (controller_approved) dims=['welcome_message'] engageΔ=+0.057 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 16,
  "decisions": {
    "approved": 14,
    "rejected": 1,
    "expired": 1
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 13,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "horizon_intake": 2,
    "apex_collections": 3
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
      "2": 0.504,
      "3": 0.175,
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
    "mttr_minutes": 210,
    "fixed_by": "controller_approved"
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

Credits used: 11 · validation runs: 4 · LLM fix calls: 1