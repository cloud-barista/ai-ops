# Alibaba Spot-GPU trace (2026)

Files downloaded from the official Alibaba Clusterdata repository.

- Dataset page: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-v2026-spot-gpu
- `node_info_df.csv`: node GPU model, GPU capacity, and CPU vCPU metadata.
- `job_info_df.csv`: job GPU/CPU requests, workers, submit time, duration, and HP/Spot category.
- Direct files: `node_info_df.csv`, `job_info_df.csv` in the same directory.
- Downloaded 2026-10-02 UTC. See the upstream README for research citation and terms.
- HP/Spot class is a workload category; it is not an observed user-SLO outcome label.
