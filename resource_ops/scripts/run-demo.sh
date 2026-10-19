#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
agent_dir="$(cd "${script_dir}/.." && pwd)"
agent_port="${AICMP_DEMO_AGENT_PORT:-18081}"
agent_url="${AICMP_DEMO_AGENT_URL:-http://127.0.0.1:${agent_port}}"
request_path="${1:-${agent_dir}/fixtures/application-profile-request-gpu.json}"
demo_temp_dir="$(mktemp -d)"
agent_binary="${demo_temp_dir}/ai-mcmp-agent"
agent_log="${demo_temp_dir}/agent.log"
agent_pid=""

cleanup() {
  if [[ -n "${agent_pid}" ]]; then
    kill "${agent_pid}" 2>/dev/null || true
    wait "${agent_pid}" 2>/dev/null || true
  fi
  rm -rf "${demo_temp_dir}"
}
trap cleanup EXIT

if ! curl -fsS "${agent_url}/health" >/dev/null 2>&1; then
  (
    cd "${agent_dir}"
    GOTOOLCHAIN=auto go build -o "${agent_binary}" ./cmd/server
  )
  env HOST=127.0.0.1 PORT="${agent_port}" "${agent_binary}" >"${agent_log}" 2>&1 &
  agent_pid=$!

  for _ in $(seq 1 120); do
    if curl -fsS "${agent_url}/health" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "${agent_pid}" 2>/dev/null; then
      echo "Resource Ops failed to start:" >&2
      sed -n '1,160p' "${agent_log}" >&2
      exit 1
    fi
    sleep 0.25
  done
fi

if ! curl -fsS "${agent_url}/health" >/dev/null 2>&1; then
  echo "Resource Ops did not become healthy at ${agent_url}" >&2
  exit 1
fi

curl -fsS \
  -X POST "${agent_url}/api/v1/resource-recommendations" \
  -H 'Content-Type: application/json' \
  --data-binary "@${request_path}"
printf '\n'
