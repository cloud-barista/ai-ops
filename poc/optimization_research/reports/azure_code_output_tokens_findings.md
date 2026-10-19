# Azure Coding output-token p90: frozen seven-day test

## Result

The **predeclared success gate failed**. The rolling input-length-bin method had equal-day mean pinball loss **11.595461** output tokens, slightly above the full-training-data static-bin comparator's **11.594884** (difference +0.000577). It beat rolling global on all three test days and kept at least 90% coverage on each day. The extra rolling adaptation did not improve this strongest matched baseline on this trace, so the experiment does not support a deployment claim for it.

This is a **within-Azure Coding-service retraining and chronological test** of an input-length-conditioned upper-quantile principle. It is not a zero-shot transfer score for the existing BurstGPT model: Azure has no matching model or log-type fields. The experiment followed the [protocol](azure_code_output_tokens_protocol.md), fixed before any held-out output summary or candidate score was computed.

## Provenance and freeze

- Official source: Microsoft Azure [LLM inference trace 2024](https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2024.md), Coding CSV from its [release](https://github.com/Azure/AzurePublicDataset/releases/tag/dataset-llm-2024), CC-BY Attribution. Downloaded file: 691,989,454 bytes, SHA-256 71de5c55cbc35f8f1ed0b6b7806b4cd1e9764b0058469725a6aac98023a1448f, matching the official release digest.
- Frozen protocol SHA-256: 916f1e156559ca140b1397931f52b6078ba61f58e38e94affa7ffb2f8f787570. Evaluator script SHA-256: 38d4d9fd9451602febd0e71da10eea54279925183709395f3a78c84c11140545.
- Train-only [manifest](../artifacts/azure_code_output_tokens_v1/manifest.json) SHA-256: e3d3c0601ee541730a186091f9dbf84a164cac398545165317fc1d64eebc343f. It records the source, protocol, script, input audit, fitted HGB model, and training histograms. An independent review checked these hashes before held-out scoring. The [input audit](../artifacts/azure_code_output_tokens_v1/input_audit.json) did not interpret output labels.
- Before the protocol was written, a first/last 4 KiB byte-range check exposed a few raw rows, including output fields at the tail of the held-out week. No held-out output distribution or candidate score was computed from them. This is a **score-frozen chronological test**, not a strictly blind label holdout; the protocol's stronger wording about not inspecting any held-out output label has this qualification.
- All 16,803,695 rows passed timestamp order and input validity checks; every ContextTokens value was positive. Train days May 10–13 held 7,326,876 requests; test days May 14–16 held 9,476,819. The training-only input quintile boundaries were 704, 1,464, 2,330, and 3,769 tokens. The static HGB used a fixed random sample of 50,000 training requests per day; the static-bin comparator used **all** training rows. The frozen test parsed the three held-out days in order, including every zero output if present, and found no invalid output value.

## Held-out comparison

The main score is the equal-weight average of the three daily pinball losses at quantile 0.9. Coverage is the fraction whose observed output fits the predicted token budget.

| Method | Equal-day pinball (tokens, lower better) | Coverage | Mean predicted budget (tokens) |
| --- | ---: | ---: | ---: |
| Rolling input-length bin (primary) | 11.595461 | 90.5968% | 44.5672 |
| Static input-length bin | **11.594884** | 90.6043% | 44.5485 |
| Rolling global | 11.599856 | 90.6652% | 45.0144 |
| Static HGB, input length only | 11.601417 | 90.6105% | 45.3344 |
| Static global | 11.599000 | 90.6402% | 45.0000 |

| Test date | Requests | Rolling-bin pinball | Static-bin pinball | Rolling-global pinball | Rolling-bin coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| May 14 (day 4) | 3,210,786 | 11.635285 | **11.631760** | 11.641651 | 90.7394% |
| May 15 (day 5) | 3,191,468 | **11.532309** | **11.532309** | 11.537281 | 90.6329% |
| May 16 (day 6) | 3,074,565 | **11.618790** | 11.620584 | 11.620637 | 90.4104% |

The day-5 rolling and static-bin predictions are exactly equal by design: under the two-day feedback delay, the day-5 rolling history contains precisely training days 0–3. On day 4 the rolling method was worse than static-bin by 0.003525 pinball tokens; on day 6 it was better by 0.001794. This leaves the three-day average slightly worse. The primary passed the rolling-global daily-win gate (3/3) and the coverage gate, but failed the strict loss gate against static-bin. Full daily and input-bin diagnostics are in [heldout_scores.json](../artifacts/azure_code_output_tokens_v1/heldout_scores.json).

An independent read-only audit streamed all 16,803,695 source rows, reproduced the training-day input-bin edges and output histograms, and recomputed predictions and scores for all 9,476,819 held-out rows, including the saved HGB model. The reported values matched within floating-point precision. Three days are too few to claim that the tiny static-bin difference is stable across periods. No post-test tuning was applied.

## Scope

The dataset records invocation time and token counts. Input length can inform a request-time decision only if tokenization has completed before that decision; the trace does not record that sequence. The [accompanying paper](https://arxiv.org/html/2408.00741) calls the released data a subset of production invocations, so completeness of all submitted or failed requests is unknown. There are no model IDs, prompt semantics, latency/SLO outcomes, GPU/VM measurements, or deployment/scale actions. These results establish neither VM sizing nor autoscaling performance.
