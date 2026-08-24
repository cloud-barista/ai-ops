#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v go >/dev/null 2>&1; then
  echo "[ERROR] Go was not found. Install Go 1.25 or later and restart the terminal." >&2
  exit 1
fi

export AIOPS_REPO_ROOT="$ROOT_DIR"
export AIOPS_BIND_ADDRESS="${AIOPS_BIND_ADDRESS:-127.0.0.1}"
export AIOPS_DEPLOYMENT_ADAPTER="${AIOPS_DEPLOYMENT_ADAPTER:-mock}"
export AIOPS_LLM_CANDIDATES_PATH="${AIOPS_LLM_CANDIDATES_PATH:-config/ops_llm_eval_candidates.local_ollama.json}"
export AIOPS_LLMOP_ALLOW_LIVE_COMPLETION="${AIOPS_LLMOP_ALLOW_LIVE_COMPLETION:-true}"
export PORT="${PORT:-18080}"

cd "$ROOT_DIR/go/service-control-api"

echo "Starting geon Agent Control at http://${AIOPS_BIND_ADDRESS}:${PORT}/"
echo "Deployment adapter: ${AIOPS_DEPLOYMENT_ADAPTER}"
echo "LLM_Op Safeguard: Qwen via Ollama"
exec go run ./cmd/service-control-api
