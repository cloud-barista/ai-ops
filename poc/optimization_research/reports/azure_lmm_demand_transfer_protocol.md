# Azure LMM next-five-minute input-demand transfer experiment

Status: frozen before five-minute demand aggregation and model scoring. The official compressed source was downloaded and checked by SHA-256 and gzip integrity. A pre-score integrity scan found the actual request timestamp coverage and verified nonnegative image/token counts. No five-minute demand aggregates or model scores were inspected before the revised freeze.

## Question and scope

Can the existing Azure Coding next-five-minute input-token q90 model transfer to the Azure LMM request trace, and do historical image/request-count signals improve a model trained on LMM training bins?

This is a forecasting experiment on the public sampled trace. It does not predict GPU count, VM size, user latency/SLO, or the effect of a scale action. The LMM prompt text is unavailable. ContextTokens includes text and image context tokens.

## Source and fixed time split

Source: the official Azure Public Dataset file AzureLMMInferenceTrace_multimodal.csv.gz. The official collection window is described as October 15–22, 2024. The integrity scan found 1,000,000 sorted rows from 2024-10-15 12:00:00.269 UTC through 2024-10-22 11:59:59.964 UTC, exactly seven days. No negative NumImages or ContextTokens values were found. The actual half-open grid is therefore fixed at 2024-10-15 12:00 UTC through 2024-10-22 12:00 UTC, with 2,016 UTC five-minute bins. The source SHA-256 is 1393ef21cc7e5e5a3155beea168edbb1b86b26d016464f102b580ff81e474e15.

Only TIMESTAMP, NumImages, and ContextTokens are read. GeneratedTokens and prompt content are excluded. Requests are aggregated into the fixed seven-day five-minute grid. A bin with no sampled request is represented as zero. Timestamps must be sorted and inside the fixed span; image and token counts must be finite nonnegative integers.

At the start of bin t, predict the sampled ContextTokens total in bin t. Only bins with a mean of at least 1,000 ContextTokens across the previous 12 completed bins are eligible. The first 12 bins are burn-in. Bins below the threshold are omitted, and no more than 5% of any validation or assessment block may be ineligible.

| Purpose | UTC interval | Bins |
|---|---|---:|
| Fit LMM-specific models and training-only constants | 2024-10-15 12:00 through 2024-10-18 12:00 | 852 after burn-in |
| Validation, reported without retuning | 2024-10-18 12:00 through 2024-10-19 12:00 | 288 |
| Chronological assessment | 2024-10-19 12:00 through 2024-10-22 12:00 | 864, three consecutive 24-hour blocks |

The assessment is a holdout within one published week, not a blind or independent trace release. All methods use the same assessment blocks, and those blocks are never used to select features or parameters. Daily blocks begin at 12:00 UTC because that is the source's observed start time.

## Models and comparisons

Primary target: the next five-minute ContextTokens sum. Primary metric: q=0.9 absolute-token pinball loss, averaged within each assessment block and then equally across the three blocks. Also report block-level empirical coverage, underprediction fraction, average predicted and observed tokens, and eligible-bin counts.

Methods fixed before scoring:

1. Frozen Coding transfer: load the saved Coding relative-q90 HGB model and apply it unchanged with its original token-only feature schema. The UTC clock features are computed from the LMM timestamps so that time-of-day values have the same meaning as in Coding.
2. LMM token-only HGB: fit the same HistGradientBoostingRegressor settings as the frozen Coding experiment on LMM training bins, using the existing token-only features.
3. LMM modality HGB: fit the same HGB settings and target as method 2, adding only past image-count and request-count features. At t, all modality features use bins no later than t-1: lags 1, 3, and 12; prior-hour q90; prior-hour image count per request; and recent-half versus previous-half summaries. UTC time-of-day and weekend indicators are computed from each bin's timestamp. No current/future-bin feature is allowed.
4. LMM weighted global relative q90: training-only scale-weighted q90 of ContextTokens divided by the prior-hour mean, multiplied by the current prior-hour mean.
5. LMM prior-hour empirical q90: empirical q90 of the preceding 12 completed ContextTokens totals.
6. LMM previous-day ratio: the previous-day same-slot relative demand multiplied by the current prior-hour mean, falling back to the training-only global ratio q90 when absent.
7. LMM previous-day plus training residual q90: method 6 plus a training-only, scale-weighted q90 residual.

For the two learned LMM models, use the frozen Coding settings: quantile HistGradientBoostingRegressor with quantile 0.9, max_iter 150, learning_rate 0.05, max_leaf_nodes 7, min_samples_leaf 20, l2_regularization 1, early_stopping false, random_state 42. Fit the relative target y_t / m_t with sample weight m_t / mean_train(m), where m_t is the preceding-hour mean. No hyperparameter search, post-hoc calibration, feature selection, or test-driven changes are allowed.

All forecasts use only information available by the start of the target bin. Prior-bin public trace values are treated as available immediately, matching the earlier Coding protocol; actual telemetry delivery delay is not in this dataset.

## Pre-score support and decision gate

Before model scoring, require at least 95% eligible bins in the LMM training interval, at least 95% in the validation block, and at least 95% in each assessment block. If this fails, record a support NO-GO and do not score the models.

The modality model is an exploratory GO only if all conditions pass:

- its three-block equal-weight mean pinball loss is at least 5% below the frozen Coding transfer, the LMM token-only model, and each local non-ML baseline;
- it beats the best local non-ML baseline on at least two of the three assessment blocks;
- its empirical coverage is 85% to 95% on each assessment block;
- each assessment block has at least 95% eligible bins.

Regardless of the gate, report all fixed method scores if support passes. A failed gate is a NO-GO for using this candidate as a research policy signal. The assessment covers one sampled service-week and does not establish production generalization.

## Reproducibility and storage

The freeze record stores the protocol, data-audit report, script, test, source, Coding model, dependency versions, and imported Coding evaluator hashes. The scoring command runs once and records a start marker before fitting. Save only small fitted models and summary metrics; do not save request-level rows or five-minute input tables. Read the gzip CSV directly and do not write an uncompressed duplicate.
