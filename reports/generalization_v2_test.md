# Grade report â€” test_generalization_v2

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Î” | pickup | Î” | engagement | Î” | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| apex_collections | 0.365 | +39.5% | 0.226 | +30.2% | 0.569 | +3.5% | 32% | 47 (41) |
| horizon_intake | 0.545 | +10.8% | 0.293 | +3.5% | 0.595 | +6.7% | 37% | 65 (302) |
| ALL | 0.437 | +23.6% | 0.253 | +16.0% | 0.581 | +4.9% | 34% | 112 (343) |

## Changes applied by the controller

8 applied â€” beneficial: 6, harmful: 1, neutral: 1

- horizon_intake v2 (controller_approved) dims=['caller_id'] engageÎ”=+0.000 didÎ”=+0.076 â†’ **beneficial**
- apex_collections v2 (controller_approved) dims=['voice'] engageÎ”=+0.030 didÎ”=+0.000 â†’ **beneficial**
- horizon_intake v3 (controller_approved) dims=['voice'] engageÎ”=+0.024 didÎ”=+0.000 â†’ **beneficial**
- apex_collections v3 (controller_approved) dims=['voice'] engageÎ”=-0.029 didÎ”=+0.000 â†’ **harmful**
- apex_collections v4 (controller_approved) dims=['welcome_message'] engageÎ”=+0.020 didÎ”=+0.000 â†’ **beneficial**
- horizon_intake v4 (controller_approved) dims=['welcome_message'] engageÎ”=+0.000 didÎ”=+0.000 â†’ **neutral**
- horizon_intake v6 (controller_approved) dims=['prompt_version'] engageÎ”=+0.120 didÎ”=+0.000 â†’ **beneficial**
- apex_collections v6 (controller_approved) dims=['caller_id'] engageÎ”=+0.000 didÎ”=+0.795 â†’ **beneficial**

## Governance

```
{
  "approval_requests": 14,
  "decisions": {
    "approved": 8,
    "rejected": 4,
    "expired": 2
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 4
  },
  "applied_via_approval": 8,
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
      "2": 0.502,
      "3": 0.487,
      "4": 0.372,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "horizon_intake",
    "at": "D3 11:30",
    "mttr_minutes": 180,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "apex_collections",
    "at": "D4 14:00"
  }
]
```

## Decaying variants â€” share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 11 Â· validation runs: 4 Â· LLM fix calls: 1
