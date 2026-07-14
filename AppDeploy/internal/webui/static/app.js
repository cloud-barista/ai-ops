"use strict";

const state = {
  readiness: null,
  apps: [],
  runtimes: [],
  targets: [],
  credentials: [],
  credentialsError: "",
  deployments: [],
  inventory: [],
  summary: null,
  metrics: [],
  deploymentFilter: "ALL",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let refreshInFlight = null;

const fieldHelpDefinitions = [
  ["#app-form [name='name']", "앱을 구분하는 고유 이름입니다. 소문자·숫자·하이픈만 사용하며 2~63자로 입력하세요. 예: image-classifier"],
  ["#app-form [name='version']", "같은 앱의 배포 버전입니다. 동일한 이름과 버전 조합은 중복 등록할 수 없습니다. 예: 0.1.0"],
  ["#app-form [name='description']", "앱의 목적이나 모델 정보를 적는 선택 항목입니다. 배포 동작에는 영향을 주지 않습니다."],
  ["#app-form [name='artifact_type']", "배포 파일의 전달 방식입니다. script, package, git, binary만 지원하며 컨테이너 이미지는 지원하지 않습니다."],
  ["#app-form [name='runtime_type']", "앱이 요구하는 실행 환경입니다. mock은 기능 시험, cpu/gpu는 VM 실행, aiinfra는 외부 AI Infra 연동용입니다."],
  ["#app-form [name='artifact_uri']", "배포할 파일 또는 저장소 위치입니다. 로컬 스크립트 업로드는 file:///절대/경로/run.sh 형식을 사용합니다."],
  ["#app-form [name='command']", "대상 환경에서 앱을 시작할 실행 명령입니다. 예: bash, python3, ./server"],
  ["#app-form [name='args']", "실행 명령 뒤에 전달할 인자입니다. 공백으로 구분하고 공백이 포함된 값은 따옴표로 묶습니다."],
  ["#app-form [name='cpu']", "앱이 요구하는 논리 CPU 수입니다. 현재는 배포 요구사항과 증적 정보로 저장됩니다. 예: 2"],
  ["#app-form [name='memory']", "앱이 요구하는 메모리입니다. Ki, Mi, Gi 단위를 사용할 수 있습니다. 예: 4Gi"],
  ["#app-form [name='gpu']", "필요한 GPU 개수입니다. Runtime이 gpu이면 반드시 1 이상이어야 합니다."],
  ["#app-form [name='storage']", "Artifact와 모델 실행에 필요한 저장 공간입니다. 예: 10Gi"],
  ["#app-form [name='port']", "앱이 HTTP 요청을 받는 포트입니다. 입력하면 추론 프록시의 기본 포트로 사용됩니다. 예: 18080"],
  ["#app-form [name='healthcheck_path']", "앱의 HTTP 상태 확인 경로입니다. 반드시 /로 시작해야 합니다. 예: /health, /healthz"],
  ["#package-type", "서버 프리셋 또는 업로드 소스의 실행 환경을 선택합니다. 임의 build 명령은 실행하지 않습니다."],
  ["#package-source", "Go는 ZIP이 필수입니다. Python, Node.js, Linux binary, Shell script는 ZIP 또는 단일 파일을 사용할 수 있습니다."],
  ["#package-entrypoint", "Go는 go.mod 기준 package 경로, 그 외 유형은 ZIP 또는 업로드 파일 안의 시작 파일 경로입니다."],

  ["#runtime-form [name='runtime_type']", "이 Profile이 담당할 실행 환경입니다. 배포할 App의 Runtime과 호환되어야 합니다."],
  ["#runtime-form [name='runtime_profile_id']", "배포 요청에서 참조하는 Runtime Profile 식별자입니다. 예: rt-cpu-001"],
  ["#runtime-form [name='name']", "화면에서 Profile을 쉽게 찾기 위한 표시 이름입니다."],
  ["#runtime-form [name='adapter_type']", "실제 실행 담당 모듈입니다. mock, cpu_vm, gpu_vm, etri_aiinfra 중 Runtime과 맞는 값을 선택하세요."],
  ["#runtime-form [name='operating_mode']", "local_mock은 로컬 시험, dry_run은 실행 모의, vm_process는 VM 프로세스 실행, remote_api는 외부 API 호출을 뜻합니다."],

  ["#credential-form [name='credential_id']", "현재 서버 프로세스 안에서 Credential을 구분하는 ID입니다. 등록 후 cred://runtime/{ID} 참조가 발급됩니다."],
  ["#credential-form [name='ssh_user']", "대상 VM에 SSH로 로그인할 사용자 이름입니다. 예: ubuntu"],
  ["#credential-auth-type", "PEM private key 파일 또는 password 중 한 가지 인증 방식만 선택합니다."],
  ["#credential-form [name='host_key_fingerprint']", "신뢰된 별도 경로에서 확인한 SSH 서버 host key의 SHA256 fingerprint입니다. 서버가 제시한 키와 다르면 연결을 거부합니다."],
  ["#credential-form [name='ssh_timeout_seconds']", "SSH 연결을 기다릴 시간입니다. 1~300초 사이로 입력하세요."],
  ["#credential-private-key-file", "PEM private key 원문을 읽어 등록하며 파일 경로 자체는 서버로 보내지 않습니다."],
  ["#credential-private-key-passphrase", "암호화된 private key를 사용할 때만 입력합니다. 화면과 응답에 다시 표시되지 않습니다."],
  ["#credential-password", "SSH password는 등록 요청에만 사용되며 화면과 응답에 다시 표시되지 않습니다."],

  ["#target-form [name='runtime_type']", "대상 시스템이 제공하는 Runtime입니다. App과 Runtime Profile의 종류와 호환되어야 합니다."],
  ["#target-form [name='csp']", "대상을 제공하는 환경입니다. 로컬/일반 VM은 local, 기능 시험은 mock, ETRI 연동은 etri를 선택합니다."],
  ["#target-form [name='target_profile_id']", "배포 대상을 참조할 고유 식별자입니다. 예: target-gpu-001"],
  ["#target-form [name='name']", "화면에서 배포 대상을 구분하기 위한 표시 이름입니다."],
  ["#target-form [name='host']", "대상 VM의 hostname 또는 IP입니다. mock을 제외한 대상에서는 필수입니다."],
  ["#target-form [name='ssh_port']", "CPU/GPU VM에 접속할 SSH 포트입니다. 일반적으로 22를 사용합니다."],
  ["#target-form [name='credential_ref']", "비밀번호나 키 자체가 아닌 자격증명 참조값입니다. 위 SSH Credentials에서 발급된 cred://runtime/... 값을 선택하거나 기존 ENV 참조를 직접 입력하세요."],
  ["#target-form [name='artifact_dir']", "업로드된 App Artifact가 저장될 대상 VM 내부 디렉터리입니다. 예: /tmp/aiapp/artifacts"],
  ["#target-form [name='log_dir']", "실행 로그를 저장할 대상 VM 내부 디렉터리입니다. 예: /tmp/aiapp/logs"],

  ["#deployment-form [name='app_version_id']", "등록된 App의 특정 버전을 선택합니다. App 등록 시 app_version_id가 자동 발급됩니다."],
  ["#deployment-form [name='runtime_profile_id']", "App Runtime과 같은 종류의 실행 Profile을 선택합니다."],
  ["#deployment-form [name='target_profile_id']", "App을 실행할 VM 또는 AI Infra 대상을 선택합니다. Runtime 종류가 서로 호환되어야 합니다."],

  ["#resource-target", "준비 상태를 검사할 배포 대상입니다. 연결성, Runtime, GPU와 저장 경로를 Adapter 기준으로 확인합니다."],
  ["#resource-runtime", "결과에 함께 표시할 Runtime Profile입니다. 실제 준비 상태 검사는 Target Profile을 기준으로 수행합니다."],

  ["#metric-form [name='deployment_id']", "메트릭을 연결할 배포를 선택합니다."],
  ["#metric-form [name='latency_ms']", "요청부터 응답까지 걸린 시간입니다. 밀리초(ms) 단위로 입력합니다."],
  ["#metric-form [name='throughput_rps']", "초당 처리한 요청 수(Requests Per Second)입니다."],
  ["#metric-form [name='quality_score']", "정확도 등 품질을 0에서 1 사이 값으로 정규화한 선택 지표입니다."],
  ["#metric-form [name='request_count']", "해당 측정 구간에서 처리한 전체 요청 수입니다."],
  ["#metric-form [name='error_count']", "해당 측정 구간에서 실패한 요청 수입니다."],

  ["#inference-deployment", "HTTP 서비스가 실행 중인 RUNNING 배포를 선택합니다."],
  ["#inference-method", "배포된 앱에 전달할 HTTP 방식입니다. 조회는 GET, JSON 요청은 POST를 사용합니다."],
  ["#inference-path", "배포된 앱의 상대 경로입니다. 반드시 /로 시작해야 합니다. 예: /generate, /predict"],
  ["#inference-port", "App Spec 포트 대신 사용할 포트입니다. 비워두면 App 등록 시 설정한 첫 번째 서비스 포트를 사용합니다."],
  ["#inference-timeout", "응답을 기다릴 최대 시간입니다. 1~300초 사이로 입력하세요."],
  ["#inference-body", "앱에 전달할 JSON 본문입니다. GET이거나 본문이 필요 없으면 비워둘 수 있습니다."],
];

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function installFieldHelp() {
  fieldHelpDefinitions.forEach(([selector, description], index) => {
    const control = $(selector);
    const label = control?.closest("label");
    if (!control || !label || label.querySelector(".field-help")) return;
    const help = element("small", "field-help", description);
    help.id = `field-help-${index + 1}`;
    control.setAttribute("aria-describedby", help.id);
    label.append(help);
  });
}

function cell(text, className) {
  return element("td", className, text ?? "—");
}

function appendEmptyRow(body, columns, message) {
  const row = element("tr", "empty-row");
  const item = cell(message);
  item.colSpan = columns;
  row.append(item);
  body.append(row);
}

function shortID(value) {
  if (!value) return "—";
  return value.length > 24 ? `${value.slice(0, 16)}…${value.slice(-6)}` : value;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function formatNumber(value, suffix = "") {
  if (value === undefined || value === null) return "—";
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(value)}${suffix}`;
}

function humanize(value) {
  return String(value || "unknown").toLowerCase().replaceAll("_", " ");
}

function isValidHostKeyFingerprint(value) {
  if (!/^SHA256:[A-Za-z0-9+/]{43}$/.test(value)) return false;
  try {
    const encoded = value.slice("SHA256:".length);
    const decoded = atob(`${encoded}=`);
    return decoded.length === 32 && btoa(decoded).replace(/=+$/, "") === encoded;
  } catch {
    return false;
  }
}

function statusClass(status) {
  const value = String(status || "").toUpperCase();
  if (["RUNNING", "AVAILABLE", "READY", "OK"].includes(value)) return "status-running";
  if (value.includes("FAILED") || ["UNAVAILABLE", "ERROR", "CRITICAL"].includes(value)) return "status-failed";
  if (value === "STOPPED") return "status-stopped";
  return "status-pending";
}

function statusBadge(status) {
  return element("span", `status-badge ${statusClass(status)}`, humanize(status));
}

async function api(path, options = {}) {
  const config = { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
  if (config.body !== undefined && typeof config.body !== "string" && !(config.body instanceof FormData)) {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  let data = null;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const error = data?.error;
    const message = error ? `[${error.code}] ${error.message}` : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return data;
}

function toast(title, message = "", type = "success") {
  const item = element("div", `toast ${type}`);
  item.append(element("div", "toast-icon", type === "error" ? "!" : "✓"));
  const copy = element("div");
  copy.append(element("strong", "", title));
  if (message) copy.append(element("span", "", message));
  item.append(copy);
  $("#toast-region").append(item);
  window.setTimeout(() => item.remove(), 4500);
}

function setServerStatus(online) {
  const dot = $("#sidebar-status-dot");
  dot.classList.toggle("online", online);
  dot.classList.toggle("offline", !online);
  $("#sidebar-status").textContent = online ? "API 연결됨" : "API 연결 실패";
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = label || "처리 중…";
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function yieldForPaint() {
  return new Promise(resolve => window.requestAnimationFrame(resolve));
}

async function loadCredentials() {
  try {
    const response = await api("/api/v1/credentials");
    return { credentials: (response.items || []).map(safeCredentialRecord), credentialsError: "" };
  } catch (error) {
    return { credentials: [], credentialsError: error.message };
  }
}

async function refreshCredentials() {
  Object.assign(state, await loadCredentials());
  renderCredentials();
  populateCredentialRefs();
}

async function runFullRefresh(showToast) {
  const button = $("#refresh-button");
  setBusy(button, true, "동기화 중…");
  try {
    const [readiness, apps, runtimes, targets, deployments, inventory, summary, metrics, credentials] = await Promise.all([
      api("/api/v1/readiness"),
      api("/api/v1/apps"),
      api("/api/v1/runtime-profiles"),
      api("/api/v1/target-profiles"),
      api("/api/v1/deployments"),
      api("/api/v1/resources/inventory"),
      api("/api/v1/monitoring/summary"),
      api("/api/v1/monitoring/metrics"),
      loadCredentials(),
    ]);
    Object.assign(state, {
      readiness,
      apps: apps.items || [],
      runtimes: runtimes.items || [],
      targets: targets.items || [],
      deployments: deployments.items || [],
      inventory: inventory.items || [],
      summary,
      metrics: metrics.items || [],
      ...credentials,
    });
    renderAll();
    setServerStatus(true);
    $("#last-updated").textContent = `${new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date())} 동기화`;
    if (showToast) toast("동기화 완료", "최신 서버 상태를 반영했습니다.");
  } catch (error) {
    setServerStatus(false);
    toast("서버 연결 실패", error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function refreshAll(showToast = false) {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = runFullRefresh(showToast).finally(() => { refreshInFlight = null; });
  return refreshInFlight;
}

function renderAll() {
  renderDashboard();
  renderApps();
  renderProfiles();
  renderCredentials();
  renderInventory();
  renderDeployments();
  renderMonitoring();
  populateSelects();
}

function renderDashboard() {
  $("#metric-apps").textContent = state.apps.length;
  $("#metric-targets").textContent = state.targets.length;
  $("#metric-running").textContent = state.deployments.filter(item => item.status === "RUNNING").length;
  $("#metric-alarms").textContent = state.summary?.alarms?.reduce((total, item) => total + (item.count || 0), 0) || 0;

  const body = $("#dashboard-deployments");
  body.replaceChildren();
  const recent = [...state.deployments].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)).slice(0, 5);
  if (!recent.length) appendEmptyRow(body, 5, "아직 생성된 배포가 없습니다.");
  recent.forEach(item => {
    const row = element("tr");
    const idCell = element("td", "primary-cell");
    idCell.append(element("strong", "", shortID(item.deployment_id)), element("span", "", item.deployment_id));
    row.append(idCell, cell(shortID(item.app_version_id)), cell(item.target_profile_id));
    const statusCell = cell(""); statusCell.append(statusBadge(item.status)); row.append(statusCell, cell(formatDate(item.updated_at)));
    body.append(row);
  });

  const checks = $("#readiness-checks");
  checks.replaceChildren();
  Object.entries(state.readiness?.checks || {}).forEach(([name, value]) => {
    const row = element("div", "check-item");
    const info = element("div");
    info.append(element("span", "check-icon", "✓"));
    const copy = element("div"); copy.append(element("strong", "", name.replaceAll("_", " ")), element("span", "", value));
    info.append(copy); row.append(info, statusBadge("ready")); checks.append(row);
  });
  if (!checks.children.length) checks.append(element("p", "muted", "준비 상태를 불러오지 못했습니다."));
}

function renderApps() {
  const body = $("#apps-table-body");
  body.replaceChildren();
  $("#apps-count").textContent = `${state.apps.length} apps`;
  if (!state.apps.length) appendEmptyRow(body, 6, "등록된 애플리케이션이 없습니다. 오른쪽 위 버튼으로 첫 앱을 등록하세요.");
  state.apps.forEach(item => {
    const row = element("tr");
    row.dataset.search = `${item.name} ${item.app_id} ${item.app_version_id}`.toLowerCase();
    const appCell = element("td", "primary-cell");
    appCell.append(element("strong", "", item.name), element("span", "", item.app_id));
    row.append(appCell, cell(item.version));
    const runtime = cell(""); runtime.append(element("span", "badge", item.app_spec?.runtime?.type || "—")); row.append(runtime);
    row.append(cell(item.app_spec?.artifact?.type), cell(formatDate(item.created_at)));
    const actions = element("td", "table-actions");
    const detail = element("button", "row-button", "상세"); detail.type = "button"; detail.dataset.action = "app-detail"; detail.dataset.id = item.app_id;
    const remove = element("button", "row-button danger", "등록 삭제");
    remove.type = "button";
    remove.dataset.action = "app-delete";
    remove.dataset.id = item.app_id;
    remove.dataset.name = item.name;
    remove.dataset.version = item.version;
    actions.append(detail, remove); row.append(actions); body.append(row);
  });
}

function profileDeleteButton(action, id, name) {
  const remove = element("button", "row-button danger", "삭제");
  remove.type = "button";
  remove.dataset.action = action;
  remove.dataset.id = id;
  remove.dataset.name = name || id;
  return remove;
}

function renderProfiles() {
  const runtimes = $("#runtime-card-list");
  runtimes.replaceChildren();
  state.runtimes.forEach(item => {
    const card = element("div", "profile-card");
    card.append(element("div", "profile-icon", item.runtime_type));
    const copy = element("div"); copy.append(element("strong", "", item.name || item.runtime_profile_id), element("span", "", item.runtime_profile_id));
    const meta = element("div", "profile-meta"); meta.append(element("strong", "", item.adapter_type), element("span", "", item.operating_mode));
    card.append(copy, meta, profileDeleteButton("runtime-delete", item.runtime_profile_id, item.name)); runtimes.append(card);
  });
  if (!state.runtimes.length) runtimes.append(element("p", "muted", "등록된 Runtime Profile이 없습니다."));

  const targets = $("#target-card-list");
  targets.replaceChildren();
  state.targets.forEach(item => {
    const card = element("div", "profile-card");
    card.append(element("div", "profile-icon", item.runtime?.runtime_type || "—"));
    const copy = element("div"); copy.append(element("strong", "", item.name || item.target_profile_id), element("span", "", item.target_profile_id));
    const meta = element("div", "profile-meta"); meta.append(element("strong", "", item.csp), element("span", "", item.vm?.host || "local mock"));
    card.append(copy, meta, profileDeleteButton("target-delete", item.target_profile_id, item.name)); targets.append(card);
  });
  if (!state.targets.length) targets.append(element("p", "muted", "등록된 Target Profile이 없습니다."));
}

function safeCredentialRecord(item = {}) {
  return {
    credential_id: typeof item.credential_id === "string" ? item.credential_id : "",
    credential_ref: typeof item.credential_ref === "string" ? item.credential_ref : "",
    credential_type: typeof item.credential_type === "string" ? item.credential_type : "",
    auth_type: typeof item.auth_type === "string" ? item.auth_type : "",
    ssh_user: typeof item.ssh_user === "string" ? item.ssh_user : "",
    host_key_fingerprint: typeof item.host_key_fingerprint === "string" ? item.host_key_fingerprint : "",
    ssh_timeout_seconds: Number.isFinite(Number(item.ssh_timeout_seconds)) ? Number(item.ssh_timeout_seconds) : null,
    persistent: item.persistent === true,
    created_at: typeof item.created_at === "string" ? item.created_at : "",
  };
}

function renderCredentials() {
  const body = $("#credentials-table-body");
  const status = $("#credential-list-status");
  if (!body || !status) return;
  body.replaceChildren();
  status.hidden = !state.credentialsError;
  status.textContent = state.credentialsError ? `Credential 목록만 불러오지 못했습니다: ${state.credentialsError}` : "";
  if (state.credentialsError) {
    appendEmptyRow(body, 9, "Credential API 접근 권한 또는 서버 설정을 확인하세요.");
    return;
  }
  if (!state.credentials.length) appendEmptyRow(body, 9, "현재 프로세스에 등록된 SSH Credential이 없습니다.");
  state.credentials.forEach(item => {
    const row = element("tr");
    const identity = element("td", "primary-cell");
    identity.append(element("strong", "", item.credential_id || "—"), element("span", "", item.credential_ref || "—"));
    const type = cell(""); type.append(element("span", "badge", item.credential_type || "—"));
    const auth = cell(""); auth.append(element("span", "badge", item.auth_type || "—"));
    const fingerprint = cell(item.host_key_fingerprint || "—"); fingerprint.classList.add("fingerprint-cell");
    row.append(identity, type, auth, cell(item.ssh_user || "—"), fingerprint);
    row.append(cell(item.ssh_timeout_seconds === null ? "—" : `${item.ssh_timeout_seconds} s`));
    row.append(cell(item.persistent ? "yes" : "no"), cell(formatDate(item.created_at)));
    const actions = element("td", "table-actions");
    const remove = element("button", "row-button danger", "삭제");
    remove.type = "button";
    remove.dataset.action = "credential-delete";
    remove.dataset.id = item.credential_id;
    remove.dataset.ref = item.credential_ref;
    actions.append(remove);
    row.append(actions);
    body.append(row);
  });
}

function availability(value) {
  const badge = statusBadge(value ? "available" : "unavailable");
  badge.textContent = value ? "yes" : "no";
  return badge;
}

function renderInventory() {
  const body = $("#inventory-table-body");
  body.replaceChildren();
  if (!state.inventory.length) appendEmptyRow(body, 7, "자원 점검 기록이 없습니다.");
  state.inventory.forEach(item => {
    const row = element("tr");
    row.append(cell(item.target_profile_id));
    const runtime = cell(""); runtime.append(statusBadge(item.runtime_health)); row.append(runtime);
    for (const key of ["cpu_available", "memory_available", "gpu_available", "storage_available"]) {
      const value = cell(""); value.append(availability(item[key])); row.append(value);
    }
    row.append(cell(formatDate(item.last_checked_at))); body.append(row);
  });
}

function deploymentMatches(item) {
  if (state.deploymentFilter === "ALL") return true;
  if (state.deploymentFilter === "FAILED") return String(item.status).includes("FAILED");
  return item.status === state.deploymentFilter;
}

function renderDeployments() {
  const items = [...state.deployments].filter(deploymentMatches).sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  const body = $("#deployments-table-body");
  body.replaceChildren();
  $("#deployments-count").textContent = `${items.length} deployments`;
  if (!items.length) appendEmptyRow(body, 6, "조건에 맞는 배포가 없습니다.");
  items.forEach(item => {
    const row = element("tr");
    const idCell = element("td", "primary-cell"); idCell.append(element("strong", "", shortID(item.deployment_id)), element("span", "", item.deployment_id));
    row.append(idCell, cell(shortID(item.app_version_id)));
    const profileCell = element("td", "primary-cell"); profileCell.append(element("strong", "", item.runtime_profile_id), element("span", "", item.target_profile_id)); row.append(profileCell);
    const statusCell = cell(""); statusCell.append(statusBadge(item.status)); row.append(statusCell, cell(formatDate(item.updated_at)));
    const actions = element("td", "table-actions");
    const logs = element("button", "row-button", "로그"); logs.type = "button"; logs.dataset.action = "deployment-logs"; logs.dataset.id = item.deployment_id;
    const detail = element("button", "row-button", "상세"); detail.type = "button"; detail.dataset.action = "deployment-detail"; detail.dataset.id = item.deployment_id;
    actions.append(logs, detail);
    if (!["STOPPED", "STOPPING"].includes(item.status)) {
      const stop = element("button", "row-button danger", "중지"); stop.type = "button"; stop.dataset.action = "deployment-stop"; stop.dataset.id = item.deployment_id; actions.append(stop);
    }
    row.append(actions); body.append(row);
  });
}

function renderMonitoring() {
  const deployments = state.summary?.deployments || {};
  $("#monitor-total").textContent = deployments.total ?? 0;
  $("#monitor-active").textContent = deployments.active ?? 0;
  $("#monitor-failed").textContent = deployments.failed ?? 0;
  $("#monitor-stopped").textContent = deployments.stopped ?? 0;

  const health = $("#runtime-health-list");
  health.replaceChildren();
  (state.summary?.runtime_health || []).forEach(item => {
    const row = element("div", "health-row");
    const copy = element("div"); copy.append(element("strong", "", item.target_profile_id), element("span", "", `runtime ${item.runtime_health} · ${formatDate(item.last_checked_at)}`));
    row.append(copy, statusBadge(item.status)); health.append(row);
  });
  if (!health.children.length) health.append(element("p", "muted", "Runtime health 데이터가 없습니다."));

  const alarms = $("#alarm-list");
  alarms.replaceChildren();
  (state.summary?.alarms || []).forEach(item => {
    const row = element("div", "alarm-row");
    const copy = element("div"); copy.append(element("strong", "", item.error_code), element("span", "", item.latest_message || item.latest_deployment_id));
    row.append(copy, element("div", "alarm-count", item.count)); alarms.append(row);
  });
  if (!alarms.children.length) alarms.append(element("p", "muted", "현재 활성 알람이 없습니다."));

  const metrics = $("#metrics-table-body");
  metrics.replaceChildren();
  const items = [...state.metrics].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  if (!items.length) appendEmptyRow(metrics, 6, "기록된 추론 메트릭이 없습니다.");
  items.forEach(item => {
    const row = element("tr");
    row.append(cell(shortID(item.deployment_id)), cell(formatNumber(item.latency_ms, " ms")), cell(formatNumber(item.throughput_rps, " rps")), cell(formatNumber(item.quality_score)), cell(`${item.request_count || 0} / ${item.error_count || 0}`), cell(formatDate(item.timestamp)));
    metrics.append(row);
  });
}

function fillSelect(selector, items, valueKey, label) {
  const select = $(selector);
  const current = select.value;
  select.replaceChildren();
  if (!items.length) {
    const option = element("option", "", "등록된 항목 없음"); option.value = ""; select.append(option); return;
  }
  items.forEach(item => {
    const option = element("option", "", label(item)); option.value = item[valueKey]; select.append(option);
  });
  if ([...select.options].some(option => option.value === current)) select.value = current;
}

function populateCredentialRefs() {
  const list = $("#credential-ref-options");
  if (!list) return;
  list.replaceChildren();
  const refs = new Set();
  state.credentials.forEach(item => {
    if (!item.credential_ref || refs.has(item.credential_ref)) return;
    refs.add(item.credential_ref);
    const option = element("option");
    option.value = item.credential_ref;
    option.label = `${item.ssh_user || "SSH"} · ${item.auth_type || "credential"}`;
    list.append(option);
  });
}

function populateSelects() {
  fillSelect("#deployment-app", state.apps, "app_version_id", item => `${item.name} ${item.version} · ${shortID(item.app_version_id)}`);
  fillSelect("#deployment-runtime", state.runtimes, "runtime_profile_id", item => `${item.name || item.runtime_profile_id} · ${item.runtime_type}`);
  fillSelect("#deployment-target", state.targets, "target_profile_id", item => `${item.name || item.target_profile_id} · ${item.runtime?.runtime_type}`);
  fillSelect("#resource-target", state.targets, "target_profile_id", item => `${item.name || item.target_profile_id} · ${item.target_profile_id}`);
  const runtimeSelect = $("#resource-runtime");
  const current = runtimeSelect.value;
  runtimeSelect.replaceChildren();
  const empty = element("option", "", "지정하지 않음"); empty.value = ""; runtimeSelect.append(empty);
  state.runtimes.forEach(item => { const option = element("option", "", `${item.name || item.runtime_profile_id} · ${item.runtime_type}`); option.value = item.runtime_profile_id; runtimeSelect.append(option); });
  runtimeSelect.value = [...runtimeSelect.options].some(option => option.value === current) ? current : "";
  fillSelect("#inference-deployment", state.deployments.filter(item => item.status === "RUNNING"), "deployment_id", item => `${shortID(item.deployment_id)} · ${item.target_profile_id}`);
  fillSelect("#metric-deployment", state.deployments, "deployment_id", item => `${shortID(item.deployment_id)} · ${item.status}`);
  populateCredentialRefs();
}

function navigate(viewName) {
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${viewName}`));
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === viewName));
  const view = $(`#view-${viewName}`);
  $("#page-title").textContent = view?.dataset.title || "AI App Deployer";
  closeMobileMenu();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openDialog(id) {
  if (id === "deployment-dialog" && (!state.apps.length || !state.runtimes.length || !state.targets.length)) {
    toast("배포 준비 필요", "App, Runtime Profile, Target Profile을 먼저 등록하세요.", "error");
    return;
  }
  const dialog = document.getElementById(id);
  if (id === "credential-dialog") {
    clearCredentialSecrets();
    updateCredentialAuthFields();
  }
  if (dialog) dialog.showModal();
}

