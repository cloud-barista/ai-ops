# Microsoft Philly GPU telemetry: bounded source audit

This directory contains a deterministic 5% **machine** sample of the public
Microsoft Research Philly trace. The original tarball and full extracted files
are never stored here. `gpu_util_machine_hash5pct.parquet` keeps one record
for every source minute row on selected machines, in source order. Ordinary
8-GPU rows preserve their values and `NA`s. The collector kept other source
field widths as the complete CSV field array in JSON with null GPU columns.
The separate `gpu_util_normalized_first_width.parquet` restores the observed
2- and 4-slot rows without changing this original sample. Both files retain
source row indices and original timestamp text. No future-risk outcomes or
model scores are created during ingestion or normalization.

## Source and access

- [Official trace description and schema](https://github.com/msr-fiddle/philly-traces/blob/29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d/README.md)
- [Official CC BY 4.0 license](https://github.com/msr-fiddle/philly-traces/blob/29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d/LICENSE)
- The official README asks research users to cite Jeon et al.,
  [*Analysis of Large-Scale Multi-Tenant GPU Clusters for DNN Training Workloads*](https://arxiv.org/abs/1901.05758),
  USENIX ATC '19. This source attribution and the CC BY 4.0 license apply to
  the compact and normalized derivatives as well.
- Pinned archive: `https://media.githubusercontent.com/media/msr-fiddle/philly-traces/29a1b87fa2d9ed80b83c9e3a37f3a88d382b031d/trace-data.tar.gz`
- Pinned compressed length: `1,055,988,361` bytes.
- Pinned HTTP ETag / Git LFS object SHA256: `2037ccf63a725a02be718b0e1aeee6142c90b7f62d0d849295120e9f6ba13d2c`.
- The archive is Git LFS backed; the `media.githubusercontent.com` URL returned
  gzip bytes and HTTP byte ranges in the preflight check.

The collector is `scripts/fetch_philly_gpu_sample.py`. It checks the pinned
HEAD and GET headers, streams gzip/tar in one pass, validates every GPU CSV
row, records the complete compressed-byte SHA256 and tar member inventory,
and only promotes the Parquet output after gzip, tar, byte count, and SHA256
checks complete. It also extracts the small `cluster_machine_list` into
`machine_list.json` as reported metadata. Its GPU counts are not treated as
ground truth for slot existence: the official repository has an
[unresolved report of inconsistencies](https://github.com/msr-fiddle/philly-traces/issues/8)
between this list and job/telemetry GPU counts. If the compact output reaches 1.9 GB or
free disk falls below 5 GB, collection stops and removes the incomplete file.

Selection is fixed independently of utilization:

```text
int.from_bytes(SHA256(machineId UTF-8), "big") % 100 < 5
```

The exact machine ID bytes are those parsed from the source CSV. Every source
row for each selected machine is retained, including full raw CSV field arrays
for non-8-slot rows. `source_row_index`, the original `time_text` with PDT/PST
suffix, and the parsed UTC epoch second are stored. The source audit counts
all GPU rows, machines, `NA` cells, field widths, non-minute timestamps, and
exact duplicate machine/UTC-second keys within the selected sample.

The complete archive SHA256 is
`2037ccf63a725a02be718b0e1aeee6142c90b7f62d0d849295120e9f6ba13d2c`.
The GPU CSV has 44,750,640 rows from 552 machines. The selected 21 machines
contribute 1,704,704 rows. Their measured time span is
2017-10-03 07:00:00 through 2017-12-16 02:42:00 UTC. No selected rows share
an exact machine/UTC-second key, and no source row has a non-minute timestamp.
The initial strict 8-slot parser stopped at a wider row; its diagnostic is
saved as `initial_parser_failure.json`. `gpu_util_audit.json` is the completed,
integrity-checked collection audit.

The CSV uses a terminal empty field after 2- and 4-value rows, which the
collector counted as raw widths 3 and 5. The full-field audit counts are
15,837,600 width-3 rows, 2,923,176 width-5 rows, 25,975,104 width-8 rows,
and 14,760 width-16 rows. The `empty` cell count in the source audit reflects
these terminal delimiters; it is not a count of missing GPU telemetry. Each
selected long-running 2- or 8-slot machine has 60 wider rows from
2017-11-05 01:00 through 01:59 PDT. The reason for this width change is
unknown. These records are never split or imputed.

`scripts/normalize_philly_gpu_sample.py` uses only each machine's first source
row to fix its expected observed slot width (2, 4, or 8). A later row with a
different width remains present with `row_usable=false` and all GPU values
null. The separate `gpu_util_normalized_first_width_audit.json` records
per-machine widths, valid and invalid row counts, source and output hashes,
the normalization script hash, and a source-row-index sequence check. The
original Parquet is preserved. `machine_list.json` is reference metadata;
its listed GPU count is not an authoritative slot-existence label because
the [official repository has an unresolved inconsistency report](https://github.com/msr-fiddle/philly-traces/issues/8).

## Scope and interpretation

Philly is a 2017 DNN **training** cluster trace with per-machine/per-GPU
utilization at one-minute resolution. The published job submission span is
2017-08-07 through 2017-12-22; use the measured span above for telemetry
splits. The trace has no application-level serving SLO or GPU memory usage
series. Any later comparison with Alibaba MaaS must use a shared
GPU-utilization-only endpoint and independently defined cohorts. This trace
cannot establish a VM scale action's causal effect, nor does it justify direct
transfer of trained model parameters between providers and eras.
