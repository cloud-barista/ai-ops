#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-"$ROOT_DIR/runs/local-api-integration-validation-$(date +%Y%m%d-%H%M%S)"}"
PORT="${2:-18080}"
SERVICE_DIR="$ROOT_DIR/go/service-control-api"
API_DIR="$OUTPUT_DIR/api-integration-validation"
BASE_URL="http://127.0.0.1:$PORT"
GO_BIN="${GO_BIN:-go}"

if ! command -v "$GO_BIN" >/dev/null 2>&1; then
  if [[ -x /usr/local/go/bin/go ]]; then
    GO_BIN="/usr/local/go/bin/go"
  else
    echo "go binary was not found. Set GO_BIN or install Go." >&2
    exit 1
  fi
fi

mkdir -p "$API_DIR"

SERVER_LOG="$API_DIR/server.log"
(
  cd "$SERVICE_DIR"
  PORT="$PORT" "$GO_BIN" run ./cmd/service-control-api
) >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if curl -fsS "$BASE_URL/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "$BASE_URL/healthz" >/dev/null 2>&1; then
  echo "service-control-api did not become ready on $BASE_URL" >&2
  cat "$SERVER_LOG" >&2 || true
  exit 1
fi

curl -fsS "$BASE_URL/healthz" \
  -o "$API_DIR/00-healthz.json"

curl -fsS "$BASE_URL/api/v1/agents" \
  -o "$API_DIR/01-agents.json"

curl -fsS -X POST "$BASE_URL/api/v1/ops-llm/select" \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}' \
  -o "$API_DIR/02-ops-llm-select.json"

curl -fsS -X POST "$BASE_URL/api/v1/apps/placement" \
  -H 'content-type: application/json' \
  -d '{"workload":"llm-chat-inference"}' \
  -o "$API_DIR/03-placement.json"

curl -fsS -X POST "$BASE_URL/api/v1/apps/deployment-plan" \
  -H 'content-type: application/json' \
  -d '{"workload":"llm-chat-inference"}' \
  -o "$API_DIR/04-deployment-plan.json"

curl -fsS -X POST "$BASE_URL/api/v1/service-operations/run" \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","operation_service":"llm-chat-inference","operation_resource":"gpu-vm-l4","mode":"mock","guard_backend":"go"}' \
  -o "$API_DIR/05-service-operations-run.json"

python3 - "$API_DIR" "$OUTPUT_DIR/api-integration-validation-summary.json" <<'PY'
import json
import sys
from pathlib import Path

api_dir = Path(sys.argv[1])
summary_path = Path(sys.argv[2])

checks = []

def load(name):
    path = api_dir / name
    with path.open(encoding="utf-8") as f:
        return json.load(f), path

def add(endpoint, method, file_name, fields_checked, valid, note=""):
    checks.append({
        "endpoint": endpoint,
        "method": method,
        "artifact": str((api_dir / file_name).as_posix()),
        "fields_checked": fields_checked,
        "valid": bool(valid),
        "note": note,
    })

healthz, _ = load("00-healthz.json")
add(
    "/healthz",
    "GET",
    "00-healthz.json",
    ["status", "service"],
    healthz.get("status") == "ok" and healthz.get("service") == "service-control-api",
)

agents, _ = load("01-agents.json")
add(
    "/api/v1/agents",
    "GET",
    "01-agents.json",
    ["agents", "version", "command"],
    isinstance(agents.get("agents"), list)
    and len(agents.get("agents", [])) >= 1
    and agents.get("command") == "list-agents",
)

llm, _ = load("02-ops-llm-select.json")
add(
    "/api/v1/ops-llm/select",
    "POST",
    "02-ops-llm-select.json",
    ["valid", "selected_model", "selected_actual_model", "benchmark_status"],
    llm.get("valid") is True
    and bool(llm.get("selected_model"))
    and "benchmark_status" in llm,
)

placement, _ = load("03-placement.json")
add(
    "/api/v1/apps/placement",
    "POST",
    "03-placement.json",
    ["valid", "selected_resource", "action", "slo_satisfied"],
    placement.get("valid") is True
    and bool(placement.get("selected_resource"))
    and bool(placement.get("action")),
)

deployment, _ = load("04-deployment-plan.json")
add(
    "/api/v1/apps/deployment-plan",
    "POST",
    "04-deployment-plan.json",
    ["valid", "selected_resource", "deployment_plan"],
    deployment.get("valid") is True
    and bool(deployment.get("selected_resource"))
    and isinstance(deployment.get("deployment_plan"), dict),
)

service_ops, _ = load("05-service-operations-run.json")
guard_validation = service_ops.get("guard_validation", {})
add(
    "/api/v1/service-operations/run",
    "POST",
    "05-service-operations-run.json",
    [
        "valid",
        "selected_llm",
        "benchmark_status",
        "selected_resource",
        "deployment_plan",
        "deployment_validation",
        "guard_backend",
        "guard_validation",
    ],
    service_ops.get("valid") is True
    and bool(service_ops.get("selected_llm"))
    and "benchmark_status" in service_ops
    and bool(service_ops.get("selected_resource"))
    and isinstance(service_ops.get("deployment_plan"), dict)
    and isinstance(service_ops.get("deployment_validation"), dict)
    and service_ops.get("guard_backend") == "go"
    and guard_validation.get("valid") is True,
)

summary = {
    "command": "local-api-integration-validation",
    "validation_type": "local_api_integration_validation",
    "target": "local",
    "base_url": "http://127.0.0.1:<port>",
    "endpoint_count": len(checks),
    "valid_endpoint_count": sum(1 for check in checks if check["valid"]),
    "valid": all(check["valid"] for check in checks),
    "production_level_validation": False,
    "full_operational_validation": False,
    "description": "Local service-control API endpoints were called sequentially and key response fields were verified. This is not production-level operational validation.",
    "endpoints": checks,
}

summary_path.parent.mkdir(parents=True, exist_ok=True)
with summary_path.open("w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(json.dumps(summary, ensure_ascii=False, indent=2))
PY

echo "Local API integration validation summary: $OUTPUT_DIR/api-integration-validation-summary.json"
