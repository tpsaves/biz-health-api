# Backtesting Methodology Notes

## Out-of-Time Train/Test Split

We use an **out-of-time split** — the test set contains closures that occurred strictly after the train set. This mirrors how the model would actually be used in production: trained on historical events, evaluated on future events.

**Cutoff: 2022-01-01**

| Set   | Closure dates | Restaurants | Count |
|-------|---------------|-------------|-------|
| Train | 2017–2021     | BARBEC'S, E RAE KOREAN, CANTINA LAREDO, LOYD'S OLD FASHION BURGER, GREEN PAPAYA | 5 |
| Test  | 2022–2023     | STEEL RESTAURANT & LOUNGE, MI PUEBLITO TAQUERIA Y PA, THE LAST STAND | 3 |

Rules and thresholds may only be derived from the **train set**. Any threshold tuned on test-set restaurants produces inflated performance numbers that will not generalize.

---

## Known Leakage: Composite Risk Cap

**Rule:** `if operational_score < 65 AND review_velocity_score < 30 → cap overall_score at 59`

**Status: FAIL (leakage)**

This rule was introduced in Phase 10 after observing that 3 restaurants scored in the moderate band but then closed within 180 days. Those 3 restaurants — STEEL, MI PUEBLITO, and THE LAST STAND — are in the **test set**. The cap was fitted by looking at the test set, not derived from the train set alone.

**Honest test-set performance (without composite cap):**
- Recall: 0% on test set (all 3 test restaurants would be classified moderate, not at-risk)
- This is the correct number to report to a model-risk reviewer

**Performance with composite cap (disclosed leakage):**
- Recall: 100% on test set
- This number should be reported with the caveat that it reflects in-sample tuning

**Path forward:** Retune the composite cap thresholds using only the 5 train-set restaurants. Since none of the train-set restaurants needed the cap (they already scored below 60 without it), a train-only-derived model would not include this cap at all. The cap is a hypothesis that needs validation on the prospective cohort (outcomes due September 2026).

---

## Reject-Inference Caveat

The prospective cohort of 107 DFW restaurants was selected by us — we chose which restaurants to score. This is **not** a random sample of restaurants a distributor would actually encounter in a credit-decision workflow.

In credit risk, this is called the **reject-inference problem**: the population we can evaluate (restaurants we chose to score) differs from the population the model would be applied to (all restaurants a distributor considers). Restaurants we never scored are absent from our validation data.

**Implication:** Our precision and recall numbers apply to restaurants similar to our prospective cohort selection, not necessarily to the full credit-decision population a distributor would face.

**Mitigation:** Once a design partner shares their actual restaurant receivables book, we can re-run validation on that population and compute unbiased performance estimates.

---

## Proxy-Outcome Caveat

Our outcome variable is **restaurant closure** (permanently closed Google Maps listing, TABC cancellation, or Dallas OpenData inspection gap).

Restaurant closure is a **proxy** for what distributors actually care about: **payment default or write-off on a net-terms receivable**. The two are correlated but not identical:

- A restaurant can close without defaulting (paid in full before closing)
- A restaurant can default without closing (financial stress but still operating)

**Implication:** Our closure-based recall and precision are an approximation of the payment-default recall that would actually matter to a distributor. The true metric requires partner receivables data.

**Current label in UI:** "closure rate (proxy outcome)" — this label should remain on all customer-facing materials until validated against real payment data.

---

## Known Limitations

1. **Small sample size:** 8 retrospective restaurants, train n=5, test n=3. No statistical claims about significance can be made. All precision/recall numbers are descriptive, not inferential.

2. **Retrospective bias:** Closed restaurants were identified post-hoc via inspection records and TABC cancellations. Restaurants that closed without leaving a data trail are not in our dataset.

3. **Historical reconstruction gaps:** Financial signals (SBA loans, property tax) and delivery platform data are not reconstructible at historical cutoffs. The retrospective model uses only review, inspection, and TABC signals — the full production model has more signals.

4. **Geographic concentration:** All restaurants are in the DFW metro area. Model performance may differ in other markets.

5. **Temporal scope:** Train set covers 2017–2021 (including COVID-era closures). Model behavior during economic disruptions may differ from baseline.

6. **Composite cap status:** The primary recall improvement (77.8% → 100%) came from a rule with known leakage. The train-set-only recall is 100% but the honest test-set recall without the leaked rule is 0%.

---

## What to Tell a Model-Risk Reviewer

> "We have a small retrospective dataset of 8 confirmed DFW restaurant closures. Our baseline model (no composite cap) achieves 100% recall on the 5-restaurant train set and 0% recall on the 3-restaurant out-of-time test set. We added a composite cap rule that brings test recall to 100%, but that rule was derived by observing the test-set restaurants — this is a known leakage we are disclosing. The cap is a hypothesis we are currently validating prospectively (107 restaurants, outcomes due September 2026). We are also seeking a design partner to provide actual payment-default ground truth, which is the metric that would matter to a food distributor."

---

*Last updated: 2026-06-04*
