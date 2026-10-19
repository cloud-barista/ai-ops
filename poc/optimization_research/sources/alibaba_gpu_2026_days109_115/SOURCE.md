# Alibaba Cluster Trace GPU v2026 — selected one-week slice

- Official dataset: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2026
- Official schema: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md
- Official download sizes/layout: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/data_download.md
- Selected period: relative days 109–115 inclusive, all 24 hourly partitions. This aligns with the network-table coverage and was chosen as a contiguous multi-day research sample.
- Tables included: pod-hourly, server-hourly, and network-hourly. Every hourly partition in this interval is retained. No rows were filtered.
- Download method: HTTP byte ranges directly from stored Parquet members inside official ZIP objects. The 351.8 GB pod archive and the other full archives were not saved. Each extracted Parquet file was checked against the ZIP central-directory CRC32; SHA-256 and Parquet row counts are recorded in `MANIFEST.json`.
- The `day` index is relative to the trace, not a calendar date. The schema does not include user request SLO or VM scale-action outcomes; this dataset supports workload/resource characterization and trace simulation, not direct causal evaluation of our autoscaling actions.
- Download date: 2026-10-02 UTC. Cite the accompanying OSDI 2026 Alibaba trace paper as listed in the official README.
