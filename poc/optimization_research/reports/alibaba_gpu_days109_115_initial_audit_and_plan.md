# Alibaba GPU trace (days 109–115): initial audit and research plan

Status: **data slice downloaded and audited; model training has not started.** This is a feasibility note, not a reported model result.

## Selected data

The source is Alibaba's official Cluster Trace GPU v2026. The selected range is relative days 109–115 inclusive, all 24 hours per day. The network table covers this same week. Relative day numbers are not calendar dates.

| Table | Hourly files | Rows | Stored Parquet bytes |
|---|---:|---:|---:|
| Pod-hourly | 168 | 261,779,108 | 14,175,406,137 |
| Server-hourly | 168 | 5,435,808 | 122,557,072 |
| Network-hourly | 168 | 5,390,218 | 203,870,478 |
| **Total** | **504** | **272,605,134** | **14,501,833,687** |

Files are under `poc/optimization_research/data/external/alibaba_gpu_2026_days109_115/`. The full 351.8 GB pod archive was not saved. `MANIFEST.json` records the upstream archive ETags, each selected partition, its CRC32/SHA-256, Parquet schema, and row count. The selected partition transfer completed without CRC errors.

## What the data can support

The pod-hourly table includes GPU/CPU requests, public GPU model, priority, job/model categories, readiness status, scheduling delay, readiness delay, and hourly utilization fields. Server-hourly provides GPU/CPU capacity and anonymized rack-domain IDs. Network-hourly provides per-server hourly traffic. This makes the slice useful for cluster workload characterization and an offline placement/readiness study.

It does not contain an AI-App request SLO, an AI-MCMP decision/action ID, VM creation outcome, traffic-shift event, or user-facing latency. It cannot establish that a deployment or scale action improves an application SLO.

## Initial label and quality findings

- The pod table has 261,779,108 pod-hour rows. `ready_status`, `ready_delay_sec`, and `schedule_delay_sec` are present on 223,001,394 rows (85.2%); this is a row count, not a count of independent pods.
- `ready_status` is true on 204,683,269 rows and false on 18,318,125 rows. These counts repeat pods observed over multiple hours.
- All 57,927,357 negative `ready_delay_sec` values equal `-1`. This includes every false-ready row and 39,609,232 true-ready rows. Treat `-1` as invalid for continuous delay prediction; keep the boolean `ready_status` as a separate observed label.
- For ready-delay regression, the row-level candidate target is `ready_status == true` and `ready_delay_sec >= 0`. This yields 165,074,037 repeated rows before pod-level deduplication. Do not report this as 165 million independent examples.
- In the day 109 ID audit, 9,409,168 distinct pod IDs appeared. 3,764,638 appeared in more than one hour; the 90th percentile was 12 observed hours per pod. `ready_status` was consistent across repeated rows in that day, but 147,368 pod IDs had conflicting non-null `ready_delay_sec` values and 169,518 had conflicting `schedule_delay_sec` values. Delay labels therefore need an explicit conflict rule or exclusion after deduplication.
- `job_type_public` and `model_type_public` are `unknown` on about 92.4% of pod-hour rows; `state_public` is `Unknown` on 99.7%. Keep unknown as an explicit category, report known-category results separately, and do not use `state_public` as a useful feature without further validation.
- Pod IDs and workload IDs repeat. Never split raw rows randomly or use either identifier as a predictive feature.

## Recommended first task

**Estimate pod readiness risk from resource-request context, with conditional ready-time prediction as a secondary task.** This is the closest supervised task in this slice to deployment-readiness judgment, because the trace has observed readiness labels and resource requests. It is a cluster-pod outcome, not a direct prediction of AI-MCMP VM deployment success.

### Targets and inputs

1. Primary target: `ready_status` (binary). Use one event-level record per `pod_id`; assign the pod to the split where it first appears in the selected range. The day 109 audit suggests this target is stable across repeated hours, but verify across the full selected week before training.
2. Secondary target: nonnegative `ready_delay_sec`, only where `ready_status == true`. Deduplicate by pod. Exclude pods whose repeated non-null delay values conflict, unless a protocol fixed before scoring justifies a deterministic aggregation rule.
3. Candidate pre-outcome inputs: GPU model, requested GPU count, requested GPU memory, requested CPU cores, priority class, public workload/model category, and GenAI indicator. Keep missing/unknown categories explicit.
4. Exclude IDs, actual selected server/rack, `state_public`, `ready_status` and delay fields from features, and all post-placement utilization/usage fields (`used_gpu_hours`, `avg_*_util`, and `avg_gpu_mem_gib`). Hourly observation time is not a job submit timestamp, so do not present it as request-time load.

### Evaluation plan

- First finalize an event-level deduplication audit over all seven days, including ready-label consistency, delay conflicts, unique pods/workloads, and the number of eligible labels by day/category.
- Use a chronological split after assigning each pod to its first-seen day: days 109–112 train, day 113 validation, days 114–115 test. Also report a stricter workload-grouped holdout for non-null `workload_id`; hold out null-ID pods by `pod_id`. Do not tune on the final two days.
- Compare a prevalence/constant baseline and a regularized logistic model before a tree model. For readiness risk, report average precision, Brier score, calibration, and recall at a fixed top-risk fraction; accuracy alone is misleading because ready outcomes dominate.
- For conditional ready delay, compare a GPU-model/priority median baseline with a quantile model. Report MAE and q90 pinball/coverage, excluding invalid `-1` and conflicting labels under a predeclared rule.
- Report results by each held-out day and by known versus unknown workload categories. Treat rows sharing a pod/workload as related observations, not independent samples.

### Advancement gate

Proceed to training only if full-week pod-level deduplication confirms enough nonconflicting, eligible pods in train, validation, and both test days, and readiness labels remain consistent across repeated observations. Keep the result exploratory unless the model beats the fixed baselines on both test days without poor calibration. Even a successful result supports only readiness-risk estimation on this Alibaba cluster trace; a separate AI-MCMP deployment/action dataset is required to claim service-level or scale-out effectiveness.

## Source and reproduction files

- Dataset and integrity manifest: `poc/optimization_research/data/external/alibaba_gpu_2026_days109_115/MANIFEST.json`
- Source notes: `poc/optimization_research/data/external/alibaba_gpu_2026_days109_115/SOURCE.md`
- Official dataset: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2026
- Official schema: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md
