"use strict";

const state = {
  readiness: null,
  apps: [],
  runtimes: [],
  targets: [],
  deployments: [],
  inventory: [],
  summary: null,
  metrics: [],
  deploymentFilter: "ALL",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

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

  ["#runtime-form [name='runtime_type']", "이 Profile이 담당할 실행 환경입니다. 배포할 App의 Runtime과 호환되어야 합니다."],
  ["#runtime-form [name='runtime_profile_id']", "배포 요청에서 참조하는 Runtime Profile 식별자입니다. 예: rt-cpu-001"],
  ["#runtime-form [name='name']", "화면에서 Profile을 쉽게 찾기 위한 표시 이름입니다."],
  ["#runtime-form [name='adapter_type']", "실제 실행 담당 모듈입니다. mock, cpu_vm, gpu_vm, etri_aiinfra 중 Runtime과 맞는 값을 선택하세요."],
  ["#runtime-form [name='operating_mode']", "local_mock은 로컬 시험, dry_run은 실행 모의, vm_process는 VM 프로세스 실행, remote_api는 외부 API 호출을 뜻합니다."],

  ["#target-form [name='runtime_type']", "대상 시스템이 제공하는 Runtime입니다. App과 Runtime Profile의 종류와 호환되어야 합니다."],
  ["#target-form [name='csp']", "대상을 제공하는 환경입니다. 로컬/일반 VM은 local, 기능 시험은 mock, ETRI 연동은 etri를 선택합니다."],
  ["#target-form [name='target_profile_id']", "배포 대상을 참조할 고유 식별자입니다. 예: target-gpu-001"],
  ["#target-form [name='name']", "화면에서 배포 대상을 구분하기 위한 표시 이름입니다."],
  ["#target-form [name='host']", "대상 VM의 hostname 또는 IP입니다. mock을 제외한 대상에서는 필수입니다."],
  ["#target-form [name='ssh_port']", "CPU/GPU VM에 접속할 SSH 포트입니다. 일반적으로 22를 사용합니다."],
  ["#target-form [name='credential_ref']", "비밀번호나 키 자체가 아닌 자격증명 참조값입니다. 실제 SSH 정보는 AIAPP_CREDENTIAL_* 환경변수로 설정하세요."],
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
  if (config.body !== undefined && typeof config.body !== "string") {
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

async function refreshAll(showToast = false) {
  const button = $("#refresh-button");
  setBusy(button, true, "동기화 중…");
  try {
    const [readiness, apps, runtimes, targets, deployments, inventory, summary, metrics] = await Promise.all([
      api("/api/v1/readiness"),
      api("/api/v1/apps"),
      api("/api/v1/runtime-profiles"),
      api("/api/v1/target-profiles"),
      api("/api/v1/deployments"),
      api("/api/v1/resources/inventory"),
      api("/api/v1/monitoring/summary"),
      api("/api/v1/monitoring/metrics"),
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

function renderAll() {
  renderDashboard();
  renderApps();
  renderProfiles();
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
    actions.append(detail); row.append(actions); body.append(row);
  });
}

function renderProfiles() {
  const runtimes = $("#runtime-card-list");
  runtimes.replaceChildren();
  state.runtimes.forEach(item => {
    const card = element("div", "profile-card");
    card.append(element("div", "profile-icon", item.runtime_type));
    const copy = element("div"); copy.append(element("strong", "", item.name || item.runtime_profile_id), element("span", "", item.runtime_profile_id));
    const meta = element("div", "profile-meta"); meta.append(element("strong", "", item.adapter_type), element("span", "", item.operating_mode));
    card.append(copy, meta); runtimes.append(card);
  });
  if (!state.runtimes.length) runtimes.append(element("p", "muted", "등록된 Runtime Profile이 없습니다."));

  const targets = $("#target-card-list");
  targets.replaceChildren();
  state.targets.forEach(item => {
    const card = element("div", "profile-card");
    card.append(element("div", "profile-icon", item.runtime?.runtime_type || "—"));
    const copy = element("div"); copy.append(element("strong", "", item.name || item.target_profile_id), element("span", "", item.target_profile_id));
    const meta = element("div", "profile-meta"); meta.append(element("strong", "", item.csp), element("span", "", item.vm?.host || "local mock"));
    card.append(copy, meta); targets.append(card);
  });
  if (!state.targets.length) targets.append(element("p", "muted", "등록된 Target Profile이 없습니다."));
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
  if (dialog) dialog.showModal();
}

function closeDialog(dialog) {
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

async function submitForm(event, action, successTitle) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const formData = new FormData(formElement);
  const button = formElement.querySelector('[type="submit"]');
  setBusy(button, true);
  try {
    const result = await action(formData);
    closeDialog(formElement.closest("dialog"));
    formElement.reset();
    toast(successTitle, result?.deployment_id || result?.app_version_id || result?.profile_id || "서버에 반영되었습니다.");
    void refreshAll();
  } catch (error) {
    toast("요청 실패", error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function bindForms() {
  $("#app-form").addEventListener("submit", event => submitForm(event, async form => {
    const runtimeType = form.get("runtime_type");
    const port = Number(form.get("port")) || 0;
    const payload = {
      app_spec: {
        schema_version: "appspec.khu.ai/v1alpha1", kind: "AIApp",
        metadata: { name: form.get("name"), version: form.get("version"), description: form.get("description") },
        artifact: { type: form.get("artifact_type"), uri: form.get("artifact_uri") },
        entrypoint: { command: form.get("command"), args: parseArguments(form.get("args")) },
        runtime: { type: runtimeType, accelerator: runtimeType === "gpu" ? "nvidia" : "none" },
        resources: { cpu: form.get("cpu"), memory: form.get("memory"), gpu: form.get("gpu"), storage: form.get("storage") },
      },
    };
    if (port) {
      payload.app_spec.network = { ports: [{ name: "http", app_port: port, protocol: "TCP" }] };
      payload.app_spec.healthcheck = { type: "http", path: "/health" };
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
	$("#target-form [name='host']").required = true;
	$("#app-runtime-type").addEventListener("change", event => { const gpu = event.target.value === "gpu"; $("#app-form [name='gpu']").value = gpu ? "1" : "0"; });
  $("#runtime-type").addEventListener("change", event => {
    const type = event.target.value;
    const values = { cpu: ["rt-cpu-001", "cpu-runtime", "cpu_vm", "vm_process"], gpu: ["rt-gpu-001", "gpu-runtime", "gpu_vm", "vm_process"], mock: ["rt-mock-001", "mock-runtime", "mock", "local_mock"], aiinfra: ["rt-aiinfra-001", "aiinfra-runtime", "etri_aiinfra", "remote_api"] }[type];
    const form = $("#runtime-form"); ["runtime_profile_id", "name", "adapter_type", "operating_mode"].forEach((name, index) => { form.elements[name].value = values[index]; });
  });
  $("#target-runtime-type").addEventListener("change", event => {
    const type = event.target.value;
    const form = $("#target-form");
    form.elements.target_profile_id.value = `target-${type}-001`; form.elements.name.value = `${type}-target`;
    form.elements.csp.value = type === "mock" ? "mock" : type === "aiinfra" ? "etri" : "local";
    form.elements.host.required = type !== "mock";
    form.elements.host.value = type === "mock" ? "" : form.elements.host.value;
  });
}

async function handleTableAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  try {
    if (button.dataset.action === "app-detail") {
      const result = await api(`/api/v1/apps/${encodeURIComponent(id)}`); showDetail(result.name, result, "APP SPEC");
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
  $$("dialog").forEach(dialog => dialog.addEventListener("click", event => { if (event.target === dialog) closeDialog(dialog); }));
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

function initialize() {
  installFieldHelp(); bindNavigation(); bindForms(); bindTypeDefaults(); bindInference(); refreshAll();
  window.setInterval(() => refreshAll(false), 30000);
}

document.addEventListener("DOMContentLoaded", initialize);
