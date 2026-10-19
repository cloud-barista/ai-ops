#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v go >/dev/null 2>&1; then
  echo "[ERROR] Go was not found. Install Go 1.25 or later and restart the terminal." >&2
  exit 1
fi

export AIOPS_REPO_ROOT="$ROOT_DIR"
export AIOPS_BIND_ADDRESS="${AIOPS_BIND_ADDRESS:-127.0.0.1}"
export AIOPS_LLMOP_CANDIDATE_ID="${AIOPS_LLMOP_CANDIDATE_ID:-qwen3.5-profile-generator}"
export AIOPS_LLMOP_MAX_LLM_ATTEMPTS="${AIOPS_LLMOP_MAX_LLM_ATTEMPTS:-2}"
export PORT="${PORT:-18080}"

cd "$ROOT_DIR/go/service-control-api"

echo "Starting LLM_Op ApplicationProfile and model-selection API at http://${AIOPS_BIND_ADDRESS}:${PORT}/"
echo "ApplicationProfile candidate: ${AIOPS_LLMOP_CANDIDATE_ID}"
exec go run ./cmd/llmop-api
