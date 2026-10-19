# Azure LLM inference trace 2024: Coding

- Official dataset description: https://github.com/Azure/AzurePublicDataset/blob/master/AzureLLMInferenceDataset2024.md
- Official release: https://github.com/Azure/AzurePublicDataset/releases/tag/dataset-llm-2024
- Asset: https://github.com/Azure/AzurePublicDataset/releases/download/dataset-llm-2024/AzureLLMInferenceTrace_code_1week.csv
- Official release metadata: 691,989,454 bytes, SHA-256 71de5c55cbc35f8f1ed0b6b7806b4cd1e9764b0058469725a6aac98023a1448f
- Data-specific license: CC-BY Attribution. Cite Stojkovic et al., DynamoLLM: Designing LLM Inference Clusters for Performance and Energy Efficiency, HPCA 2025, as requested by the data page.
- Scope: Coding service; timestamp, context token count, generated token count only. No model IDs, latency/SLO measurements, GPU/VM telemetry, or deployment actions.
- Use: the frozen Coding output-token experiment (`../../../reports/azure_code_output_tokens_findings.md`). A small first/last byte-range check exposed raw output fields before the protocol was written; the complete held-out output distribution and candidate scores were first computed after the training manifest was reviewed.
