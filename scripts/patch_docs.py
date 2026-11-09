with open('SUBMISSION.md', 'r', encoding='utf-8') as f:
    text = f.read()

# Normalize CRLF
is_crlf = '\r\n' in text
text_norm = text.replace('\r\n', '\n')

old_fwer = """- **Multiple Testing Correction**: Holm-Bonferroni adjustment across all treatment arms: candidate p-values are sorted $p_{(1)} \le p_{(2)} \dots \le p_{(m)}$ and tested sequentially against $\\alpha / (m - i + 1)$.
- **False-Winner Control**: Family-wise error rate is strictly capped at $\\alpha = 0.05$."""

new_fwer = """- **Multiple Testing Correction**: Holm-Bonferroni step-down adjustment across candidate treatment arms: candidate p-values are sorted $p_{(1)} \le p_{(2)} \dots \le p_{(m)}$ and tested sequentially against $\\alpha / (m - i + 1)$. This guarantees Family-Wise Error Rate (FWER) $\\le 0.05$ at experiment conclusion across multiple treatments.
- **Early Harm-Stop (Alpha Spending)**: During the experiment run, conservative one-sided interim looks are evaluated. To prevent bleeding on inferior variants without inflating false halts, interim looks use a conservative alpha spending boundary ($\\alpha_{\\text{interim}} = 0.01$). If a candidate treatment exhibits statistically significant degradation on primary KPI ($\\Delta < -0.02, p < 0.01$) or breaches the guardrail drop threshold ($p < 0.01$), the arm is halted immediately, reverting allocation to 100% control."""

assert old_fwer in text_norm, "old_fwer not found"
text_norm = text_norm.replace(old_fwer, new_fwer, 1)

old_c10 = "| **C10** | Impact Scores & Dry-Run | **Built** | Pre-computes affected lead count, configuration diff, expected"
new_c10 = "| **C10** | Priority Tiers & Dry-Run | **Built** | Pre-computes affected lead count (with 95% CI), configuration diff, priority tier (Tier 1 incident 0.7-0.9 to Tier 4 routine 0.2-0.35),"
assert old_c10 in text_norm, "old_c10 not found"
text_norm = text_norm.replace(old_c10, new_c10, 1)

old_example = """   - Impact Score: 0.4 | Affected Leads: 1500
   - Rationale: Pickup rate on D2 dropped to 0.201 vs peer baseline 0.248 (p=0.0). Rotating traffic to healthy DIDs ['D1', 'D3']."""

new_example = """   - Priority Tier: 0.70 (Tier 1 - Incident Remediation) | Affected Leads: 1500 ± 78 (95% CI)
   - Rationale: Pickup rate on D2 dropped to 0.201 vs peer baseline 0.248 (p=0.0). Expected gain: +0.047 lift on 1500 ± 78 leads (~70.5 ± 3.7 connects/day). Rotating traffic to healthy DIDs ['D1', 'D3']."""
assert old_example in text_norm, "old_example not found"
text_norm = text_norm.replace(old_example, new_example, 1)

old_rajesh = "We declined because pickup and engagement represent independent funnels; blending them obscures caller-ID degradation and produces false winners."
new_rajesh = "We declined because pickup rate (connected/dials) and engagement rate (engaged/connected) represent independent funnels; blending them obscures caller-ID degradation and produces false winners."
assert old_rajesh in text_norm, "old_rajesh not found"
text_norm = text_norm.replace(old_rajesh, new_rajesh, 1)

if is_crlf:
    text_final = text_norm.replace('\n', '\r\n')
else:
    text_final = text_norm

with open('SUBMISSION.md', 'w', encoding='utf-8') as f:
    f.write(text_final)

print("SUBMISSION.md successfully updated!")
