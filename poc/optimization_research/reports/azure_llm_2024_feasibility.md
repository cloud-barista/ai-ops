# Azure LLM inference trace 2024: external validation feasibility

## Decision

The official Azure 2024 trace is a feasible **independent-service validation set for request-time output-token prediction**. It has request timestamps, input-token counts, and observed output-token counts for two separate services over seven consecutive days each. It does **not** contain model identity, prompt text, latency, SLO outcomes, GPU/VM measurements, or deployment/scale actions. It can test whether a BurstGPT-derived prediction rule transfers to a different service, but it cannot establish deployment-readiness or autoscaling benefit.

## Verified provenance and contents

| File | Service | Bytes | Official SHA-256 | Observed first–last timestamp (UTC) |
| --- | --- | ---: | --- | --- |
| `AzureLLMInferenceTrace_code_1week.csv` | Coding | 691,989,454 | `71de5c55cbc35f8f1ed0b6b7806b4cd1e9764b0058469725a6aac98023a1448f` | 2024-05-10 00:00:00.009930 – 2024-05-16 23:59:59.929501 |
| `AzureLLMInferenceTrace_conv_1week.csv` | Conversation | 1,135,195,393 | `a0cc9b969a9bbf0fd811802cbf4323edd3a209ace791e3799ad4f9207f213941` | 2024-05-12 00:00:00.001163 – 2024-05-18 23:59:59.995460 |

The byte sizes and digests are from the [official GitHub release metadata](https://github.com/Azure/AzurePublicDataset/releases/tag/dataset-llm-2024). The first and last rows were independently inspected using HTTP byte ranges of 4 KiB each; the complete files had **not** been downloaded or audited at the time of this feasibility check. These small ranges included raw output fields, including at the tail of the later Coding test period, although no output summaries or model scores were computed from them. The CSV header in both files is exactly `TIMESTAMP,ContextTokens,GeneratedTokens`. The [dataset description](https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2024.md) defines these as invocation time, context-token count, and generated-token count. The [accompanying paper](https://arxiv.org/html/2408.00741) describes the two services as Coding and Conversation and confirms that the shared traces are a subset of production invocations.

The dataset-specific description licenses the **data under CC-BY Attribution** and asks users to cite Stojkovic et al., *DynamoLLM: Designing LLM Inference Clusters for Performance and Energy Efficiency*, HPCA 2025. Its description says prompt content is unavailable for privacy reasons. This means application semantics cannot be extracted from the trace.

## Appropriate experiment

1. Freeze a BurstGPT-trained output-token predictor and its evaluation rule before opening Azure outcomes. Use `ContextTokens` as the request-time input and `GeneratedTokens` only as the target. BurstGPT `Model` and `Log Type` features have no Azure counterpart, so the portable predictor must use a predeclared fallback or a prompt-length-only variant.
2. Score Coding and Conversation separately as two external services, with pinball loss and coverage on the full week. Treat the files as independent test data, not as a source of tuning if claiming zero-shot transfer.
3. For a separately named *within-Azure adaptation* experiment, use days 1–4 for training, day 5 for validation, and days 6–7 for test, per service. Preserve day order and define any label-feedback delay before evaluation. Seven days offer only short temporal validation; report daily results and uncertainty.

The source lacks latency and hardware outcomes, so token-prediction scores must not be presented as measured SLO, energy, VM-sizing, or scale-policy improvements. The full transfer experiment requires roughly 1.83 GB of CSV downloads before processing. No data file was downloaded during this feasibility check; only small byte ranges and release metadata were read.

After this feasibility check, the Coding CSV was downloaded and evaluated in a separate [within-Azure chronological experiment](azure_code_output_tokens_findings.md). Its rolling input-length method did not beat the matched static-bin baseline under the frozen success criterion.