function closeDialog(dialog) {
  if (dialog?.id === "credential-dialog") clearCredentialSecrets();
  if (dialog?.open) dialog.close();
}

function showDetail(title, data, kicker = "DETAIL") {
  $("#detail-title").textContent = title;
  $("#detail-kicker").textContent = kicker;
  $("#detail-content").textContent = JSON.stringify(data, null, 2);
  openDialog("detail-dialog");
}

function parseArguments(value) {
  const output = [];
  const pattern = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match;
  while ((match = pattern.exec(value || "")) !== null) output.push(match[1] ?? match[2] ?? match[3]);
  return output;
}

function formatArgument(value) {
  if (/^[A-Za-z0-9_./:=+-]+$/.test(value)) return value;
  if (!value.includes("'")) return `'${value}'`;
  return `"${value.replaceAll('"', '\\"')}"`;
}

function populateAppForm(spec) {
  const form = $("#app-form");
  const fields = {
    name: spec.metadata?.name,
    version: spec.metadata?.version,
    description: spec.metadata?.description,
    artifact_type: spec.artifact?.type,
    artifact_uri: spec.artifact?.uri,
    checksum: spec.artifact?.checksum,
    runtime_type: spec.runtime?.type,
    command: spec.entrypoint?.command,
    args: (spec.entrypoint?.args || []).map(formatArgument).join(" "),
    cpu: spec.resources?.cpu,
    memory: spec.resources?.memory,
    gpu: spec.resources?.gpu,
    storage: spec.resources?.storage,
    port: spec.network?.ports?.[0]?.app_port,
    healthcheck_path: spec.healthcheck?.path,
  };
  Object.entries(fields).forEach(([name, value]) => {
    if (value !== undefined && value !== null && form.elements[name]) form.elements[name].value = value;
  });
}

