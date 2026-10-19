#!/usr/bin/env bash
set -Eeuo pipefail

# Runs inside one CB-Tumblebug-provisioned Ubuntu node. The caller transfers
# this repository's src/ and scripts/ directories before invoking the script.

device_scope="${KHU_DEVICE_SCOPE:-cpu}"
profile_set="${KHU_PROFILE_SET:-baseline}"
max_cases="${KHU_MAX_CASES:-1}"
sample_interval="${KHU_SAMPLE_INTERVAL_SECONDS:-0.10}"
seed="${KHU_SEED:-42}"
run_id="${KHU_RUN_ID:-tumblebug-$(date -u +%Y%m%dT%H%M%SZ)}"
cpu_core_counts="${KHU_CPU_CORE_COUNTS:-}"
cpu_core_samples="${KHU_CPU_CORE_SAMPLES_PER_PROFILE:-}"
remote_grid_shard="${KHU_REMOTE_GRID_SHARD:-0}"
torch_index_url="${KHU_TORCH_INDEX_URL:-}"
work_root="${KHU_WORK_ROOT:-/tmp/khu-collector}"
output_root="${KHU_OUTPUT_ROOT:-/tmp/khu-collection-output}"
cache_dir="${KHU_HF_CACHE_DIR:-$work_root/huggingface-cache}"
result_dir="$output_root/$run_id"

