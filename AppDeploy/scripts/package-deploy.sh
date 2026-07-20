#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  package-deploy.sh --type TYPE [--source FILE] [--name NAME] [--version VERSION]
    [--entrypoint PATH] [--runtime cpu|gpu] [--port PORT] [--health-path PATH]
    [--target-id ID] [--base-url URL] [--build-only]

TYPE: aiops-geon-service-control, go, python, node, binary, script
Requires: Bash 3.2+, curl, jq
EOF
}

base_url="http://localhost:8080"
package_type=""
source_path=""
app_name=""
app_version="0.1.0"
entrypoint=""
runtime_type="cpu"
service_port="0"
health_path="/health"
target_id=""
build_only="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) base_url="$2"; shift 2 ;;
    --type) package_type="$2"; shift 2 ;;
    --source) source_path="$2"; shift 2 ;;
    --name|--app-name) app_name="$2"; shift 2 ;;
    --version) app_version="$2"; shift 2 ;;
    --entrypoint) entrypoint="$2"; shift 2 ;;
    --runtime|--runtime-type) runtime_type="$2"; shift 2 ;;
    --port|--service-port) service_port="$2"; shift 2 ;;
    --health-path) health_path="$2"; shift 2 ;;
    --target-id|--target-profile-id) target_id="$2"; shift 2 ;;
    --build-only) build_only="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for command in curl jq; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "$command is required" >&2
    exit 2
  fi
done

case "$package_type" in
  aiops-geon-service-control|go|python|node|binary|script) ;;
  *) echo "--type must be aiops-geon-service-control, go, python, node, binary, or script" >&2; exit 2 ;;
esac
case "$runtime_type" in
  cpu|gpu) ;;
  *) echo "--runtime must be cpu or gpu" >&2; exit 2 ;;
esac
if ! [[ "$service_port" =~ ^[0-9]+$ ]] || (( service_port > 65535 )); then
  echo "--port must be 0 or 1-65535" >&2
  exit 2
fi
base_url="${base_url%/}"
request_id="req-package-shell-$(date -u +%Y%m%d%H%M%S)-$$"
work_dir="$(mktemp -d)"
cleanup() {
  rm -f -- "$work_dir/package.json" "$work_dir/app.json" "$work_dir/resource.json" "$work_dir/deployment.json"
  rmdir -- "$work_dir" 2>/dev/null || true
}
trap cleanup EXIT

print_partial_state() {
  local archive_name=""
  local artifact_uri=""
  local app_id=""
  local app_version_id=""
  local resource_status=""
  if [[ -n "${package_file-}" && -s "${package_file-}" ]]; then
    archive_name="$(jq -r '.archive_name // empty' "$package_file" 2>/dev/null || true)"
    artifact_uri="$(jq -r '.artifact_uri // empty' "$package_file" 2>/dev/null || true)"
  fi
  if [[ -n "${app_file-}" && -s "${app_file-}" ]]; then
    app_id="$(jq -r '.app_id // empty' "$app_file" 2>/dev/null || true)"
    app_version_id="$(jq -r '.app_version_id // empty' "$app_file" 2>/dev/null || true)"
  fi
  if [[ -n "${resource_file-}" && -s "${resource_file-}" ]]; then
    resource_status="$(jq -r '.status // empty' "$resource_file" 2>/dev/null || true)"
  fi
  if [[ -n "$archive_name$artifact_uri$app_id$app_version_id$resource_status" ]]; then
    jq -cn \
      --arg archive_name "$archive_name" \
      --arg artifact_uri "$artifact_uri" \
      --arg app_id "$app_id" \
      --arg app_version_id "$app_version_id" \
      --arg resource_check_status "$resource_status" \
      '{archive_name:$archive_name, artifact_uri:$artifact_uri, app_id:$app_id, app_version_id:$app_version_id, resource_check_status:$resource_check_status}' \
      | sed 's/^/partial_result: /' >&2
  fi
}

api_call() {
  local method="$1"
  local path="$2"
  local output="$3"
  local body="${4-}"
  local status
  if [[ -n "$body" ]]; then
    if ! status="$(curl -sS -o "$output" -w '%{http_code}' -X "$method" \
      -H "X-Request-ID: $request_id" -H 'Accept: application/json' -H 'Content-Type: application/json' \
      --data-binary "$body" "$base_url$path")"; then
      echo "API $method $path transport failed" >&2
      print_partial_state
      exit 1
    fi
  else
    if ! status="$(curl -sS -o "$output" -w '%{http_code}' -X "$method" \
      -H "X-Request-ID: $request_id" -H 'Accept: application/json' "$base_url$path")"; then
      echo "API $method $path transport failed" >&2
      print_partial_state
      exit 1
    fi
  fi
  if [[ ! "$status" =~ ^2 ]]; then
    echo "API $method $path failed: HTTP $status" >&2
    cat "$output" >&2
    print_partial_state
    exit 1
  fi
}

