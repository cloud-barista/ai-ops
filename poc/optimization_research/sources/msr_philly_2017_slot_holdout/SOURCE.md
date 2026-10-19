# Microsoft Philly GPU trace: withheld `[35,45)` machine sample

The official source is the [Microsoft Research Philly trace](https://github.com/msr-fiddle/philly-traces/blob/29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d/README.md), pinned at commit `29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d`. The original `trace-data.tar.gz` is 1,055,988,361 bytes, SHA-256 `2037ccf63a725a02be718b0e1aeee6142c90b7f62d0d849295120e9f6ba13d2c`. The archive is streamed and audited without retaining the tarball.

The sample contains every original minute GPU row for raw machine IDs whose integer SHA-256 residue modulo 100 lies in `[35,45)`. The raw and normalized Parquet files, collection audit, machine list, and source-row-order audit are written in this directory by `poc/optimization_research/scripts/evaluate_philly_slot_day_holdout.py`. The exact method and acceptance criteria were frozen before collecting these IDs in `poc/optimization_research/artifacts/philly_slot_day_holdout_v1/freeze.json`.

The source carries [CC BY 4.0](https://github.com/msr-fiddle/philly-traces/blob/29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d/LICENSE). Cite Jeon et al., *Analysis of Large-Scale Multi-Tenant GPU Clusters for DNN Training Workloads*, USENIX ATC 2019, as requested by the maintainers. GPU utilization is a cluster risk signal; it is not a user-facing AI-App SLO or measured VM scaling outcome.