case "$device_scope" in cpu|gpu|both) ;; *) echo "invalid KHU_DEVICE_SCOPE" >&2; exit 2 ;; esac
[[ "$profile_set" =~ ^[a-z0-9-]+$ ]] || { echo "invalid KHU_PROFILE_SET" >&2; exit 2; }
[[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_RUN_ID" >&2; exit 2; }
[[ "$max_cases" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KHU_MAX_CASES" >&2; exit 2; }
[[ "$remote_grid_shard" =~ ^[0-9]+$ ]] || { echo "invalid KHU_REMOTE_GRID_SHARD" >&2; exit 2; }

mkdir -p "$result_dir" "$cache_dir"

if command -v sudo >/dev/null 2>&1; then
  sudo -n apt-get update
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip ca-certificates
else
  apt-get update
  env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip ca-certificates
fi

if [[ "$device_scope" != cpu ]]; then
  command -v nvidia-smi >/dev/null 2>&1 || {
    echo "GPU collection requires an NVIDIA image/driver with nvidia-smi" >&2
    exit 3
  }
fi

python3 -m venv "$work_root/.venv"
source "$work_root/.venv/bin/activate"
python -m pip install --upgrade pip

if [[ -z "$torch_index_url" ]]; then
  if [[ "$device_scope" == cpu ]]; then
    torch_index_url="https://download.pytorch.org/whl/cpu"
  else
    torch_index_url="https://download.pytorch.org/whl/cu128"
  fi
fi
python -m pip install --index-url "$torch_index_url" "torch==2.7.1"
python -m pip install \
  "transformers==4.46.2" \
  "huggingface-hub==0.36.2" \
  "sentencepiece==0.2.0"

python - "$device_scope" <<'PY'
import json
import sys

import torch

requested_device = sys.argv[1]
diagnostic = {
    "event": "torch_runtime_ready",
    "torch_version": torch.__version__,
    "torch_cuda_version": torch.version.cuda,
    "cuda_available": torch.cuda.is_available(),
    "cuda_device_count": torch.cuda.device_count(),
    "cuda_devices": [
        torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())
    ],
}
print(json.dumps(diagnostic, ensure_ascii=False), flush=True)
if requested_device != "cpu" and not diagnostic["cuda_available"]:
    raise RuntimeError("PyTorch cannot use CUDA on this VM")
PY

prefetch_args=(
  --cache-dir "$cache_dir"
  --device "$device_scope"
  --profile-set "$profile_set"
  --remote-grid-shard "$remote_grid_shard"
  --max-cases "$max_cases"
  --cpu-core-counts "$cpu_core_counts"
)
collect_args=(
  --output-db "$result_dir/executions.sqlite3"
  --cache-dir "$cache_dir"
  --csv-dir "$result_dir/raw-csv"
  --run-id "$run_id"
  --device "$device_scope"
  --profile-set "$profile_set"
  --remote-grid-shard "$remote_grid_shard"
  --max-cases "$max_cases"
  --sample-interval-seconds "$sample_interval"
  --seed "$seed"
  --cpu-core-counts "$cpu_core_counts"
)
if [[ -n "$cpu_core_samples" ]]; then
  prefetch_args+=(--cpu-core-samples-per-profile "$cpu_core_samples")
  collect_args+=(--cpu-core-samples-per-profile "$cpu_core_samples")
fi
if [[ "${KHU_APPLY_CPU_CORE_COUNTS_TO_GPU:-0}" == 1 ]]; then
  prefetch_args+=(--apply-cpu-core-counts-to-gpu)
  collect_args+=(--apply-cpu-core-counts-to-gpu)
fi

export PYTHONPATH="$work_root/src"
export TOKENIZERS_PARALLELISM=false
python "$work_root/scripts/prefetch_khu_models.py" "${prefetch_args[@]}" \
  | tee "$result_dir/prefetch-plan.json"
python -m khu_policy_agent collect-live "${collect_args[@]}" \
  | tee "$result_dir/collection-summary.json"
collection_status="complete"
if ! python - "$result_dir/executions.sqlite3" "$run_id" <<'PY'
import json
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
row = connection.execute(
    "SELECT status, execution_count, success_count FROM collection_runs WHERE run_id = ?",
    (sys.argv[2],),
).fetchone()
if row is None:
    raise RuntimeError("collection run record is missing")
status, execution_count, success_count = row
if status != "complete" or execution_count <= 0 or success_count != execution_count:
    failures = connection.execute(
        """
        SELECT execution_id, profile_name, model_id, device, error_json
          FROM execution_measurements
         WHERE run_id = ? AND success = 0
         ORDER BY ordinal, execution_id
        """,
        (sys.argv[2],),
    ).fetchall()
    for execution_id, profile_name, model_id, device, error_json in failures:
        try:
            error = json.loads(error_json)
        except (TypeError, json.JSONDecodeError):
            error = {"raw": error_json}
        print(
            json.dumps(
                {
                    "event": "stored_collection_failure",
                    "execution_id": execution_id,
                    "profile_name": profile_name,
                    "model_id": model_id,
                    "device": device,
                    "error": error,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
    raise RuntimeError(
        f"collection failed: status={status} executions={execution_count} successes={success_count}"
    )
PY
then
  collection_status="failed"
fi

if [[ "$collection_status" == complete ]]; then
  python -m khu_policy_agent prepare-observed-lifecycle-dataset \
    --source-db "$result_dir/executions.sqlite3" \
    --output-db "$result_dir/observed-lifecycle.sqlite3" \
    --csv-dir "$result_dir/lifecycle-csv" \
    --run-id "$run_id-lifecycle" \
    --collection-run-id "$run_id" \
    --seed "$seed" \
    | tee "$result_dir/lifecycle-summary.json"
  python - "$result_dir/observed-lifecycle.sqlite3" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
scenario_count = connection.execute("SELECT COUNT(*) FROM deployment_scenarios").fetchone()[0]
outcome_count = connection.execute("SELECT COUNT(*) FROM action_outcomes").fetchone()[0]
if scenario_count <= 0 or outcome_count <= 0:
    raise RuntimeError(
        f"lifecycle ETL produced no training rows: scenarios={scenario_count} outcomes={outcome_count}"
    )
PY
else
  echo "collection failed; packaging raw database and logs for diagnosis" >&2
fi

python - "$result_dir/provenance.json" "$collection_status" <<'PY'
import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone

target = sys.argv[1]
keys = (
    "KHU_DEVICE_SCOPE", "KHU_PROFILE_SET", "KHU_MAX_CASES",
    "KHU_SAMPLE_INTERVAL_SECONDS", "KHU_SEED", "KHU_RUN_ID",
    "KHU_CPU_CORE_COUNTS", "KHU_CPU_CORE_SAMPLES_PER_PROFILE",
    "KHU_REMOTE_GRID_SHARD", "KHU_TB_NAMESPACE", "KHU_TB_INFRA_ID",
    "KHU_TB_NODE_ID", "KHU_TB_SPEC_ID", "KHU_TB_IMAGE_ID",
)
payload = {
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "collection_status": sys.argv[2],
    "host": socket.gethostname(),
    "platform": platform.platform(),
    "python": platform.python_version(),
    "configuration": {key: os.environ.get(key, "") for key in keys},
}
with open(target, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\n")
PY

archive="$output_root/$run_id.tar.gz"
tar -czf "$archive" -C "$output_root" "$run_id"
rm -f "$output_root/$run_id.tar.gz.part-"*
split -b 180M -d -a 4 "$archive" "$output_root/$run_id.tar.gz.part-"
rm -f "$archive"

python - "$output_root" "$run_id" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
run_id = sys.argv[2]
parts = []
for path in sorted(root.glob(f"{run_id}.tar.gz.part-*")):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parts.append({"name": path.name, "sourcePath": str(path), "sha256": digest, "size": path.stat().st_size})
if not parts:
    raise RuntimeError("collection archive has no parts")
manifest = {"run_id": run_id, "parts": parts}
(root / f"{run_id}.parts.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(json.dumps(manifest, ensure_ascii=False))
PY

if [[ "$collection_status" != complete ]]; then
  echo "diagnostic archive is ready for download" >&2
  exit 9
fi
