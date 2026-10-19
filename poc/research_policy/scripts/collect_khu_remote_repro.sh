#!/usr/bin/env bash
set -Eeuo pipefail

# Reproducible KHU live-inference collection.
#
# Run this on research-host (or copy it there first).  The collector itself pins
# model revisions in khu_policy_agent.live_collector; this wrapper pins the
# workload matrix, resource conditions, seeds, and ETL procedure.
#
# Example:
#   START_SHARD=0 END_SHARD=3 RUN_PREFIX=paper-a \
#     ./collect_khu_remote_repro.sh

ROOT="${EXPERIMENT_ROOT:-/home/researcher/khu-policy-experiment-20260911}"
RUNTIME="${PYTHON_RUNTIME:-$ROOT/venv/bin/python}"
PYTHONPATH_ROOT="${PYTHONPATH_ROOT:-$ROOT/src}"
RAW_DB="${RAW_DB:-$ROOT/runs/executions.sqlite3}"
LIFECYCLE_DB="${LIFECYCLE_DB:-$ROOT/runs/observed-lifecycle.sqlite3}"
CACHE_DIR="${CACHE_DIR:-/home/researcher/.cache/huggingface}"
CSV_DIR="${CSV_DIR:-$ROOT/runs/csv}"
LOG_DIR="${LOG_DIR:-$ROOT/runs/repro-logs}"

START_SHARD="${START_SHARD:-0}"
END_SHARD="${END_SHARD:-19}"
RUN_PREFIX="${RUN_PREFIX:-remote-repro}"
SEED_BASE="${SEED_BASE:-80000}"
MAX_CASES="${MAX_CASES:-1600}"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-0.10}"
CPU_CORE_COUNTS="${CPU_CORE_COUNTS:-2,4,8,16}"
# One CPU limit per workload, rotated across the four limits.  Set to 0 to
# reproduce the exhaustive CPU matrix instead.
CPU_CORE_SAMPLES_PER_PROFILE="${CPU_CORE_SAMPLES_PER_PROFILE:-1}"

PROFILE_SET="remote-exhaustive"
DEVICE_SCOPE="both"

die() { echo "[repro] ERROR: $*" >&2; exit 1; }
[[ -x "$RUNTIME" ]] || die "Python runtime not found: $RUNTIME"
[[ -f "$ROOT/src/khu_policy_agent/live_collector.py" ]] || die "collector source not found under $ROOT/src"
command -v nvidia-smi >/dev/null || die "nvidia-smi is required"
(( START_SHARD >= 0 && END_SHARD >= START_SHARD )) || die "invalid shard range"

mkdir -p "$LOG_DIR" "$CSV_DIR" "$(dirname "$RAW_DB")" "$(dirname "$LIFECYCLE_DB")"
exec 9>"$ROOT/runs/.khu-collection.lock"
flock -n 9 || die "another KHU collection is already running for $ROOT"

free_kib() { df -Pk "$ROOT" | awk 'NR == 2 {print $4}'; }

status_of() {
  "$RUNTIME" - "$RAW_DB" "$1" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
row = connection.execute(
    "SELECT status FROM collection_runs WHERE run_id = ?", (sys.argv[2],)
).fetchone()
print(row[0] if row else "missing")
PY
}

run_etl() {
  local collection_run_id="$1"
  local etl_run_id="$2"
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PYTHONPATH_ROOT" \
    "$RUNTIME" -B -m khu_policy_agent prepare-observed-lifecycle-dataset \
    --source-db "$RAW_DB" \
    --output-db "$LIFECYCLE_DB" \
    --csv-dir "$CSV_DIR" \
    --run-id "$etl_run_id" \
    --collection-run-id "$collection_run_id" \
    --seed 42
}

for ((shard=START_SHARD; shard<=END_SHARD; shard++)); do
  (( $(free_kib) >= 20971520 )) || die "free disk is below 20 GiB"
  suffix=$(printf '%02d' "$shard")
  collection_run_id="${RUN_PREFIX}-s${suffix}"
  etl_run_id="${RUN_PREFIX}-lifecycle-s${suffix}"
  log_file="$LOG_DIR/${collection_run_id}.log"

  {
    echo "[repro] started $(date -u +%FT%TZ)"
    echo "[repro] root=$ROOT profile_set=$PROFILE_SET shard=$shard"
    echo "[repro] raw_db=$RAW_DB lifecycle_db=$LIFECYCLE_DB"
    echo "[repro] free_kib=$(free_kib)"

    env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PYTHONPATH_ROOT" \
      TOKENIZERS_PARALLELISM=false "$RUNTIME" -B -m khu_policy_agent collect-live \
      --output-db "$RAW_DB" \
      --cache-dir "$CACHE_DIR" \
      --csv-dir "$CSV_DIR" \
      --run-id "$collection_run_id" \
      --device "$DEVICE_SCOPE" \
      --profile-set "$PROFILE_SET" \
      --remote-grid-shard "$shard" \
      --max-cases "$MAX_CASES" \
      --cpu-core-counts "$CPU_CORE_COUNTS" \
      --cpu-core-samples-per-profile "$CPU_CORE_SAMPLES_PER_PROFILE" \
      --apply-cpu-core-counts-to-gpu \
      --skip-existing-conditions \
      --sample-interval-seconds "$SAMPLE_INTERVAL" \
      --seed "$((SEED_BASE + shard))"

    status=$(status_of "$collection_run_id")
    [[ "$status" == complete ]] || die "collection run $collection_run_id ended with status=$status"
    run_etl "$collection_run_id" "$etl_run_id"
    echo "[repro] completed $(date -u +%FT%TZ) free_kib=$(free_kib)"
  } 2>&1 | tee "$log_file"
done

echo "[repro] all shards completed $(date -u +%FT%TZ)"
