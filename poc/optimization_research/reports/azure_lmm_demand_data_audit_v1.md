# Azure LMM source integrity audit (pre-score)

The official gzip source has SHA-256 1393ef21cc7e5e5a3155beea168edbb1b86b26d016464f102b580ff81e474e15 and passes gzip integrity validation.

A bounded streaming scan read only TIMESTAMP, NumImages, and ContextTokens. It found 1,000,000 rows, monotone UTC timestamps from 2024-10-15 12:00:00.269 through 2024-10-22 11:59:59.964, and zero negative image or context-token values. The row stream is exactly seven days from noon to noon, so the original midnight-anchored draft protocol was superseded before demand aggregation or model scoring.

The first attempt stopped at the fixed-span validation because the draft grid ended at 2024-10-22 00:00 UTC. It failed before the support check, model fit, score calculation, or evaluation-start marker. No model scores or five-minute demand sums were generated. The superseded freeze is preserved at artifacts/azure_lmm_demand_transfer_v1/freeze_superseded_before_score.json.

The revised protocol uses the observed half-open source span 2024-10-15 12:00 through 2024-10-22 12:00 UTC. Training, validation, and assessment remain a fixed chronological 3-day / 1-day / 3-day split, aligned to the source start. The protocol also computes UTC time-of-day and weekend features from each actual timestamp, which is necessary for the noon-aligned grid.
