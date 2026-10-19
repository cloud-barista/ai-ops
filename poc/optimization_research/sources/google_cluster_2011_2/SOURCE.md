# Google ClusterData2011 v2.1: verified trace files

Source: the [official Google trace page](https://github.com/google/cluster-data/blob/master/ClusterData2011_2.md), [v2.1 format and schema](https://drive.google.com/file/d/0B5g07T_gRDg9Z0lsSTEtTWtpOW8/view?resourcekey=0-cozD56gA4fUDdrkHnLJSrQ), and public bucket `https://storage.googleapis.com/clusterdata-2011-2/`. Accessed 2026-09-28. The official page describes the 29-day Borg trace and its CC-BY license. The files below retain Google's compressed CSV bytes. Their SHA-256 digests match the bucket's `SHA256SUM` manifest; the downloaded manifest itself has SHA-256 `b68993eefffd34d42f2fc6613187048112602d185ffe61992292bacf2c2e9940`.

| Bucket-relative path | Bytes | Official SHA-256 |
| --- | ---: | --- |
| `task_events/part-00050-of-00500.csv.gz` | 5,796,157 | `9e2e370e893a559c87e97b8ce92e6e1d688a1501ae762923650318dcf8ea828e` |
| `task_usage/part-00050-of-00500.csv.gz` | 92,102,114 | `3a9f92fefed2c263a38b3881d9d4570bfd16d27b55ce7feafd35e22d0aa3bf1e` |
| `task_events/part-00200-of-00500.csv.gz` | 3,215,317 | `df8befaa4899af4ee555384c590a5e5da8160ea63500ee599a3c3f0a6bc8cefc` |
| `task_usage/part-00200-of-00500.csv.gz` | 100,327,539 | `e18bfe62a1d738336757ae02d97ca418784f51ea4e75a877a206fed2e8fd1295` |
| `task_events/part-00400-of-00500.csv.gz` | 1,750,348 | `6356f733e54f5b8782d284c592c1474c6df5d84c5442460c7a1a6433db499a7c` |
| `task_usage/part-00400-of-00500.csv.gz` | 81,750,429 | `24ab025b78ebcc4c2eb63366dc913c4acd6858e1992cba4d029667a83dd1923c` |
| `task_events/part-00450-of-00500.csv.gz` | 3,675,439 | `e6184d294745eab2fc9679c1c73cd2385c2ee2ba5cbace663339c61b8ad1d401` |
| `task_usage/part-00450-of-00500.csv.gz` | 79,483,221 | `a41cb509d4825c96dc523dbcaa2d5cee89ba9c8b972a2d060d8c0c8cfdb2e2ef` |

Total compressed bytes: 368,100,564 (351.05 MiB). The original three positions `00050/00200/00400` were selected for extraction feasibility, not favorable labels or scores. The fourth position `00450` was fixed in the joint-envelope protocol (`../../../reports/google_2011_joint_envelope_protocol.md`) before its source files were downloaded on 2026-09-29. Both downloaded bytes matched the pre-existing official `SHA256SUM` entries. No Google data has been joined with the local AI-MCMP SQLite. The original observed-resource study (`../../../reports/google_2011_observed_resource_findings.md`) trained on `00050`, selected against `00200`, and tested on `00400`; the later joint-envelope study (`../../../reports/google_2011_joint_envelope_findings.md`) reused those development shards and held `00450` for its new test. Neither estimates safe AI-App resource shapes. Reproduce the original file integrity and join audit with `python scripts/audit_google_2011_resource_feasibility.py` from the research directory; the joint-envelope runner verifies `00450` independently.

## Later job-event feasibility audit (2026-09-29)

Four small `job_events` files and the official `schema.csv` were subsequently downloaded from the same public bucket. Each SHA-256 matched the **same pre-existing official manifest**. They were selected to join the task-event shards already on disk; they do not form a continuous trace. They were not used for the earlier observed-resource model or its test selection.

| Bucket-relative path | Bytes | Official SHA-256 |
| --- | ---: | --- |
| `schema.csv` | 3,963 | `b3490ab5ad8806ef30eb5bdf432b662bc4e280abdec549bb3c3edb4b1ab6baeb` |
| `job_events/part-00050-of-00500.csv.gz` | 211,654 | `8c813793de20270b9e892e614085a57ede0ec37ff310427493847fe39bde496c` |
| `job_events/part-00200-of-00500.csv.gz` | 174,141 | `87c951f569304bcd988de3d040e279a1c0dfb096fb394b2a49037b7c562d9bfd` |
| `job_events/part-00400-of-00500.csv.gz` | 207,865 | `34c1cbed9f5b0c1891c133b5382e6911b6a181423ce643b5da338d7f8bfed82a` |
| `job_events/part-00450-of-00500.csv.gz` | 239,103 | `845b44b78f05438c9e73054e2d7bbc537eef741537f349cddada4bd29d54162c` |

Reproduce source and cross-table checks with `python scripts/audit_google_2011_job_arrivals.py`. See the job-arrival feasibility report (`../../../reports/google_2011_job_arrival_feasibility.md`) for the exact interpretation and limits.
