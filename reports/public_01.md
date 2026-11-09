# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.337 | -1.2% | 0.242 | -1.2% | 0.492 | +0.1% | -1% | 71 (57) |
| nw_kyc | 0.369 | +0.8% | 0.243 | +0.4% | 0.599 | +0.3% | 0% | 24 (21) |
| nw_renewal | 0.416 | -0.1% | 0.264 | -0.3% | 0.542 | +0.2% | 3% | 45 (54) |
| ALL | 0.370 | -0.4% | 0.250 | -0.5% | 0.531 | +0.2% | 1% | 140 (132) |

## Changes applied by the controller

15 applied — neutral: 11, harmful: 2, beneficial: 2

- ks_cancel v2 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_auto) dims=['welcome_message'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_renewal v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.010 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_auto) dims=['welcome_message'] engageΔ=-0.006 didΔ=+0.000 → **neutral**
- nw_renewal v4 (controller_approved) dims=['welcome_message'] engageΔ=-0.009 didΔ=+0.000 → **neutral**
- ks_cancel v6 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v5 (controller_approved) dims=['voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- ks_cancel v7 (controller_auto) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v8 (controller_auto) dims=['welcome_message'] engageΔ=-0.020 didΔ=+0.000 → **harmful**
- nw_renewal v6 (controller_approved) dims=['voice'] engageΔ=-0.002 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.030 didΔ=+0.000 → **beneficial**
- ks_cancel v9 (controller_auto) dims=['welcome_message'] engageΔ=+0.020 didΔ=+0.000 → **beneficial**
- nw_renewal v8 (controller_approved) dims=['welcome_message'] engageΔ=-0.029 didΔ=+0.000 → **harmful**

## Governance

```
{
  "approval_requests": 8,
  "decisions": {
    "approved": 7,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 7,
  "auto_applied": 8,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 2,
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

Credits used: 0 · validation runs: 0 · LLM fix calls: 0