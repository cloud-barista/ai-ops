# BurstGPT day 0–120 fixed transfer protocol

2026-09-28. This protocol was fixed before any day 61–120 prediction scores were computed. The `rolling_bin` method comes from the **post-hoc comparison of `BurstGPT_3.csv`**; this experiment asks whether that specific rule transfers to the earlier, nonoverlapping window from the same service. It is not an independent-service or fully blind test, and it is not evidence of academic algorithm novelty.

## Population, target, and timing

- Source assets and SHA256: [`BurstGPT_1.csv`, `BurstGPT_2.csv`](../data/external/burstgpt_v2_12/SOURCE.md). Use all rows with `Request tokens > 0`, including the 18 rows with `Response tokens = 0`. This is a request-side eligibility rule. Target: observed `Response tokens` in the CSV.
- `Timestamp` supplies relative day `floor(Timestamp / 86400)` and order only. Request-time features: `Model`, `Log Type`, and `log1p(Request tokens)` or the fixed raw-token bin. `Response tokens`, `Total tokens`, and any future outcomes are excluded from features. Assumption: the tokenized input length is available before the decision.
- Fit fixed references on file 1 days 0–59 only. Day 60 is excluded from fixed training because at the start of day 61 the batch-feedback rule below has not delivered it. All eligible rows on file 2 days 61–120 are the held-out test. No validation selection, hyperparameter tuning, or revision after test scores is permitted.
- **Batch feedback assumption:** At the start of prediction day `d`, all outputs from request days `≤ d−2` are available. Rolling history is the seven request days `d−8` through `d−2`, inclusive. Day 60 therefore first enters a rolling window on day 62. The source does not record completion or delivery times, so this is a sensitivity assumption and not a verified online guarantee.

## Locked methods

All empirical p90s use NumPy `quantile(..., 0.9, method="higher")` and all predictions are nonnegative. A cohort is `Model × Log Type`. A missing cohort falls back to the fixed global p90; a sparse bin falls back to the rolling cohort p90.

1. **Primary: `rolling_bin`**, the prior study's fixed rule. Compute output p90 in the available seven days by cohort × fixed input-token bin: `1–64`, `65–256`, `257–1024`, `1025–4096`, `>4096`. If a cell has fewer than 100 rows, use its available seven-day cohort p90. If that cohort is empty, use its fixed cohort p90.
2. `rolling_cohort`: available seven-day cohort p90, with fixed-cohort/global fallback.
3. `static_cohort`: file 1 days 0–59 cohort p90, with fixed-global fallback.
4. `static_global`: file 1 days 0–59 global p90.
5. `hgb_base`: a fixed `HistGradientBoostingRegressor` directly predicting p90 from two categorical request-side features plus `log1p(Request tokens)`. Hyperparameters stay at quantile `.9`, 140 iterations, 15 maximum leaf nodes, 60 minimum leaf samples, L2 10, learning rate `.05`, no early stopping, seed 42. Category codes are learned only on days 0–59; unknown categories use missing category code. No update occurs on the test period.

The **primary method is fixed as `rolling_bin`**. The manifest records the method and hashes of source, protocol, code, and fitted HGB *before* day 61–120 outcomes are scored. The four other methods are fixed comparators; the best comparator is not selected after seeing test scores.

## Scores and predeclared interpretation

Use the same eligible test rows for each method. Report row-weighted and equal-day-weighted p90 pinball loss (tokens), empirical coverage `P(Response tokens ≤ predicted)`, mean predicted tokens, per-day values, all four cohort values, and six successive 10-day blocks: 61–70, 71–80, 81–90, 91–100, 101–110, 111–120. Cohort equal-day loss averages over days where that cohort occurs; block equal-day loss gives each of its 10 days the same weight. Coverage within cohorts and blocks is request-weighted. Pinball loss for target `y` and prediction `p` is `.9 max(y−p, 0) + .1 max(p−y, 0)`.

- **Relative-loss transfer criterion:** `rolling_bin` must have lower equal-day-weighted pinball loss than each of `rolling_cohort`, `static_cohort`, and `hgb_base` on all 60 test days combined; it must also have lower equal-day-weighted loss than `rolling_cohort` in at least five of the six 10-day blocks and in every one of the four full-period cohorts. This is a descriptive replication gate, not a probability guarantee.
- **Safety-upper-bound criterion:** `rolling_bin` empirical coverage must be at least 90% on all test requests, each 10-day block, and each of the four full-period cohorts. Failure on any part means no capacity-bound adoption, even if pinball loss improves.

These gates are reported separately. The trace lacks GPU/VM, model-memory coefficients, user SLOs, and action outcomes, so even satisfying both gates would justify only further output-length research, not a real deployment, GPU-size, or scaling claim.
