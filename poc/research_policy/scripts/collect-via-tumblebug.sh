#!/usr/bin/env bash
set -Eeuo pipefail

# Provision one CB-Tumblebug node, run the KHU live collector, download and
# verify its data, then terminate the Infra after a successful download.

action="${1:-review}"
case "$action" in review|run|status|destroy) ;; *) echo "usage: $0 [review|run|status|destroy]" >&2; exit 2 ;; esac

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tb_base="${TUMBLEBUG_API_BASE_URL:-}"
tb_user="${TUMBLEBUG_USERNAME:-}"
tb_password="${TUMBLEBUG_PASSWORD:-}"
namespace="${TUMBLEBUG_NAMESPACE:-default}"
credential_holder="${TUMBLEBUG_CREDENTIAL_HOLDER:-}"
spec_id="${KHU_TB_SPEC_ID:-}"
image_id="${KHU_TB_IMAGE_ID:-}"
infra_id="${KHU_TB_INFRA_ID:-khu-collect-$(date -u +%Y%m%d%H%M%S)}"
node_group_id="${KHU_TB_NODE_GROUP_ID:-collector}"
root_disk_gib="${KHU_TB_ROOT_DISK_GIB:-80}"
root_disk_type="${KHU_TB_ROOT_DISK_TYPE:-}"
zone="${KHU_TB_ZONE:-}"
connection_name="${KHU_TB_CONNECTION_NAME:-}"
run_id="${KHU_RUN_ID:-$infra_id}"
output_dir="${KHU_LOCAL_OUTPUT_DIR:-$root_dir/runs/tumblebug/$run_id}"
poll_seconds="${KHU_TB_POLL_SECONDS:-15}"
timeout_seconds="${KHU_TB_READY_TIMEOUT_SECONDS:-1800}"
ssh_ready_poll_seconds="${KHU_TB_SSH_READY_POLL_SECONDS:-20}"
ssh_ready_timeout_seconds="${KHU_TB_SSH_READY_TIMEOUT_SECONDS:-900}"
ssh_settle_seconds="${KHU_TB_SSH_SETTLE_SECONDS:-10}"
collect_retry_attempts="${KHU_TB_COLLECT_RETRY_ATTEMPTS:-5}"
collect_retry_delay_seconds="${KHU_TB_COLLECT_RETRY_DELAY_SECONDS:-20}"
auto_destroy="${KHU_TB_AUTO_DESTROY:-1}"
destroy_on_failure="${KHU_TB_DESTROY_ON_FAILURE:-1}"

