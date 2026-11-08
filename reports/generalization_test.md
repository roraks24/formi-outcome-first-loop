# Grade report — test_02_generalization

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.353 | +2.7% | 0.241 | -0.1% | 0.500 | +2.4% | 8% | 55 (58) |
| nw_kyc | 0.369 | -0.8% | 0.241 | +0.2% | 0.592 | -0.7% | 15% | 29 (32) |
| nw_renewal | 0.471 | +51.1% | 0.287 | +41.6% | 0.549 | +3.5% | 26% | 114 (240) |
| ALL | 0.396 | +16.7% | 0.257 | +12.8% | 0.535 | +2.4% | 19% | 198 (330) |

## Changes applied by the controller

7 applied — beneficial: 4, neutral: 3

- nw_renewal v2 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.104 → **beneficial**
- ks_cancel v2 (controller_approved) dims=['voice'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.009 didΔ=+0.000 → **neutral**
- nw_renewal v4 (controller_approved) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nw_renewal v5 (controller_approved) dims=['prompt_version'] engageΔ=+0.136 didΔ=+0.000 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.035 didΔ=+0.000 → **beneficial**

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
  "applied_via_approval": 7,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 3,
    "nw_kyc": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "nw_renewal",
    "at": "D3 10:00",
    "flagged_did_share_by_day": {
      "3": 0.0,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0,
      "8": 0.0,
      "9": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D4 11:30",
    "mttr_minutes": 1530,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D6 14:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "7": 0.201,
    "8": 0.194,
    "9": 0.201
  }
}
```

Credits used: 10 · validation runs: 2 · LLM fix calls: 2
## Generalization Incident Handling Verification

- **Total Detections Logged**: 78
- **Total Proposals Executed**: 10

### Incident Triage Summary

- Min 660: Workflow `nw_renewal` flagged by `caller_id_degraded` on `caller_id=D1`
- Min 6825: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6840: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6855: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6870: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6885: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6900: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6915: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6930: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6945: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6960: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6975: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 6990: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7005: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7020: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7035: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7050: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7065: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7080: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7095: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7110: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7125: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7140: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7155: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7170: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7185: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7200: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7215: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7230: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7245: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7260: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7275: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7290: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7305: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7320: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7335: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7350: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7365: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7380: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7395: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7410: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7425: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7440: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7455: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7470: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7485: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7500: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7515: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7530: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7545: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7560: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7575: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7590: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7605: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7620: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7635: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7650: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7665: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7680: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7695: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7710: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7725: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7740: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7755: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7770: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7785: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7800: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7815: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7830: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7845: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7860: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7875: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7890: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7905: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7920: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7935: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7950: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`
- Min 7965: Workflow `nw_renewal` flagged by `hallucination_spike` on `prompt_version=P2`

### Proposals Summary

- Proposal `prop_9eb5a9107825` (nw_renewal): Status **APPLIED** (applied v2) — Rotate caller ID on nw_renewal: disable degraded D1, use D2, D3, D4
- Proposal `prop_733e9962fcfe` (nw_renewal): Status **rejected** (applied vNone) — A/B test on nw_renewal (voice): V1 vs V2, V3
- Proposal `prop_4f6f840f6072` (nw_kyc): Status **APPLIED** (applied v2) — A/B test on nw_kyc (voice): V1 vs V2
- Proposal `prop_726566d389ed` (ks_cancel): Status **APPLIED** (applied v2) — A/B test on ks_cancel (voice): V1 vs V2, V3
- Proposal `prop_a708894ac302` (ks_cancel): Status **APPLIED** (applied v3) — A/B test on ks_cancel (welcome_message): W1 vs W2, W3
- Proposal `prop_476300a69578` (nw_kyc): Status **rejected** (applied vNone) — A/B test on nw_kyc (welcome_message): W1 vs W2
- Proposal `prop_ff195fcb6f08` (nw_renewal): Status **APPLIED** (applied v4) — A/B test on nw_renewal (voice): V1 vs V2, V3
- Proposal `prop_eaf4955296e4` (nw_renewal): Status **rejected** (applied vNone) — Remediate hallucination on nw_renewal: replace P2 with validated P5
- Proposal `prop_c7b6b0b66908` (nw_renewal): Status **APPLIED** (applied v5) — Remediate hallucination on nw_renewal: replace P2 with validated P3
- Proposal `prop_9097d64b1b56` (nw_renewal): Status **APPLIED** (applied v6) — A/B test on nw_renewal (welcome_message): W1 vs W2, W3
