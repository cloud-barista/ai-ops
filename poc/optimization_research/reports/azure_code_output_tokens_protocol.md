# Frozen protocol: Azure Coding output-token p90, v1

Status: **specified before complete-file analysis and before inspecting held-out output labels**. The source itself has only three columns; this is a new within-Azure experiment on an independent service, **not** a transfer score for the previously fitted BurstGPT model. Azure has no counterpart of BurstGPT's `Model` or `Log Type` features. The method reuses the *principle* of an input-length-conditioned rolling upper quantile; it learns new bin edges and parameters on Azure training data. No tuning or method choice will use held-out output labels. The initial v1 protocol was written before the full download. Pre-test review then added a full-training-data static-bin comparator and moved held-out output validity checks after model freeze. A second pre-test review, before input audit or model preparation, added the observability and sampling assumptions below. No held-out output was inspected during either revision.

## Source and split

- Source: Microsoft Azure [LLM inference trace 2024](https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2024.md), Coding CSV in the [official release](https://github.com/Azure/AzurePublicDataset/releases/tag/dataset-llm-2024), CC-BY Attribution.
- Exact asset: `AzureLLMInferenceTrace_code_1week.csv`, 691,989,454 bytes, official SHA-256 `71de5c55cbc35f8f1ed0b6b7806b4cd1e9764b0058469725a6aac98023a1448f` (GitHub release API digest). Header exactly `TIMESTAMP,ContextTokens,GeneratedTokens`; first/last tiny byte-range checks indicated 2024-05-10 through 2024-05-16 UTC. Verify full SHA-256 before any analysis.
- Day 0 is 2024-05-10 UTC. Fixed training days 0–3 (May 10–13); fixed test days 4–6 (May 14–16). No validation-based selection: there are only seven days. Score all three test days, including zeros in `GeneratedTokens`.
- Eligibility depends only on request-time input: `ContextTokens > 0`. The pre-freeze input-only audit aborts on malformed timestamps, out-of-range dates, unsorted rows, and missing/negative/noninteger **input** token counts. Training-output validity is checked during preparation, and held-out-output validity (missing/negative/noninteger) is checked only in the frozen test stage. Report input-side eligibility and counts by day before preparing any model. Do not inspect test output values, distributions, or statistics before the model/manifest freeze.
- This is a request-time predictor **only if input tokenization has completed before the decision** and the resulting `ContextTokens` is available to it. The public trace records input length, but does not show when tokenization or the resource decision occurred.
- The [accompanying paper](https://arxiv.org/html/2408.00741) says these shared traces are a **subset of production invocations**. Completeness of the request-submission and failed-request populations is unverified. Report results conditional on the released Coding-service sample, without extrapolating to all submissions or failures.

## Fixed methods

All methods predict the 90th percentile of `GeneratedTokens` from information available at the start of each test day. Empirical quantiles use NumPy `method="higher"`, and predictions are nonnegative. Bin edges use **only eligible training `ContextTokens`**: four empirical quantiles at 20/40/60/80%, also with `method="higher"`. Sort and deduplicate equal edges; this yields at most five bins, with `np.searchsorted(edges, length, side="left")`, so each boundary value belongs to the lower bin. Save the actual edges in the frozen manifest. These data-derived edges replace the fixed absolute BurstGPT token thresholds; this is an explicit rule change for the new experiment.

1. **Primary `rolling_bin`**: for each request, predict the empirical p90 of past outputs in its training-derived input-length bin. For decision day `d`, historical request days are `[max(0,d-8), d-2]` inclusive, a seven-day maximum window with a two-day label-availability delay. When a bin has fewer than 100 eligible historical requests, use `rolling_global`.
2. **`rolling_global`**: empirical p90 of all eligible historical outputs in the same window and feedback rule.
3. **`static_hgb_length`**: `HistGradientBoostingRegressor(loss="quantile", quantile=0.9, max_iter=140, max_leaf_nodes=15, min_samples_leaf=60, l2_regularization=10, learning_rate=0.05, early_stopping=False, random_state=42)` on `log1p(ContextTokens)` only. Fit once on a reproducible uniform-without-replacement sample of at most 50,000 eligible requests from each of training days 0–3 (seed 42), then freeze. This 200,000-row cap keeps the fit practical and is part of the protocol. Clip predictions below zero to zero.
4. **`static_bin`**: empirical p90 of **all** eligible training outputs in each of the identical training-derived input-length bins, frozen after days 0–3. Use `static_global` if a bin has fewer than 100 eligible training requests. This controls for binning itself and for the HGB's 200,000-row training cap.
5. **`static_global`**: empirical p90 of all eligible outputs on training days 0–3; one frozen value.

For day 4, history is days 0–2; for day 5, days 0–3; for day 6, days 0–4. Thus day-4 test outputs may inform the day-6 rolling prediction under the stated two-day delayed-feedback assumption, but day-5 and day-6 outputs cannot. The trace records invocation times, not actual feedback times, so this is a declared simulation assumption.

## Metrics and decision gates

For each method, report all-row pinball loss in output-token units at quantile 0.9, the **equal-weight mean of the three daily pinball losses**, empirical coverage `P(output <= prediction)` overall and per day, mean predicted token budget overall and per day, and eligible row counts. Report primary and comparator values per input-length bin as a diagnostic, without selecting a different method. Pinball loss is `0.9*(y-p)` if `y>=p`, else `0.1*(p-y)`.

The predeclared **success gate** for `rolling_bin` requires all of:

1. Its equal-day mean pinball loss is strictly below each of `rolling_global`, `static_hgb_length`, `static_bin`, and `static_global`.
2. It beats `rolling_global` on at least two of the three individual test days by daily pinball loss.
3. Its coverage is at least 90% overall **and** on each of the three test days.

Otherwise report a failed gate and the full comparison; no retuning or alternative gate on this holdout. This is evidence only about output-length budgeting on this Azure service. The trace lacks model IDs, latency, SLOs, hardware telemetry, and deployment/scale actions, so no VM-sizing or autoscaling benefit can be measured from it.

## Freeze sequence

1. Write this protocol and implementation/tests, then record their SHA-256 hashes.
2. Download only the Coding asset, verify its official SHA-256/size, and run an input-only full-file audit (date/order/input counts). No test output statistics.
3. Read labels for training days 0–3 only; save training-derived edges, static empirical p90, fitted HGB model, source/protocol/script hashes, split, method parameters, and training counts to an immutable versioned artifact directory. Send the manifest and hashes for independent review **before** any held-out output label is parsed.
4. After review, execute a separate test command that verifies frozen hashes, reads test labels sequentially by day, and scores the predeclared comparisons.
