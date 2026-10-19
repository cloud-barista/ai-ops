#!/usr/bin/env bash
# Run the AI-MCMP resource discovery and recommendation API.
set -euo pipefail
cd "$(dirname "$0")"
agent_host="${AICMP_AGENT_HOST:-0.0.0.0}"
agent_port="${AICMP_AGENT_PORT:-8000}"
prometheus_url="${RESOURCE_PROMETHEUS_URL:-http://127.0.0.1:9090}"
echo "Starting AI-MCMP resource-ops on http://${agent_host}:${agent_port}"
echo "Resource source: ${prometheus_url}"
exec env HOST="${agent_host}" PORT="${agent_port}" RESOURCE_PROMETHEUS_URL="${prometheus_url}" go run ./cmd/server
