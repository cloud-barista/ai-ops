# Argonne LLM-Inference-Bench source snapshot

- Official repository: https://github.com/argonne-lcf/LLM-Inference-Bench
- Pinned source revision: `4211f5d4e9bc0145ed541b6d372d5d8223b44235` (2025-07-18).
- Source CSV: https://github.com/argonne-lcf/LLM-Inference-Bench/blob/4211f5d4e9bc0145ed541b6d372d5d8223b44235/Plots/All_results.csv
- Paper describing measurements and throughput definition: https://arxiv.org/html/2411.00136v1
- `All_results.csv`: 421,976 bytes; SHA-256 `631a12e08c786ce2d4bdfdc0f67b93234360682cb4b36c93806270ddcfd58412`.
- Repository `LICENSE`: 1,508 bytes; SHA-256 `885d0459f105e9e5362ebf6da918eb8d0866948c559e03e6bfcebcffe4cc5c96` (BSD-3-Clause).

These files are kept separate from the KHU SQLite extraction. The CSV has no per-row run timestamp, failure/OOM label, VM ID, price, or user SLO. Missing shapes are unobserved, not failed configurations. Audit with `python poc/optimization_research/scripts/audit_argonne_vllm.py` from the project root.