const packageTypeDefinitions = {
  "aiops-geon-service-control": { accept: "", entrypoint: "", label: "빌드/시작 경로", message: "ai-ops-geon 프리셋은 서버에 설정된 소스를 사용하므로 파일 업로드가 필요 없습니다." },
  go: { accept: ".zip,application/zip", entrypoint: ".", label: "Go build 경로", message: "go.mod가 ZIP 최상위에 있어야 합니다. 예: . 또는 ./cmd/server" },
  python: { accept: ".zip,.py,application/zip,text/x-python", entrypoint: "main.py", label: "Python 시작 파일", message: "의존성은 대상 VM에 준비되어 있어야 하며 시작 파일은 .py 형식이어야 합니다." },
  node: { accept: ".zip,.js,.mjs,.cjs,application/zip,text/javascript", entrypoint: "index.js", label: "Node.js 시작 파일", message: "필요한 node_modules가 있다면 ZIP에 포함하고 시작 파일을 지정하세요." },
  binary: { accept: ".zip,application/zip,application/octet-stream", entrypoint: "", label: "바이너리 시작 파일", message: "Linux amd64 ELF 실행 파일 또는 해당 파일을 포함한 ZIP만 허용합니다." },
  script: { accept: ".zip,.sh,.bash,application/zip,text/x-shellscript", entrypoint: "run.sh", label: "Shell 시작 파일", message: "시작 파일은 .sh 또는 .bash 형식이며 대상 VM에서 bash로 실행됩니다." },
};