required=(curl jq tar sha256sum)
for command_name in "${required[@]}"; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "missing command: $command_name" >&2; exit 2; }
done
[[ -n "$tb_base" ]] || { echo "TUMBLEBUG_API_BASE_URL is required" >&2; exit 2; }
[[ -n "$tb_user" ]] || { echo "TUMBLEBUG_USERNAME is required" >&2; exit 2; }
[[ "$collect_retry_attempts" =~ ^[1-9][0-9]*$ ]] || { echo "KHU_TB_COLLECT_RETRY_ATTEMPTS must be positive" >&2; exit 2; }
[[ "$collect_retry_delay_seconds" =~ ^[0-9]+$ ]] || { echo "KHU_TB_COLLECT_RETRY_DELAY_SECONDS must be non-negative" >&2; exit 2; }
if [[ -z "$tb_password" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "CB-Tumblebug password: " tb_password
    echo >&2
  else
    echo "TUMBLEBUG_PASSWORD is required for non-interactive execution" >&2
    exit 2
  fi
fi
[[ -n "$tb_password" ]] || { echo "CB-Tumblebug password is empty" >&2; exit 2; }
[[ "$namespace" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid TUMBLEBUG_NAMESPACE" >&2; exit 2; }
[[ "$infra_id" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_TB_INFRA_ID" >&2; exit 2; }
[[ "$node_group_id" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_TB_NODE_GROUP_ID" >&2; exit 2; }
[[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_RUN_ID" >&2; exit 2; }
[[ "$root_disk_gib" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KHU_TB_ROOT_DISK_GIB" >&2; exit 2; }
[[ "$poll_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KHU_TB_POLL_SECONDS" >&2; exit 2; }
[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KHU_TB_READY_TIMEOUT_SECONDS" >&2; exit 2; }
[[ "$ssh_ready_poll_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KHU_TB_SSH_READY_POLL_SECONDS" >&2; exit 2; }
[[ "$ssh_ready_timeout_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "invalid KHU_TB_SSH_READY_TIMEOUT_SECONDS" >&2; exit 2; }
[[ "$ssh_settle_seconds" =~ ^[0-9]+$ ]] || { echo "invalid KHU_TB_SSH_SETTLE_SECONDS" >&2; exit 2; }
[[ -z "$root_disk_type" || "$root_disk_type" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_TB_ROOT_DISK_TYPE" >&2; exit 2; }
[[ -z "$zone" || "$zone" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_TB_ZONE" >&2; exit 2; }
[[ -z "$connection_name" || "$connection_name" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid KHU_TB_CONNECTION_NAME" >&2; exit 2; }
tb_base="${tb_base%/}"
mkdir -p "$output_dir"

auth_headers=(--user "$tb_user:$tb_password" --header "Accept: application/json")
if [[ -n "$credential_holder" ]]; then
  auth_headers+=(--header "x-credential-holder: $credential_holder")
fi

api_get() {
  curl --fail-with-body --silent --show-error "${auth_headers[@]}" "$1"
}

api_json() {
  local method="$1" url="$2" body="$3"
  curl --fail-with-body --silent --show-error "${auth_headers[@]}" \
    --header "Content-Type: application/json" --request "$method" --data-binary "@$body" "$url"
}

destroy_infra() {
  curl --fail-with-body --silent --show-error "${auth_headers[@]}" \
    --request DELETE "$tb_base/ns/$namespace/infra/$infra_id?option=terminate" \
    | tee "$output_dir/destroy-response.json"
}

infra_may_exist=0
cleanup_active=0
cleanup_on_exit() {
  local exit_code=$?
  if (( exit_code != 0 )) && [[ "$infra_may_exist" == 1 ]] && \
     [[ "$destroy_on_failure" == 1 ]] && [[ "$cleanup_active" == 0 ]]; then
    cleanup_active=1
    echo "collection failed; terminating Infra $infra_id to stop further VM cost" >&2
    set +e
    destroy_infra >&2
    set -e
  fi
  exit "$exit_code"
}
trap cleanup_on_exit EXIT

if [[ "$action" == status ]]; then
  api_get "$tb_base/ns/$namespace/infra/$infra_id" | tee "$output_dir/status.json" | jq .
  exit 0
fi
if [[ "$action" == destroy ]]; then
  destroy_infra
  exit 0
fi

[[ -n "$spec_id" ]] || { echo "KHU_TB_SPEC_ID is required" >&2; exit 2; }
[[ -n "$image_id" ]] || { echo "KHU_TB_IMAGE_ID is required" >&2; exit 2; }

request_json="$output_dir/infra-request.json"
jq -n \
  --arg name "$infra_id" \
  --arg group "$node_group_id" \
  --arg spec "$spec_id" \
  --arg image "$image_id" \
  --arg disk_type "$root_disk_type" \
  --arg zone "$zone" \
  --arg connection "$connection_name" \
  --argjson disk "$root_disk_gib" \
  '{
    name: $name,
    description: "KHU Hugging Face inference measurement node",
    installMonAgent: "no",
    policyOnPartialFailure: "rollback",
    label: {purpose: "khu-policy-data-collection"},
    nodeGroups: [({
      name: $group,
      nodeGroupSize: 1,
      specId: $spec,
      imageId: $image,
      rootDiskSize: $disk,
      label: {role: "khu-collector"}
    }
      + (if $disk_type != "" then {rootDiskType: $disk_type} else {} end)
      + (if $zone != "" then {zone: $zone} else {} end)
      + (if $connection != "" then {connectionName: $connection} else {} end))]
  }' > "$request_json"

api_get "$tb_base/readyz" | tee "$output_dir/readyz.json" | jq .
api_json POST "$tb_base/ns/$namespace/infraDynamicReview" "$request_json" \
  | tee "$output_dir/review-response.json" | jq .
if jq -e '(.creationViable == false) or (((.overallStatus // "") | ascii_downcase) == "error")' \
  "$output_dir/review-response.json" >/dev/null; then
  echo "CB-Tumblebug review rejected this Infra request; no VM was created" >&2
  exit 3
fi
if [[ "$action" == review ]]; then
  echo "review complete; no VM was created"
  exit 0
fi

package="$output_dir/khu-tb-collector.tar.gz"
tar -czf "$package" -C "$root_dir" src scripts
if (( $(wc -c < "$package") >= 50000000 )); then
  echo "collector package exceeds CB-Tumblebug's 50 MB transfer limit" >&2
  exit 3
fi

infra_may_exist=1
api_json POST "$tb_base/ns/$namespace/infraDynamic" "$request_json" \
  | tee "$output_dir/create-response.json" | jq .

deadline=$(( $(date +%s) + timeout_seconds ))
node_id=""
while (( $(date +%s) < deadline )); do
  status_json="$output_dir/status-latest.json"
  api_get "$tb_base/ns/$namespace/infra/$infra_id" > "$status_json"
  total="$(jq -r '.statusCount.countTotal // 0' "$status_json")"
  running="$(jq -r '.statusCount.countRunning // 0' "$status_json")"
  status="$(jq -r '.status // ""' "$status_json")"
  node_id="$(jq -r '.node[0].id // empty' "$status_json")"
  echo "infra=$infra_id status=$status running=$running/$total node=$node_id"
  if [[ "$total" =~ ^[1-9][0-9]*$ ]] && [[ "$running" == "$total" ]] && [[ -n "$node_id" ]]; then
    cp "$status_json" "$output_dir/status-running.json"
    break
  fi
  sleep "$poll_seconds"
done
[[ -n "$node_id" ]] || { echo "Infra did not become ready before timeout" >&2; exit 4; }

# A CSP can report a VM as Running before cloud-init has installed the SSH key
# and made the login account usable. GPU/DLAMI images commonly need longer than
# plain Ubuntu images, so probe the same Tumblebug remote-command path before
# attempting file transfer.
ssh_probe_request="$output_dir/ssh-probe-command.json"
ssh_probe_response="$output_dir/ssh-probe-response.json"
ssh_probe_error="$output_dir/ssh-probe-error.log"
jq -n '{command: ["echo KHU_SSH_READY"], timeoutMinutes: 2}' > "$ssh_probe_request"
ssh_deadline=$(( $(date +%s) + ssh_ready_timeout_seconds ))
ssh_ready=0
ssh_attempt=0
while (( $(date +%s) < ssh_deadline )); do
  ssh_attempt=$((ssh_attempt + 1))
  if api_json POST "$tb_base/ns/$namespace/cmd/infra/$infra_id?nodeId=$node_id" \
      "$ssh_probe_request" > "$ssh_probe_response" 2> "$ssh_probe_error"; then
    if jq -e \
      '[.results[]? | select((.error // "") == "") | .stdout[]?] | any(contains("KHU_SSH_READY"))' \
      "$ssh_probe_response" >/dev/null; then
      ssh_ready=1
      echo "SSH ready for node=$node_id after attempt=$ssh_attempt"
      break
    fi
  fi
  echo "waiting for SSH readiness node=$node_id attempt=$ssh_attempt"
  sleep "$ssh_ready_poll_seconds"
done
if [[ "$ssh_ready" != 1 ]]; then
  echo "SSH did not become ready within ${ssh_ready_timeout_seconds}s for node=$node_id" >&2
  if [[ -s "$ssh_probe_response" ]]; then jq . "$ssh_probe_response" >&2 || true; fi
  if [[ -s "$ssh_probe_error" ]]; then cat "$ssh_probe_error" >&2; fi
  exit 4
fi

transfer_url="$tb_base/ns/$namespace/transferFileAndCmd/infra/$infra_id?nodeId=$node_id"
transfer_response="$output_dir/transfer-response.json"
transfer_error="$output_dir/transfer-error.log"
transfer_deadline=$(( $(date +%s) + ssh_ready_timeout_seconds ))
transfer_ready=0
transfer_attempt=0
while (( $(date +%s) < transfer_deadline )); do
  transfer_attempt=$((transfer_attempt + 1))
  if curl --fail-with-body --silent --show-error "${auth_headers[@]}" \
      --form "path=/tmp" \
      --form "file=@$package;filename=khu-tb-collector.tar.gz" \
      --form 'command=true' \
      "$transfer_url" > "$transfer_response" 2> "$transfer_error"; then
    if jq -e '
        ((.fileTransferResults.results // []) | length > 0) and
        (all(.fileTransferResults.results[]?; (.error // "") == ""))
      ' "$transfer_response" >/dev/null; then
      transfer_ready=1
      echo "collector archive transferred after attempt=$transfer_attempt"
      jq . "$transfer_response"
      break
    fi
  fi
  echo "retrying collector archive transfer attempt=$transfer_attempt"
  sleep "$ssh_ready_poll_seconds"
done
if [[ "$transfer_ready" != 1 ]]; then
  echo "collector archive transfer did not succeed within ${ssh_ready_timeout_seconds}s" >&2
  if [[ -s "$transfer_response" ]]; then jq . "$transfer_response" >&2 || true; fi
  if [[ -s "$transfer_error" ]]; then cat "$transfer_error" >&2; fi
  exit 5
fi

# transferFileAndCmd opens a second SSH connection for its command immediately
# after SCP. Some DLAMI images close that connection while accepting the SCP.
# Extract with a separate idempotent command and retry only this setup step.
setup_request="$output_dir/setup-command.json"
setup_response="$output_dir/setup-response.json"
setup_error="$output_dir/setup-error.log"
jq -n \
  --arg command 'rm -rf /tmp/khu-collector && mkdir -p /tmp/khu-collector && tar -xzf /tmp/khu-tb-collector.tar.gz -C /tmp/khu-collector && test -f /tmp/khu-collector/scripts/tumblebug-vm-collect.sh && echo KHU_SETUP_READY' \
  '{command: [$command], timeoutMinutes: 5}' > "$setup_request"
setup_deadline=$(( $(date +%s) + ssh_ready_timeout_seconds ))
setup_ready=0
setup_attempt=0
while (( $(date +%s) < setup_deadline )); do
  setup_attempt=$((setup_attempt + 1))
  if api_json POST "$tb_base/ns/$namespace/cmd/infra/$infra_id?nodeId=$node_id" \
      "$setup_request" > "$setup_response" 2> "$setup_error"; then
    if jq -e \
      '[.results[]? | select((.error // "") == "") | .stdout[]?] | any(contains("KHU_SETUP_READY"))' \
      "$setup_response" >/dev/null; then
      setup_ready=1
      echo "collector setup ready after attempt=$setup_attempt"
      jq . "$setup_response"
      break
    fi
  fi
  echo "retrying collector setup attempt=$setup_attempt"
  sleep "$ssh_ready_poll_seconds"
done
if [[ "$setup_ready" != 1 ]]; then
  echo "collector setup did not succeed within ${ssh_ready_timeout_seconds}s" >&2
  if [[ -s "$setup_response" ]]; then jq . "$setup_response" >&2 || true; fi
  if [[ -s "$setup_error" ]]; then cat "$setup_error" >&2; fi
  exit 5
fi
if (( ssh_settle_seconds > 0 )); then
  echo "waiting ${ssh_settle_seconds}s before the long collection command"
  sleep "$ssh_settle_seconds"
fi

device_scope="${KHU_DEVICE_SCOPE:-cpu}"
profile_set="${KHU_PROFILE_SET:-baseline}"
max_cases="${KHU_MAX_CASES:-1}"
sample_interval="${KHU_SAMPLE_INTERVAL_SECONDS:-0.10}"
seed="${KHU_SEED:-42}"
cpu_core_counts="${KHU_CPU_CORE_COUNTS:-}"
cpu_core_samples="${KHU_CPU_CORE_SAMPLES_PER_PROFILE:-}"
remote_grid_shard="${KHU_REMOTE_GRID_SHARD:-0}"
for value in "$device_scope" "$profile_set" "$run_id" "$max_cases" "$sample_interval" "$seed" "$cpu_core_counts" "$cpu_core_samples" "$remote_grid_shard"; do
  [[ "$value" =~ ^[A-Za-z0-9.,_-]*$ ]] || { echo "unsafe collection parameter: $value" >&2; exit 2; }
done

remote_command="cd /tmp/khu-collector && chmod +x scripts/tumblebug-vm-collect.sh && env"
append_env() { printf -v remote_command '%s %q=%q' "$remote_command" "$1" "$2"; }
append_env KHU_DEVICE_SCOPE "$device_scope"
append_env KHU_PROFILE_SET "$profile_set"
append_env KHU_MAX_CASES "$max_cases"
append_env KHU_SAMPLE_INTERVAL_SECONDS "$sample_interval"
append_env KHU_SEED "$seed"
append_env KHU_RUN_ID "$run_id"
append_env KHU_CPU_CORE_COUNTS "$cpu_core_counts"
append_env KHU_CPU_CORE_SAMPLES_PER_PROFILE "$cpu_core_samples"
append_env KHU_REMOTE_GRID_SHARD "$remote_grid_shard"
append_env KHU_TB_NAMESPACE "$namespace"
append_env KHU_TB_INFRA_ID "$infra_id"
append_env KHU_TB_NODE_ID "$node_id"
append_env KHU_TB_SPEC_ID "$spec_id"
append_env KHU_TB_IMAGE_ID "$image_id"
if [[ "${KHU_APPLY_CPU_CORE_COUNTS_TO_GPU:-0}" == 1 ]]; then
  append_env KHU_APPLY_CPU_CORE_COUNTS_TO_GPU 1
fi
if [[ -n "${KHU_TORCH_INDEX_URL:-}" ]]; then
  append_env KHU_TORCH_INDEX_URL "$KHU_TORCH_INDEX_URL"
fi
remote_command+=" ./scripts/tumblebug-vm-collect.sh"

jq -n --arg command "$remote_command" '{command: [$command], timeoutMinutes: 120}' \
  > "$output_dir/collect-command.json"
collect_url="$tb_base/ns/$namespace/cmd/infra/$infra_id?nodeId=$node_id"
collect_response="$output_dir/collect-response.json"
collect_error="$output_dir/collect-error.log"
collect_completed=0
remote_failed=0
for (( collect_attempt=1; collect_attempt<=collect_retry_attempts; collect_attempt++ )); do
  attempt_response="$output_dir/collect-response-attempt-$collect_attempt.json"
  if api_json POST "$collect_url" "$output_dir/collect-command.json" \
      > "$attempt_response" 2> "$collect_error"; then
    cat "$attempt_response" > "$collect_response"
    jq . "$collect_response"
    remote_errors="$(jq -r '.results[]? | .error // empty' "$collect_response")"
    if [[ -z "$remote_errors" ]]; then
      collect_completed=1
      break
    fi
    if grep -Eqi \
      'failed to establish SSH connection|ssh: handshake failed' \
      <<<"$remote_errors"; then
      echo "retrying collection command after SSH handshake failure attempt=$collect_attempt" >&2
    else
      collect_completed=1
      remote_failed=1
      echo "remote collection failed; downloading its diagnostic archive before cleanup" >&2
      break
    fi
  else
    echo "retrying collection command after API transport failure attempt=$collect_attempt" >&2
    if [[ -s "$collect_error" ]]; then cat "$collect_error" >&2; fi
  fi
  if (( collect_attempt < collect_retry_attempts && collect_retry_delay_seconds > 0 )); then
    sleep "$collect_retry_delay_seconds"
  fi
done
if [[ "$collect_completed" != 1 ]]; then
  echo "collection command could not establish SSH after ${collect_retry_attempts} attempts" >&2
  exit 5
fi

download_file() {
  local source_path="$1" destination="$2"
  jq -n --arg path "$source_path" '{sourcePath: $path}' > "$output_dir/download-request.json"
  curl --fail-with-body --silent --show-error "${auth_headers[@]}" \
    --header "Content-Type: application/json" --request POST \
    --data-binary "@$output_dir/download-request.json" \
    --output "$destination" \
    "$tb_base/ns/$namespace/downloadFile/infra/$infra_id/node/$node_id"
}

parts_manifest="$output_dir/$run_id.parts.json"
if ! download_file "/tmp/khu-collection-output/$run_id.parts.json" "$parts_manifest"; then
  if [[ "$remote_failed" == 1 ]]; then
    echo "diagnostic archive is unavailable because the remote collector stopped before packaging" >&2
  else
    echo "collection manifest download failed" >&2
  fi
  exit 5
fi
jq -e '.parts | length > 0' "$parts_manifest" >/dev/null
while IFS=$'\t' read -r part_name source_path expected_sha; do
  destination="$output_dir/$part_name"
  download_file "$source_path" "$destination"
  actual_sha="$(sha256sum "$destination" | awk '{print $1}')"
  [[ "$actual_sha" == "$expected_sha" ]] || { echo "checksum mismatch: $part_name" >&2; exit 6; }
done < <(jq -r '.parts[] | [.name, .sourcePath, .sha256] | @tsv' "$parts_manifest")

archive="$output_dir/$run_id.tar.gz"
: > "$archive"
while IFS= read -r part_name; do
  cat "$output_dir/$part_name" >> "$archive"
done < <(jq -r '.parts[].name' "$parts_manifest")
mkdir -p "$output_dir/extracted"
tar -xzf "$archive" -C "$output_dir/extracted"
sha256sum "$archive" > "$archive.sha256"

jq -n \
  --arg namespace "$namespace" --arg infra_id "$infra_id" --arg node_id "$node_id" \
  --arg spec_id "$spec_id" --arg image_id "$image_id" --arg run_id "$run_id" \
  --arg collected_at "$(date -u +%FT%TZ)" \
  '{namespace:$namespace,infra_id:$infra_id,node_id:$node_id,spec_id:$spec_id,image_id:$image_id,run_id:$run_id,collected_at:$collected_at}' \
  > "$output_dir/tumblebug-provenance.json"

if [[ "$remote_failed" == 1 ]]; then
  echo "failure diagnostics downloaded and verified: $output_dir" >&2
  exit 5
fi

if [[ "$auto_destroy" == 1 ]]; then
  cleanup_active=1
  destroy_infra
  infra_may_exist=0
  cleanup_active=0
else
  echo "data verified; Infra retained because KHU_TB_AUTO_DESTROY=$auto_destroy"
fi
echo "collection complete: $output_dir"
