#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  printf '사용법은 %s NS_ID DEPLOYMENT_ID [LLM_TYPE]입니다.\n' "$0" >&2
  exit 2
fi

namespace=$1
deployment_id=$2
llm_type="${3:-self_hosted}"
ops_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../../ai-app/docs/api/examples/common.sh
source "$ops_root/../ai-app/docs/api/examples/common.sh"

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

[[ "$namespace" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail "네임스페이스를 확인하세요."
[[ "$deployment_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || \
  fail "실제 AI-App 배포 UUID를 입력하세요."

aiapp GET /partner/nl/readyz >/dev/null
body=$(jq -n --arg type "$llm_type" '{intent:"배포 가능한 응용을 보여줘",llmType:$type}')
catalog=$(aiapp POST "/partner/nl/ns/$namespace/intents" -H 'Content-Type: application/json' -d "$body")
jq -e --arg type "$llm_type" '
  .status == "succeeded" and .opsLlm.status == "completed" and .opsLlm.llm_type == $type
  and any(.steps[]; .tool == "list_applications" and .executed == true)
' <<<"$catalog" >/dev/null || fail "자연어 카탈로그 조회와 OpsLLM 실행 결과를 확인하세요."
jq '{runId,status,answer,opsLlm}' <<<"$catalog"

body=$(jq -n --arg type "$llm_type" --arg id "$deployment_id" '{
  intent:("배포 " + $id + " 상태를 조회해줘"),llmType:$type
}')
status=$(aiapp POST "/partner/nl/ns/$namespace/intents" -H 'Content-Type: application/json' -d "$body")
jq -e --arg id "$deployment_id" '
  .status == "succeeded" and .opsLlm.status == "completed"
  and any(.steps[]; .tool == "application_status" and .executed == true
    and .agent == "ai-mcmp-app-automation-engine" and .input.deploymentId == $id)
' <<<"$status" >/dev/null || fail "자연어 배포 조회와 엔진 MCP 실행 결과를 확인하세요."
jq '{runId,status,answer,opsLlm,steps:[.steps[] | {tool,agent,executed,engineRunId,engineStatus}]}' <<<"$status"
run_id=$(jq -er '.runId' <<<"$status")
ops_run_id=$(jq -er '.opsLlm.run_id' <<<"$status")

protocol_version=2025-03-26
mcp_call() {
  local method=$1 params=$2 identifier=$3 request
  request=$(jq -n --arg method "$method" --argjson params "$params" --argjson id "$identifier" \
    '{jsonrpc:"2.0",id:$id,method:$method,params:$params}')
  aiapp POST /partner/nl/mcp \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "X-AIMCMP-Namespace: $namespace" \
    -H "MCP-Protocol-Version: $protocol_version" -d "$request"
}

initialized=$(mcp_call initialize \
  '{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"aiops-integration-check","version":"1.0"}}' 1)
protocol_version=$(jq -er '.result.protocolVersion' <<<"$initialized")
aiapp POST /partner/nl/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "X-AIMCMP-Namespace: $namespace" \
  -H "MCP-Protocol-Version: $protocol_version" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
tools=$(mcp_call tools/list '{}' 2)
jq -e 'any(.result.tools[]; .name == "get_application_intent_run")' <<<"$tools" >/dev/null || \
  fail "자연어 에이전트 MCP의 실행 기록 도구를 확인하세요."
params=$(jq -n --arg id "$run_id" '{name:"get_application_intent_run",arguments:{runId:$id}}')
lookup=$(mcp_call tools/call "$params" 3)
jq -e --arg run "$run_id" --arg ops "$ops_run_id" '
  .result.isError != true and .result.structuredContent.runId == $run
  and .result.structuredContent.opsLlm.run_id == $ops
' <<<"$lookup" >/dev/null || fail "REST와 MCP의 실행 기록이 일치하는지 확인하세요."
printf '통합 시험 통과. REST·MCP 실행 ID %s, OpsLLM 실행 ID %s\n' "$run_id" "$ops_run_id"
