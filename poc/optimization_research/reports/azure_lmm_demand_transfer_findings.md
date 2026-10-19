# Azure LMM input-demand transfer experiment: findings

Status: **EXPLORATORY NO-GO** under the predeclared gate. The experiment trained two LMM-specific q90 predictors and tested whether adding image/request history improves on the existing Azure Coding model and fixed baselines.

## Research question

Does the frozen Azure Coding next-five-minute input-token model transfer to the Azure LMM service? Does adding past image and request counts improve an LMM-specific predictor of the next five-minute ContextTokens total?

The target is the sum of ContextTokens in the next five-minute interval. This is a sampled input-demand forecast, not a GPU/VM count, user-latency SLO, or scale-action outcome.

## Data and split

The official Azure LMM trace contains 1,000,000 requests over exactly seven days. Its first observed request is 2024-10-15 12:00:00.269 UTC; the last is 2024-10-22 11:59:59.964 UTC. All 2,016 five-minute bins contain at least one sampled request. The compressed CSV is 8.13 MB.

The data audit found no negative NumImages or ContextTokens values. The model reads only TIMESTAMP, NumImages, and ContextTokens. It does not read GeneratedTokens or prompt content. Each five-minute prediction uses only completed earlier bins, with a 1,000-token minimum prior-hour mean.

The fixed chronological split is:

- Train: 2024-10-15 12:00 to 2024-10-18 12:00 UTC; 852 training bins after burn-in.
- Validation: 2024-10-18 12:00 to 2024-10-19 12:00 UTC; 288 bins.
- Assessment: 2024-10-19 12:00 to 2024-10-22 12:00 UTC; three consecutive 24-hour blocks, 864 bins total.

All train, validation, and assessment bins passed the support rule. The three assessment blocks each had 288 eligible bins.

The original draft assumed a midnight start. The pre-score range check found the trace starts at noon, so that attempt stopped before aggregation, training, or scoring. Its freeze is preserved as freeze_superseded_before_score.json. The corrected noon-to-noon protocol was frozen before bin aggregation and scoring. No scores from the superseded draft were produced.

## Methods

The frozen Coding HistGradientBoosting quantile model was applied unchanged as the cross-service transfer baseline. Two LMM models used the same fixed HGB settings and relative-demand objective:

1. Token-only HGB: existing Coding token-history features, retrained on the LMM training period.
2. Modality HGB: the same model with additional past-only image-count and request-count features.

The comparisons also included four fixed non-ML baselines: a training-weighted relative q90, prior-hour empirical q90, previous-day relative demand, and previous-day demand plus a training residual q90. The primary score is absolute-token q90 pinball loss; lower is better. Coverage is the fraction of observed five-minute token totals at or below the q90 prediction.

## Assessment results

| Method | Equal-block mean q90 pinball (tokens) |
|---|---:|
| Frozen Coding model, transferred unchanged | 139,071.776 |
| LMM token-only HGB | 104,651.470 |
| LMM modality HGB | **103,132.553** |
| Training-weighted relative q90 baseline | 112,425.292 |
| Prior-hour empirical q90 baseline | 119,464.220 |
| Previous-day relative-demand baseline | 299,529.018 |
| Previous-day plus training-residual baseline | 149,343.895 |

The modality model reduced pinball loss by 25.84% relative to the frozen Coding model and by 8.27% relative to the strongest simple baseline. Adding modality features reduced loss by only 1.45% relative to the LMM token-only model.

| Assessment block (UTC) | Modality-model pinball | Empirical q90 coverage |
|---|---:|---:|
| Oct 19 12:00–Oct 20 12:00 | 59,931.872 | 84.03% |
| Oct 20 12:00–Oct 21 12:00 | 114,823.929 | 80.56% |
| Oct 21 12:00–Oct 22 12:00 | 134,641.858 | 85.76% |

The modality model beat the best local non-ML baseline on two of three blocks. It missed the preregistered gate because it did not improve at least 5% over the token-only LMM model, and its coverage fell below 85% on the first two blocks. This shows under-calibration for the intended q90 forecast.

## Conclusion

LMM-specific retraining improved the input-token forecast over applying the Coding model unchanged. The added image/request features produced only a small gain over token-only training and did not meet the q90 coverage requirement. The candidate is therefore **NO-GO for use as a research policy signal** under the frozen acceptance rule. No post-hoc calibration or test-driven tuning was applied.

The result applies only to this one-week Azure LMM public sample. It does not establish generalization to another LMM service or time period. The 864 five-minute bins are temporally dependent, so they are not treated as 864 independent samples and no IID confidence interval is reported. The trace has no model latency, GPU usage, VM allocation, deployment action, or user SLO labels, so this experiment cannot support a claim about GPU sizing or scale-out effectiveness.

## Reproduction and artifacts

- Data source and hash: data/external/azure_lmm_2025/SOURCE.md
- Data integrity audit: reports/azure_lmm_demand_data_audit_v1.md
- Frozen protocol: reports/azure_lmm_demand_transfer_protocol.md
- Evaluator: scripts/evaluate_azure_lmm_demand_transfer.py
- Leak-check tests: tests/test_azure_lmm_demand_transfer.py (4 passed under Python 3.10; the scoring run used the frozen Python 3.9.25 environment because pytest is unavailable there)
- Full metrics, freeze, and hash manifest: artifacts/azure_lmm_demand_transfer_v1/
- Saved token-only and modality models are about 58 KB and 81 KB. The raw trace remains compressed; no uncompressed duplicate or request-level result table was written.
