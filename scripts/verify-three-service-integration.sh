#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
deployment_dir="$(cd -- "${script_dir}/.." && pwd)"
workspace_dir="$(cd -- "${deployment_dir}/.." && pwd)"
llmop_dir="${workspace_dir}/llmop"
resource_dir="${workspace_dir}/resource_ops"

llmop_port="${LLMOP_INTEGRATION_PORT:-18081}"
resource_port="${RESOURCE_OPS_INTEGRATION_PORT:-18082}"
deployment_port="${DEPLOYMENT_AGENT_INTEGRATION_PORT:-18083}"
llmop_url="http://127.0.0.1:${llmop_port}"
resource_url="http://127.0.0.1:${resource_port}"
deployment_url="http://127.0.0.1:${deployment_port}"
go_bin="${GO_BIN:-go}"

for command_name in curl jq "${go_bin}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ERROR] Required command was not found: ${command_name}" >&2
    exit 1
  fi
done
for directory in "${llmop_dir}" "${resource_dir}" "${deployment_dir}"; do
  if [[ ! -d "${directory}" ]]; then
    echo "[ERROR] Integration repository was not found: ${directory}" >&2
    exit 1
  fi
done

integration_temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ai-mcmp-integration.XXXXXX")"
pids=()

cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  rm -rf -- "${integration_temp_dir}"
}
trap cleanup EXIT

build_service() {
  local workdir="$1"
  local package="$2"
  local output="$3"
  (
    cd "${workdir}"
    GOTOOLCHAIN=auto "${go_bin}" build -o "${output}" "${package}"
  )
}

wait_for_health() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local log_path="$4"
  for _ in $(seq 1 120); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "[ERROR] ${name} stopped before becoming healthy." >&2
      sed -n '1,200p' "${log_path}" >&2
      return 1
    fi
    sleep 0.25
  done
  echo "[ERROR] ${name} did not become healthy: ${url}" >&2
  sed -n '1,200p' "${log_path}" >&2
  return 1
}

llmop_binary="${integration_temp_dir}/llmop-api"
resource_binary="${integration_temp_dir}/resource-ops"
deployment_binary="${integration_temp_dir}/deployment-agent"

build_service "${llmop_dir}/go/service-control-api" ./cmd/llmop-api "${llmop_binary}"
build_service "${resource_dir}" ./cmd/server "${resource_binary}"
build_service "${deployment_dir}/go/service-control-api" ./cmd/service-control-api "${deployment_binary}"

env \
  AIOPS_REPO_ROOT="${llmop_dir}" \
  AIOPS_BIND_ADDRESS=127.0.0.1 \
  PORT="${llmop_port}" \
  "${llmop_binary}" >"${integration_temp_dir}/llmop.log" 2>&1 &
pids+=("$!")
wait_for_health llmop "${llmop_url}/healthz" "${pids[-1]}" "${integration_temp_dir}/llmop.log"

env \
  HOST=127.0.0.1 \
  PORT="${resource_port}" \
  RESOURCE_PROMETHEUS_URL="${RESOURCE_PROMETHEUS_URL:-http://163.180.117.64:9090}" \
  "${resource_binary}" >"${integration_temp_dir}/resource-ops.log" 2>&1 &
pids+=("$!")
wait_for_health resource_ops "${resource_url}/health" "${pids[-1]}" "${integration_temp_dir}/resource-ops.log"

env \
  AIOPS_REPO_ROOT="${deployment_dir}" \
  AIOPS_BIND_ADDRESS=127.0.0.1 \
  AIOPS_LEGACY_API_ENABLED=false \
  PORT="${deployment_port}" \
  "${deployment_binary}" >"${integration_temp_dir}/deployment-agent.log" 2>&1 &
pids+=("$!")
wait_for_health deployment_agent "${deployment_url}/healthz" "${pids[-1]}" "${integration_temp_dir}/deployment-agent.log"

correlation_id="flow-three-service-integration"
trace_id="trace-three-service-integration"
llmop_result="${integration_temp_dir}/llmop-result.json"
resource_request="${integration_temp_dir}/resource-request.json"
resource_result="${integration_temp_dir}/resource-result.json"
deployment_request="${integration_temp_dir}/deployment-request.json"
deployment_result="${integration_temp_dir}/deployment-result.json"

curl -fsS \
  -X POST "${llmop_url}/api/v1/application-profiles" \
  -H 'Content-Type: application/json' \
  --data-binary "@${llmop_dir}/examples/application-profile/generate-request.json" \
  >"${llmop_result}"

jq -n \
  --arg request_id "${correlation_id}" \
  --slurpfile llmop "${llmop_result}" \
  '{schema_version:"1.0",request_id:$request_id,application_profile:$llmop[0].application_profile,preferences:{max_results:3}}' \
  >"${resource_request}"

curl -fsS \
  -X POST "${resource_url}/api/v1/resource-recommendations" \
  -H 'Content-Type: application/json' \
  --data-binary "@${resource_request}" \
  >"${resource_result}"

jq -n \
  --arg correlation_id "${correlation_id}" \
  --arg trace_id "${trace_id}" \
  --slurpfile llmop "${llmop_result}" \
  --slurpfile resources "${resource_result}" \
  '{schema_version:"1.0",correlation_id:$correlation_id,trace_id:$trace_id,application_profile:$llmop[0].application_profile,resource_recommendation:$resources[0]}' \
  >"${deployment_request}"

curl -fsS \
  -X POST "${deployment_url}/api/v1/deployment-plans" \
  -H 'Content-Type: application/json' \
  --data-binary "@${deployment_request}" \
  >"${deployment_result}"

jq -e \
  --arg correlation_id "${correlation_id}" \
  --arg profile_id "$(jq -r '.application_profile.profile_id' "${llmop_result}")" \
  '.correlation_id == $correlation_id and .profile_id == $profile_id and .state == "DEPLOY_APPROVED" and .decision.action == "DEPLOY" and (.deployment_plan.selected_candidate_id | length > 0)' \
  "${deployment_result}" >/dev/null

jq -n \
  --slurpfile llmop "${llmop_result}" \
  --slurpfile resources "${resource_result}" \
  --slurpfile deployment "${deployment_result}" \
  '{valid:true,llmop:{profile_id:$llmop[0].application_profile.profile_id,analysis_mode:$llmop[0].analysis.mode},resource_ops:{source:$resources[0].source,feasible:$resources[0].summary.feasible,top_resource:$resources[0].recommendations[0].resource.resource_id},deployment_agent:{state:$deployment[0].state,action:$deployment[0].decision.action,selected_candidate_id:$deployment[0].deployment_plan.selected_candidate_id,plan_id:$deployment[0].deployment_plan.plan_id}}'