package_file="$work_dir/package.json"
if [[ "$package_type" == "aiops-geon-service-control" ]]; then
  if (( service_port == 0 )); then service_port=18089; fi
  package_body="$(jq -cn --arg preset "$package_type" --arg version "$app_version" --argjson port "$service_port" \
    '{preset:$preset, service_port:$port} + (if $version == "" then {} else {app_version:$version} end)')"
  api_call POST /api/v1/artifacts/packages "$package_file" "$package_body"
else
  if [[ -z "$source_path" || ! -f "$source_path" || ! -s "$source_path" ]]; then
    echo "--source must be a non-empty file for $package_type packages" >&2
    exit 2
  fi
  source_name="$(basename -- "$source_path")"
  source_name_lower="$(printf '%s' "$source_name" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "$app_name" ]]; then
    app_name="$(printf '%s' "${source_name%.*}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')"
    if (( ${#app_name} < 2 )); then app_name="app-${app_name:-upload}"; fi
    app_name="${app_name:0:63}"
    app_name="${app_name%-}"
  fi
  if [[ -z "$entrypoint" && "$source_name_lower" != *.zip && "$package_type" != "go" ]]; then
    entrypoint="$source_name"
  fi
  if [[ -z "$entrypoint" ]]; then
    case "$package_type" in
      go) entrypoint="." ;;
      python) entrypoint="main.py" ;;
      node) entrypoint="index.js" ;;
      script) entrypoint="run.sh" ;;
      *) echo "--entrypoint is required for this ZIP package" >&2; exit 2 ;;
    esac
  fi
  multipart=(
    --form-string "package_type=$package_type"
    --form "source=@$source_path"
    --form-string "app_name=$app_name"
    --form-string "app_version=$app_version"
    --form-string "entrypoint=$entrypoint"
    --form-string "runtime_type=$runtime_type"
  )
  if (( service_port > 0 )); then
    multipart+=( --form-string "service_port=$service_port" --form-string "healthcheck_path=$health_path" )
  fi
  if ! status="$(curl -sS -o "$package_file" -w '%{http_code}' -X POST \
    -H "X-Request-ID: $request_id" -H 'Accept: application/json' \
    "${multipart[@]}" "$base_url/api/v1/artifacts/packages")"; then
    echo "API POST /api/v1/artifacts/packages transport failed" >&2
    exit 1
  fi
  if [[ ! "$status" =~ ^2 ]]; then
    echo "API POST /api/v1/artifacts/packages failed: HTTP $status" >&2
    cat "$package_file" >&2
    print_partial_state
    exit 1
  fi
fi

if [[ "$build_only" == "true" ]]; then
  jq . "$package_file"
  exit 0
fi
app_file="$work_dir/app.json"
resource_file="$work_dir/resource.json"
deployment_file="$work_dir/deployment.json"
if ! app_body="$(jq -ce '{app_spec:.app_spec} | select(.app_spec != null)' "$package_file")"; then
  echo "Package response does not contain app_spec" >&2
  print_partial_state
  exit 1
fi
api_call POST /api/v1/apps "$app_file" "$app_body"
if ! app_version_id="$(jq -er '.app_version_id' "$app_file")"; then
  echo "App response does not contain app_version_id" >&2
  print_partial_state
  exit 1
fi
if [[ -n "$target_id" ]]; then
  resource_body="$(jq -cn --arg target "$target_id" '{target_profile_id:$target}')"
  api_call POST /api/v1/resources/check "$resource_file" "$resource_body"
  if ! resource_status="$(jq -er '.status // empty' "$resource_file")"; then
    echo "Resource Check response does not contain status" >&2
    print_partial_state
    exit 1
  fi
  if [[ "$resource_status" != "available" ]]; then
    echo "Resource Check status is '${resource_status:-missing}'; Deployment was not created" >&2
    print_partial_state
    exit 1
  fi
fi
deployment_body="$(jq -cn --arg app "$app_version_id" --arg target "$target_id" \
  '{app_version_id:$app, requested_by:"appdeploy-bash-package"} + (if $target == "" then {} else {target_profile_id:$target} end)')"
api_call POST /api/v1/deployments "$deployment_file" "$deployment_body"

jq -n \
  --slurpfile package "$package_file" \
  --slurpfile app "$app_file" \
  --slurpfile resource "$resource_file" \
  --slurpfile deployment "$deployment_file" \
  '{package:$package[0], app:$app[0], resource_check:$resource[0], deployment:$deployment[0]}'
