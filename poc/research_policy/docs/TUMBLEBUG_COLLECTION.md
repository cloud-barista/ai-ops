# CB-Tumblebug VM collection

This workflow provisions one CB-Tumblebug Infra node, runs real Hugging Face
inference on the node, samples its CPU, memory, and NVIDIA GPU telemetry, and
downloads the resulting SQLite databases. It lives outside GEON because VM
lifecycle and experiment execution are infrastructure responsibilities.

## Safety and lineage

- `review` calls `/infraDynamicReview` and does not create a VM.
- `run` saves review, create, status, transfer, command, and delete responses.
- `run` waits until the VM accepts a Tumblebug SSH command before transferring
  files; CSP `Running` status alone does not mean cloud-init has finished.
- File transfer and archive extraction are separate retryable setup operations,
  because GPU images can transiently close a second back-to-back SSH session.
- The remote result stores namespace, Infra ID, node ID, spec ID, and image ID.
- Downloads are split below the CB-Tumblebug 200 MB limit and checked with
  SHA-256 before extraction.
- The Infra is deleted with `option=terminate` only after verified download.
- A failed run also terminates the Infra by default to prevent unattended cost;
  set `KHU_TB_DESTROY_ON_FAILURE=0` only for deliberate failure inspection.
- Shared namespace resources are not deleted automatically.
- Credentials are read from environment variables and are never stored in the
  request or result directory.

## Requirements

Run the orchestration script on the Linux host that can reach CB-Tumblebug.
It requires `bash`, `curl`, `jq`, `tar`, and `sha256sum`. CB-Tumblebug must
already be initialized with a cloud credential, common spec, and Ubuntu image.

The new VM must have outbound HTTPS access. GPU collection additionally needs
an NVIDIA-capable spec and image with a working driver and `nvidia-smi`.

## Run

Copy the example without putting a real password in Git:

```bash
cd ~/geon-khudata
cp tumblebug-collection.env.example tumblebug-collection.env
chmod 600 tumblebug-collection.env
vi tumblebug-collection.env
source tumblebug-collection.env
```

The script prompts for the CB-Tumblebug password without echoing it. For an
unattended experiment, set `TUMBLEBUG_PASSWORD` only in the process environment
or a secret manager.

Perform the non-provisioning preflight first:

```bash
bash scripts/collect-via-tumblebug.sh review
```

Create the VM, collect one GPU condition, download the data, and terminate the
VM after verification:

```bash
bash scripts/collect-via-tumblebug.sh run
```

Results are written under `runs/tumblebug/<run-id>/`. The extracted archive
contains `executions.sqlite3`, `observed-lifecycle.sqlite3`, CSV exports,
collection summaries, the exact prefetch plan, and provenance.

For an existing Infra ID, status and cleanup are explicit:

```bash
export KHU_TB_INFRA_ID=khu-collect-20260918
bash scripts/collect-via-tumblebug.sh status
bash scripts/collect-via-tumblebug.sh destroy
```

Start with one GPU case. After that succeeds, increase `KHU_MAX_CASES` using
the predetermined workload matrix:

```bash
export KHU_DEVICE_SCOPE=both
export KHU_PROFILE_SET=diverse
export KHU_MAX_CASES=12
bash scripts/collect-via-tumblebug.sh review
bash scripts/collect-via-tumblebug.sh run
```

`SCALE_OUT` and `SCALE_IN` rows produced by the current ETL compare separately
measured static replica counts. They are not evidence of a live scaling
transition. Real transition data requires a later AppDeployer/Tumblebug loop
that records metrics before, during, and after an actual node-group change.

## Bounded 1.2 GiB campaign

For a resumable CPU/GPU campaign, use `scripts/tumblebug_campaign.py` with
`config/tumblebug-campaign-1g.json`. The controller counts only verified durable
evidence, requires 700 MiB of GPU data and 400 MiB of CPU data, requires three
successful runs for every listed VM tier, and stops at a 1.5 GiB hard ceiling.

`plan`, `review`, and `verify` do not create a VM. `run` requires both the
explicit `--execute-live-cloud` flag and a positive `--max-cost-usd` value.
Use `--max-live-entries` to bound a paid pilot to a fixed number of VMs.
See [`docs/tumblebug-1g-campaign.md`](tumblebug-1g-campaign.md) for the
complete operator procedure and data-lineage boundary.
