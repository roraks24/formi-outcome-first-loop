# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.349 | +2.1% | 0.242 | -1.1% | 0.507 | +3.0% | 3% | 59 (57) |
| nw_kyc | 0.372 | +1.4% | 0.246 | +1.5% | 0.596 | -0.3% | 6% | 25 (21) |
| nw_renewal | 0.407 | -2.1% | 0.260 | -1.5% | 0.539 | -0.4% | 4% | 49 (54) |
| ALL | 0.373 | +0.4% | 0.249 | -0.8% | 0.535 | +1.1% | 4% | 133 (132) |

## Changes applied by the controller

6 applied — neutral: 4, beneficial: 2

- ks_cancel v2 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['voice'] engageΔ=+0.014 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_auto) dims=['welcome_message'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_renewal v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.010 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 4,
  "decisions": {
    "approved": 4,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 4,
  "auto_applied": 2,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1
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

Credits used: 5 · validation runs: 1 · LLM fix calls: 1