function updatePackageBuilder() {
  const packageType = $("#package-type").value;
  const definition = packageTypeDefinitions[packageType];
  const usesUpload = packageType !== "aiops-geon-service-control";
  const sourceField = $("#package-source-field");
  const entrypointField = $("#package-entrypoint-field");
  const source = $("#package-source");
  const entrypoint = $("#package-entrypoint");
  sourceField.hidden = !usesUpload;
  entrypointField.hidden = !usesUpload;
  source.required = usesUpload;
  entrypoint.required = usesUpload;
  if (!usesUpload) source.setCustomValidity("");
  source.accept = definition.accept;
  $("#package-entrypoint-label").textContent = definition.label;
  if (!entrypoint.value || entrypoint.value === entrypoint.dataset.autoValue) entrypoint.value = definition.entrypoint;
  entrypoint.dataset.autoValue = definition.entrypoint;
  $("#package-build-result").textContent = definition.message;
}

function packageSourceChanged() {
  const sourceInput = $("#package-source");
  const file = sourceInput.files[0];
  if (!file) {
    sourceInput.setCustomValidity("");
    return;
  }
  const form = $("#app-form");
  const isZip = file.name.toLowerCase().endsWith(".zip");
  sourceInput.setCustomValidity(!isZip && !/^[A-Za-z0-9._-]{1,128}$/.test(file.name) ? "단일 파일 이름은 영문, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다." : "");
  if (!isZip && $("#package-type").value !== "go") {
    $("#package-entrypoint").value = file.name;
  }
  if (!form.elements.name.value) {
    let name = file.name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    if (name.length < 2) name = `app-${name || "upload"}`;
    form.elements.name.value = name.slice(0, 63).replace(/-+$/, "");
  }
}

