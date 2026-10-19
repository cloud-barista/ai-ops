# Token-aware llm-d autoscaling: pinned small source files

Source: [`asm582/token-aware-autoscaling`](https://github.com/asm582/token-aware-autoscaling/tree/7e70bf5f69c773eb6f51f870c5d23cf41f0efb04), commit `7e70bf5f69c773eb6f51f870c5d23cf41f0efb04`. The repository's [Apache 2.0 license](https://github.com/asm582/token-aware-autoscaling/blob/7e70bf5f69c773eb6f51f870c5d23cf41f0efb04/LICENSE) is preserved as `LICENSE`. Attribute the original author and mark changes when reusing the material.

`SOURCE.json` pins each downloaded file's source URL, byte count, Git blob SHA-1, and SHA-256. It also inventories all 42 blobs in the three `experiments/2026-08-20-*` directories. This copy preserves the 33 small text/CSV/JSON/YAML experiment files, the license, and the 25 KB benchmark collection script: **35 files, 260,916 bytes**. The nine PNG charts were omitted because the underlying stage summaries and timelines are present. No larger benchmark output or per-request trace is present in the pinned experiment tree. The raw downloaded files were not edited.

The stored `benchmark/run-benchmark.sh` (`benchmark/run-benchmark.sh`) lines 371–408 shows the standard timeline sampler. It polls Kubernetes Deployments for `.spec.replicas` and `.status.readyReplicas` through separate sequential calls, then timestamps the row. Individual field read times are unavailable. Thus a CSV transition is the **first observation of a changed target or Ready count**, not a logged KEDA/HPA command timestamp; differences between row timestamps are not exact transition-time bounds. The prefill-heavy report says its original wrapper stopped and the committed timeline came from a replacement sampler after an early gap; its `epoch` schema differs from the other two runs.

The three `workload-used.yaml` files enable per-request reporting, but only stage and run summary lifecycle JSON files were published in the pinned experiment directories. Those JSON files have counts and latency quantiles without per-request IDs or timestamps.

Recheck local byte integrity and counts with `python3 poc/optimization_research/scripts/audit_token_aware_autoscaling.py` from `/home/researcher/ai-mcmp`.
