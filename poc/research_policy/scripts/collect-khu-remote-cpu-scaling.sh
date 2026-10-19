#!/usr/bin/env bash
set -euo pipefail

# Run on a prepared heterogeneous remote host. The collector uses only local
# Hugging Face snapshots and measures actual CPU affinity and GPU replicas.
if [ "$#" -gt 1 ]; then
  echo "usage: $0 [round-count]" >&2
  exit 2
fi

round_count=${1:-10}
if ! [[ "$round_count" =~ ^[1-9][0-9]*$ ]]; then
  echo "round-count must be a positive integer" >&2
  exit 2
fi

experiment_root=${KHU_EXPERIMENT_ROOT:-"$HOME/khu-policy-experiment-20260911"}
runtime="$experiment_root/venv/bin/python"
source_root="$experiment_root/src"
raw_db="$experiment_root/runs/executions.sqlite3"
raw_csv_dir="$experiment_root/runs/csv"
placement_db="$experiment_root/runs/placement.sqlite3"
placement_csv_dir="$experiment_root/runs/placement-csv"
cache_dir=${HF_CACHE_DIR:-"$HOME/.cache/huggingface"}

if [ ! -x "$runtime" ]; then
  echo "missing runtime: $runtime" >&2
  exit 1
fi

mkdir -p "$experiment_root/runs" "$raw_csv_dir" "$placement_csv_dir"
for round in $(seq 1 "$round_count"); do
  printf -v suffix '%03d' "$round"
  collection_run="remote-cpu-scaling-round-$suffix"
  etl_run="remote-placement-etl-$suffix"
  printf '[campaign] round=%s started %s\n' "$suffix" "$(date -u +%FT%TZ)"
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$source_root" TOKENIZERS_PARALLELISM=false \
    "$runtime" -B -m khu_policy_agent collect-live \
      --output-db "$raw_db" --cache-dir "$cache_dir" --csv-dir "$raw_csv_dir" \
      --run-id "$collection_run" --device both --profile-set remote-cpu-scaling --max-cases 32 \
      --cpu-core-counts 2,4,8,16 --apply-cpu-core-counts-to-gpu \
      --sample-interval-seconds 0.10 --seed "$((32000 + round))"
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$source_root" \
    "$runtime" -B -m khu_policy_agent prepare-live-dataset \
      --source-db "$raw_db" --output-db "$placement_db" --csv-dir "$placement_csv_dir" \
      --run-id "$etl_run" --collection-run-id "$collection_run" --seed 42
  printf '[campaign] round=%s completed %s\n' "$suffix" "$(date -u +%FT%TZ)"
done