async function buildPackage() {
  const form = $("#app-form");
  const button = $("#package-build-button");
  const status = $("#package-build-result");
  const packageType = $("#package-type").value;
  let request;
  if (packageType === "aiops-geon-service-control") {
    request = { preset: packageType, service_port: Number(form.elements.port.value) || 18089 };
    const currentVersion = form.elements.version.value.trim();
    if (currentVersion && currentVersion !== "0.1.0") request.app_version = currentVersion;
  } else {
    const source = $("#package-source").files[0];
    if (!source) {
      $("#package-source").reportValidity();
      return;
    }
    if (!$("#package-source").reportValidity()) return;
    if (!form.elements.name.reportValidity() || !form.elements.version.reportValidity() || !$("#package-entrypoint").reportValidity()) return;
    request = new FormData();
    request.append("package_type", packageType);
    request.append("source", source);
    request.append("app_name", form.elements.name.value.trim());
    request.append("app_version", form.elements.version.value.trim());
    request.append("entrypoint", $("#package-entrypoint").value.trim());
    request.append("runtime_type", form.elements.runtime_type.value);
    const servicePort = Number(form.elements.port.value);
    if (servicePort) {
      request.append("service_port", String(servicePort));
      request.append("healthcheck_path", form.elements.healthcheck_path.value.trim() || "/health");
    }
  }
  setBusy(button, true, "패키지 생성 중…");
  status.textContent = packageType === "go" || packageType === "aiops-geon-service-control"
    ? "Go Linux/amd64 빌드 중입니다. 첫 빌드는 의존성 확인 때문에 오래 걸릴 수 있습니다."
    : "업로드 소스를 검사하고 package archive를 생성하고 있습니다.";
  try {
    await yieldForPaint();
    const result = await api("/api/v1/artifacts/packages", { method: "POST", body: request });
    populateAppForm(result.app_spec);
    status.textContent = `${result.archive_name} · ${Math.ceil(result.size_bytes / 1024 / 1024)} MiB · ${result.elapsed_ms} ms`;
    toast("패키지 생성 완료", "App 등록값을 자동 입력했습니다.");
  } catch (error) {
    status.textContent = error.message;
    toast("패키지 생성 실패", error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

async function submitForm(event, action, successTitle) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const button = formElement.querySelector('[type="submit"]');
  setBusy(button, true);
  try {
    await yieldForPaint();
    const formData = new FormData(formElement);
    const result = await action(formData);
    closeDialog(formElement.closest("dialog"));
    formElement.reset();
    if (formElement.id === "app-form") {
      $("#package-build-form").reset();
      updatePackageBuilder();
    }
    if (formElement.id === "target-form") updateTargetFormForType();
    toast(successTitle, result?.deployment_id || result?.app_version_id || result?.profile_id || "서버에 반영되었습니다.");
    void refreshAll();
  } catch (error) {
    toast("요청 실패", error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function clearCredentialSecrets() {
  const privateKey = $("#credential-private-key-file");
  const passphrase = $("#credential-private-key-passphrase");
  const password = $("#credential-password");
  if (privateKey) privateKey.value = "";
  if (passphrase) passphrase.value = "";
  if (password) password.value = "";
}

function clearCredentialPayload(payload) {
  if (!payload) return;
  payload.private_key = "";
  payload.private_key_passphrase = "";
  payload.password = "";
}

function updateCredentialAuthFields() {
  const authType = $("#credential-auth-type")?.value || "private_key";
  const usesPrivateKey = authType === "private_key";
  const privateKeyField = $("#credential-private-key-field");
  const passphraseField = $("#credential-passphrase-field");
  const passwordField = $("#credential-password-field");
  const privateKey = $("#credential-private-key-file");
  const password = $("#credential-password");
  clearCredentialSecrets();
  if (privateKeyField) privateKeyField.hidden = !usesPrivateKey;
  if (passphraseField) passphraseField.hidden = !usesPrivateKey;
  if (passwordField) passwordField.hidden = usesPrivateKey;
  if (privateKey) privateKey.required = usesPrivateKey;
  if (password) password.required = !usesPrivateKey;
}

async function submitCredential(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const button = formElement.querySelector('[type="submit"]');
  let payload = null;
  setBusy(button, true, "등록 중…");
  try {
    await yieldForPaint();
    const form = new FormData(formElement);
    const authType = form.get("auth_type");
    const hostKeyFingerprint = String(form.get("host_key_fingerprint") || "").trim();
    if (!isValidHostKeyFingerprint(hostKeyFingerprint)) {
      throw new Error("Host key fingerprint를 SHA256:base64 형식으로 입력하세요.");
    }
    payload = {
      credential_id: form.get("credential_id"),
      credential_type: "ssh",
      auth_type: authType,
      ssh_user: form.get("ssh_user"),
      host_key_fingerprint: hostKeyFingerprint,
      ssh_timeout_seconds: Number(form.get("ssh_timeout_seconds")) || 30,
    };
    if (authType === "private_key") {
      const file = $("#credential-private-key-file").files[0];
      if (!file) throw new Error("PEM private key 파일을 선택하세요.");
      if (file.size > 65536) throw new Error("PEM private key 파일은 65536 bytes 이하여야 합니다.");
      payload.private_key = await file.text();
      if (!payload.private_key.trim()) throw new Error("PEM private key 파일이 비어 있습니다.");
      const passphrase = $("#credential-private-key-passphrase").value;
      if (passphrase) payload.private_key_passphrase = passphrase;
    } else {
      payload.password = $("#credential-password").value;
      if (!payload.password) throw new Error("SSH password를 입력하세요.");
    }

    // The request payload now owns the values; remove every Secret-bearing DOM
    // value before starting network I/O so success and failure follow the same path.
    clearCredentialSecrets();
    const request = api("/api/v1/credentials", { method: "POST", body: payload });
    clearCredentialPayload(payload);
    const result = await request;
    const targetCredentialRef = $("#target-form [name='credential_ref']");
    if (targetCredentialRef && result.credential_ref) targetCredentialRef.value = result.credential_ref;
    closeDialog(formElement.closest("dialog"));
    formElement.reset();
    updateCredentialAuthFields();
    toast("Credential 등록 완료", result.credential_ref || result.credential_id || "서버 메모리에 등록되었습니다.");
    void refreshCredentials();
  } catch (error) {
    clearCredentialSecrets();
    toast("Credential 등록 실패", error.message, "error");
  } finally {
    clearCredentialPayload(payload);
    clearCredentialSecrets();
    setBusy(button, false);
  }
}

function bindForms() {
  $("#credential-form").addEventListener("submit", submitCredential);

  $("#app-form").addEventListener("submit", event => submitForm(event, async form => {
    const runtimeType = form.get("runtime_type");
    const port = Number(form.get("port")) || 0;
    const payload = {
      app_spec: {
        schema_version: "appspec.khu.ai/v1alpha1", kind: "AIApp",
        metadata: { name: form.get("name"), version: form.get("version"), description: form.get("description") },
        artifact: { type: form.get("artifact_type"), uri: form.get("artifact_uri"), checksum: form.get("checksum") || undefined },
        entrypoint: { command: form.get("command"), args: parseArguments(form.get("args")) },
        runtime: { type: runtimeType, accelerator: runtimeType === "gpu" ? "nvidia" : "none" },
        resources: { cpu: form.get("cpu"), memory: form.get("memory"), gpu: form.get("gpu"), storage: form.get("storage") },
      },
    };
    if (port) {
      payload.app_spec.network = { ports: [{ name: "http", app_port: port, protocol: "TCP" }] };
      payload.app_spec.healthcheck = { type: "http", path: form.get("healthcheck_path") || "/health" };
    }
    return api("/api/v1/apps", { method: "POST", body: payload });
  }, "앱 등록 완료"));

  $("#runtime-form").addEventListener("submit", event => submitForm(event, form => {
    const type = form.get("runtime_type");
    return api("/api/v1/runtime-profiles", { method: "POST", body: {
      runtime_profile_id: form.get("runtime_profile_id"), name: form.get("name"), runtime_type: type,
      accelerator: type === "gpu" ? "nvidia" : "none", adapter_type: form.get("adapter_type"), operating_mode: form.get("operating_mode"),
    } });
  }, "Runtime Profile 추가 완료"));

  $("#target-form").addEventListener("submit", event => submitForm(event, form => {
    const type = form.get("runtime_type");
    const body = {
      target_profile_id: form.get("target_profile_id"), name: form.get("name"), csp: form.get("csp"),
      vm: { host: form.get("host"), ssh_port: Number(form.get("ssh_port")) || 0, credential_ref: form.get("credential_ref") },
      runtime: { runtime_type: type, accelerator: type === "gpu" ? "nvidia" : "none", operating_mode: type === "mock" ? "local_mock" : type === "aiinfra" ? "remote_api" : "vm_process" },
    };
    if (["cpu", "gpu"].includes(type)) body.storage = { artifact_dir: form.get("artifact_dir"), model_dir: "/tmp/aiapp/models", log_dir: form.get("log_dir") };
    if (type === "gpu") body.gpu = { vendor: "nvidia", count: 1, driver_required: true };
    return api("/api/v1/target-profiles", { method: "POST", body });
  }, "Target Profile 추가 완료"));

  $("#deployment-form").addEventListener("submit", event => submitForm(event, form => api("/api/v1/deployments", { method: "POST", body: {
    app_version_id: form.get("app_version_id"), runtime_profile_id: form.get("runtime_profile_id"), target_profile_id: form.get("target_profile_id"), requested_by: "appdeployer-web",
  } }), "배포 생성 완료"));

  $("#metric-form").addEventListener("submit", event => submitForm(event, form => api(`/api/v1/deployments/${encodeURIComponent(form.get("deployment_id"))}/metrics`, { method: "POST", body: {
    latency_ms: Number(form.get("latency_ms")), throughput_rps: Number(form.get("throughput_rps")), quality_score: Number(form.get("quality_score")), request_count: Number(form.get("request_count")), error_count: Number(form.get("error_count")), metadata: { source: "appdeployer-web" },
  } }), "메트릭 기록 완료"));

  $("#resource-check-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    setBusy(button, true, "점검 중…");
    try {
      const result = await api("/api/v1/resources/check", { method: "POST", body: { target_profile_id: $("#resource-target").value, runtime_profile_id: $("#resource-runtime").value || undefined } });
      const panel = $("#resource-result"); panel.classList.remove("empty"); panel.replaceChildren();
      panel.append(statusBadge(result.status), element("strong", "", humanize(result.status)), element("span", "", Object.entries(result.checks || {}).map(([key, value]) => `${key}: ${value}`).join(" · ")));
      toast("자원 점검 완료", `${result.target_profile_id}: ${result.status}`);
      await refreshAll();
    } catch (error) { toast("자원 점검 실패", error.message, "error"); }
    finally { setBusy(button, false); }
  });

  $("#inference-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('[type="submit"]');
    setBusy(button, true, "호출 중…");
    try {
      const bodyText = $("#inference-body").value.trim();
      const request = { method: $("#inference-method").value, path: $("#inference-path").value, timeout_seconds: Number($("#inference-timeout").value) || 30 };
      const port = Number($("#inference-port").value); if (port) request.port = port;
      if (bodyText) request.body = JSON.parse(bodyText);
      const result = await api(`/api/v1/inference/${encodeURIComponent($("#inference-deployment").value)}/invoke`, { method: "POST", body: request });
      $("#inference-response").textContent = JSON.stringify(result, null, 2); toast("추론 호출 완료", `HTTP ${result.status_code} · ${result.duration_ms} ms`);
    } catch (error) { $("#inference-response").textContent = error.message; toast("추론 호출 실패", error.message, "error"); }
    finally { setBusy(button, false); }
  });
}

function bindTypeDefaults() {
	const targetForm = $("#target-form");
	const targetCredentialRef = targetForm.elements.credential_ref;
	targetCredentialRef.maxLength = 256;
	targetCredentialRef.pattern = "cred://[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*";
	targetCredentialRef.title = "cred://runtime/credential-id 또는 cred://namespace/credential-id 형식으로 입력하세요.";
	let targetValidationNoticeShown = false;
	targetForm.addEventListener("invalid", event => {
		if (targetValidationNoticeShown) return;
		targetValidationNoticeShown = true;
		const label = event.target.closest("label")?.firstChild?.textContent?.trim();
		const message = event.target.validationMessage || "필수 값과 입력 형식을 확인하세요.";
		toast("Target 입력 확인", label ? `${label}: ${message}` : message, "error");
		window.setTimeout(() => { targetValidationNoticeShown = false; }, 0);
	}, true);
	updateTargetFormForType();
	$("#credential-auth-type").addEventListener("change", updateCredentialAuthFields);
	updateCredentialAuthFields();
	$("#package-build-button").addEventListener("click", buildPackage);
	$("#package-type").addEventListener("change", updatePackageBuilder);
	$("#package-source").addEventListener("change", packageSourceChanged);
	updatePackageBuilder();
	$("#app-form [name='artifact_uri']").addEventListener("input", () => { $("#app-form [name='checksum']").value = ""; });
	$("#app-runtime-type").addEventListener("change", event => { const gpu = event.target.value === "gpu"; $("#app-form [name='gpu']").value = gpu ? "1" : "0"; });
  $("#runtime-type").addEventListener("change", event => {
    const type = event.target.value;
    const values = { cpu: ["rt-cpu-001", "cpu-runtime", "cpu_vm", "vm_process"], gpu: ["rt-gpu-001", "gpu-runtime", "gpu_vm", "vm_process"], mock: ["rt-mock-001", "mock-runtime", "mock", "local_mock"], aiinfra: ["rt-aiinfra-001", "aiinfra-runtime", "etri_aiinfra", "remote_api"] }[type];
    const form = $("#runtime-form"); ["runtime_profile_id", "name", "adapter_type", "operating_mode"].forEach((name, index) => { form.elements[name].value = values[index]; });
  });
  $("#target-runtime-type").addEventListener("change", updateTargetFormForType);
}

function updateTargetFormForType() {
  const form = $("#target-form");
  const type = form.elements.runtime_type.value;
  const credentialRef = form.elements.credential_ref;
  const wasCredentialDisabled = credentialRef.disabled;
  form.elements.target_profile_id.value = `target-${type}-001`; form.elements.name.value = `${type}-target`;
  form.elements.csp.value = type === "mock" ? "mock" : type === "aiinfra" ? "etri" : "local";
  form.elements.host.required = type !== "mock";
  form.elements.host.value = type === "mock" ? "" : form.elements.host.value;
  if (["cpu", "gpu"].includes(type)) {
    credentialRef.disabled = false;
    credentialRef.required = true;
    if (wasCredentialDisabled || !credentialRef.value) {
      credentialRef.value = state.credentials[0]?.credential_ref || `cred://local/${type}-vm-001`;
    }
    return;
  }
  credentialRef.disabled = true;
  credentialRef.required = false;
  credentialRef.value = "";
}

async function handleTableAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  try {
    if (button.dataset.action === "app-detail") {
      const result = await api(`/api/v1/apps/${encodeURIComponent(id)}`); showDetail(result.name, result, "APP SPEC");
    } else if (button.dataset.action === "app-delete") {
      const name = button.dataset.name || id;
      const version = button.dataset.version || "—";
      const confirmed = window.confirm(`${name} ${version} (${id}) App 등록을 삭제할까요?\n\n등록 정보만 삭제됩니다. STOPPED 배포 이력과 Artifact·배포 파일은 유지되며, 그 외 상태의 배포가 참조하면 삭제가 거부됩니다.`);
      if (!confirmed) return;
      setBusy(button, true, "삭제 중…");
      await api(`/api/v1/apps/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("App 등록 삭제 완료", `${name} ${version}`);
      void refreshAll();
    } else if (button.dataset.action === "runtime-delete" || button.dataset.action === "target-delete") {
      const runtime = button.dataset.action === "runtime-delete";
      const profileType = runtime ? "Runtime" : "Target";
      const name = button.dataset.name || id;
      const inventoryNotice = runtime ? "" : " 관련 readiness inventory도 함께 삭제됩니다.";
      if (!window.confirm(`${name} (${id}) ${profileType} Profile을 삭제할까요?\n\nSTOPPED가 아닌 Deployment가 참조하면 삭제가 거부됩니다.${inventoryNotice}`)) return;
      setBusy(button, true, "삭제 중…");
      const collection = runtime ? "runtime-profiles" : "target-profiles";
      await api(`/api/v1/${collection}/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast(`${profileType} Profile 삭제 완료`, `${name} (${id})`);
      await refreshAll();
    } else if (button.dataset.action === "credential-delete") {
      const ref = button.dataset.ref || id;
      if (!window.confirm(`${ref} Credential을 현재 서버 메모리에서 삭제할까요?\n\n이 참조를 사용하는 다음 VM 연결은 실패하며 삭제한 Secret은 복구할 수 없습니다.`)) return;
      setBusy(button, true, "삭제 중…");
      await api(`/api/v1/credentials/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Credential 삭제 완료", ref);
      void refreshCredentials();
    } else if (button.dataset.action === "deployment-detail") {
      const result = await api(`/api/v1/deployments/${encodeURIComponent(id)}`); showDetail(shortID(id), result, "DEPLOYMENT");
    } else if (button.dataset.action === "deployment-logs") {
      const result = await api(`/api/v1/deployments/${encodeURIComponent(id)}/logs`); showDetail(`로그 · ${shortID(id)}`, result.items || [], "EVENT STREAM");
    } else if (button.dataset.action === "deployment-stop") {
      if (!window.confirm(`${shortID(id)} 배포를 중지할까요?`)) return;
      setBusy(button, true, "중지 중…");
      await api(`/api/v1/deployments/${encodeURIComponent(id)}/stop`, { method: "POST" }); toast("배포 중지 완료", id); await refreshAll();
    }
  } catch (error) { toast("요청 실패", error.message, "error"); }
  finally { setBusy(button, false); }
}

async function checkInferenceHealth() {
  const id = $("#inference-deployment").value;
  if (!id) { toast("Deployment 선택 필요", "실행 중인 배포가 없습니다.", "error"); return; }
  const button = $("#inference-health-button"); setBusy(button, true, "확인 중…");
  try { const result = await api(`/api/v1/inference/${encodeURIComponent(id)}/health`); $("#inference-response").textContent = JSON.stringify(result, null, 2); toast("Health 확인 완료", `HTTP ${result.status_code}`); }
  catch (error) { $("#inference-response").textContent = error.message; toast("Health 확인 실패", error.message, "error"); }
  finally { setBusy(button, false); }
}

function openMobileMenu() { $("#sidebar").classList.add("open"); $("#mobile-overlay").classList.add("active"); }
function closeMobileMenu() { $("#sidebar").classList.remove("open"); $("#mobile-overlay").classList.remove("active"); }

function bindNavigation() {
  $$(".nav-item").forEach(item => item.addEventListener("click", () => navigate(item.dataset.view)));
  $$('[data-navigate]').forEach(item => item.addEventListener("click", () => navigate(item.dataset.navigate)));
  $$('[data-open-dialog]').forEach(item => item.addEventListener("click", () => openDialog(item.dataset.openDialog)));
  $$(".dialog-close").forEach(item => item.addEventListener("click", () => closeDialog(item.closest("dialog"))));
  $$("dialog").forEach(dialog => {
    dialog.addEventListener("click", event => { if (event.target === dialog) closeDialog(dialog); });
    if (dialog.id === "credential-dialog") {
      dialog.addEventListener("cancel", clearCredentialSecrets);
      dialog.addEventListener("close", clearCredentialSecrets);
    }
  });
  $("#menu-toggle").addEventListener("click", openMobileMenu); $("#mobile-overlay").addEventListener("click", closeMobileMenu);
  $("#refresh-button").addEventListener("click", () => refreshAll(true));
  $$("[data-status-filter]").forEach(item => item.addEventListener("click", () => { $$("[data-status-filter]").forEach(button => button.classList.remove("active")); item.classList.add("active"); state.deploymentFilter = item.dataset.statusFilter; renderDeployments(); }));
  $$("[data-table-search]").forEach(input => input.addEventListener("input", () => { const query = input.value.trim().toLowerCase(); $$(`#${input.dataset.tableSearch} tr`).forEach(row => { row.hidden = Boolean(query) && !String(row.dataset.search || "").includes(query); }); }));
  document.addEventListener("click", handleTableAction);
}

function bindInference() {
  $("#inference-health-button").addEventListener("click", checkInferenceHealth);
  $("#copy-response").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#inference-response").textContent); toast("응답 복사 완료"); }
    catch { toast("복사 실패", "브라우저 클립보드 권한을 확인하세요.", "error"); }
  });
}

function shouldAutoRefresh() {
  const active = document.activeElement;
  const editing = active?.matches?.("input, select, textarea");
  return !document.hidden && !$("dialog[open]") && !editing;
}

function autoRefresh() {
  if (shouldAutoRefresh()) void refreshAll(false);
}

function initialize() {
  installFieldHelp(); bindNavigation(); bindForms(); bindTypeDefaults(); bindInference(); refreshAll();
  window.setInterval(autoRefresh, 30000);
  document.addEventListener("visibilitychange", autoRefresh);
}

document.addEventListener("DOMContentLoaded", initialize);
