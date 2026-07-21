"use strict";

const API = Object.freeze({
  health: "/healthz",
  agents: "/api/v1/agents",
  planner: "/api/v1/planner/deployments",
  actionProposals: "/api/v1/automation/action-proposals",
  feedback: "/api/v1/automation/feedback",
  autonomyStatus: "/api/v1/autonomy/status",
  autonomyConfig: "/api/v1/autonomy/config",
  autonomyStart: "/api/v1/autonomy/start",
  autonomyStop: "/api/v1/autonomy/stop",
  autonomyEmergencyStop: "/api/v1/autonomy/emergency-stop",
  autonomyCycles: "/api/v1/autonomy/cycles",
  autonomyEvents: "/api/v1/autonomy/events",
});

const HISTORY_KEY = "geon-agent-control-history-v1";
const APP_VERSION_KEY = "geon-agent-control-app-version-id";
const VIEW_LABELS = Object.freeze({
  overview: ["CONTROL PLANE", "운영 개요"],
  planner: ["QWEN TO APPDEPLOY", "Deployment Planner"],
  agents: ["AGENT REGISTRY AND GO GUARD", "Agents & Guard"],
  autonomy: ["SLO TO GUARDED EXECUTION", "Autonomous Loop"],
  feedback: ["EXECUTION FEEDBACK", "Feedback"],
});

const state = {
  agents: [],
  history: readHistory(),
  lastGuard: null,
  activeView: "overview",
  autonomyTimer: null,
  autonomyConfigLoaded: false,
  autonomyBusy: false,
  autonomyStatus: null,
};

function byID(id) {
  return document.getElementById(id);
}

function text(value, fallback = "-") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function displayTimestamp(value, fallback = "-") {
  if (!value || String(value).startsWith("0001-01-01")) return fallback;
  return new Date(value).toLocaleString("ko-KR", { hour12: false });
}

function parseList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function readHistory() {
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.slice(0, 8) : [];
  } catch (_error) {
    return [];
  }
}

function writeHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, 8)));
  } catch (_error) {
    // The dashboard remains functional when browser storage is unavailable.
  }
}

function addHistory(type, status, title, detail) {
  state.history.unshift({
    type,
    status: text(status, "unknown"),
    title,
    detail: text(detail, ""),
    at: new Date().toISOString(),
  });
  state.history = state.history.slice(0, 8);
  writeHistory();
  renderHistory();
}

function createElement(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
}

function setBusy(form, busy, busyLabel) {
  const button = form.querySelector('button[type="submit"]');
  if (!button) return;
  if (!button.dataset.label) button.dataset.label = button.textContent.trim();
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label;
}

function showToast(message, tone = "info") {
  const toast = createElement("div", `toast is-${tone}`);
  const body = createElement("div", "");
  const titles = { success: "완료", warning: "확인 필요", error: "요청 실패", info: "알림" };
  body.append(createElement("strong", "", titles[tone] || titles.info));
  body.append(createElement("span", "", message));
  toast.append(body);
  byID("toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const raw = await response.text();
  let payload = {};
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch (_error) {
      payload = { message: raw };
    }
  }
  if (!response.ok) {
    const message = payload.message || payload.error?.message || `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function switchView(viewName) {
  const labels = VIEW_LABELS[viewName] || VIEW_LABELS.overview;
  document.querySelectorAll("[data-view]").forEach((view) => {
    const active = view.dataset.view === viewName;
    view.hidden = !active;
    view.classList.toggle("is-active", active);
  });
  document.querySelectorAll(".nav-item[data-view-target]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.viewTarget === viewName);
  });
  byID("view-eyebrow").textContent = labels[0];
  byID("view-title").textContent = labels[1];
  state.activeView = viewName;
  if (viewName === "autonomy") {
    startAutonomyPolling();
  } else {
    stopAutonomyPolling();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setHealth(ok, label) {
  const pill = byID("health-pill");
  pill.dataset.status = ok ? "ok" : "error";
  pill.querySelector(".status-dot").classList.toggle("is-online", ok);
  pill.querySelector(".status-dot").classList.toggle("is-offline", !ok);
  pill.querySelector("span:last-child").textContent = label;
  byID("sidebar-health-dot").classList.toggle("is-online", ok);
  byID("sidebar-health-dot").classList.toggle("is-offline", !ok);
  byID("metric-api").textContent = ok ? "ONLINE" : "OFFLINE";
}

async function loadHealth() {
  try {
    const payload = await apiRequest(API.health);
    setHealth(payload.status === "ok", payload.status === "ok" ? "정상" : text(payload.status));
  } catch (error) {
    setHealth(false, "연결 오류");
    throw error;
  }
}

function agentRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.agents)) return payload.agents;
  if (Array.isArray(payload.items)) return payload.items;
  return [];
}

function renderAgents() {
  byID("metric-agents").textContent = String(state.agents.length);
  byID("agent-count").textContent = String(state.agents.length);
  const tableBody = byID("agent-table-body");
  const snapshot = byID("overview-agent-list");
  const validation = byID("validation-agent");
  tableBody.replaceChildren();
  snapshot.replaceChildren();
  validation.replaceChildren();

  if (state.agents.length === 0) {
    const row = document.createElement("tr");
    const cell = createElement("td", "empty-cell", "등록된 Agent가 없습니다.");
    cell.colSpan = 6;
    row.append(cell);
    tableBody.append(row);
    snapshot.append(createElement("p", "empty-state", "등록된 Agent가 없습니다."));
    validation.append(new Option("등록 Agent 없음", ""));
    return;
  }

  state.agents.forEach((agent) => {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.append(createElement("strong", "", text(agent.name)));
    if (agent.korean_name) nameCell.append(createElement("small", "table-subtitle", agent.korean_name));
    row.append(nameCell);
    row.append(createElement("td", "", text(agent.role)));
    row.append(createElement("td", "", (agent.capabilities || []).join(", ") || "-"));
    row.append(createElement("td", "", (agent.bounded_actions || []).join(", ") || "-"));
    const status = createElement("span", "status-badge", agent.enabled === false ? "disabled" : "enabled");
    status.dataset.status = agent.enabled === false ? "rejected" : "approved";
    const statusCell = document.createElement("td");
    statusCell.append(status);
    row.append(statusCell);
    const manageCell = document.createElement("td");
    manageCell.className = "agent-manage-cell";
    if (agent.source === "runtime") {
      const deleteButton = createElement("button", "icon-button danger-icon");
      deleteButton.type = "button";
      deleteButton.setAttribute("data-delete-agent", text(agent.name));
      deleteButton.title = `${text(agent.name)} 삭제`;
      deleteButton.setAttribute("aria-label", `${text(agent.name)} 삭제`);
      const deleteIcon = document.createElement("i");
      deleteIcon.setAttribute("data-lucide", "trash-2");
      deleteIcon.setAttribute("aria-hidden", "true");
      deleteButton.append(deleteIcon);
      manageCell.append(deleteButton);
    } else {
      const protectedLabel = createElement("span", "protected-label");
      protectedLabel.title = "config/agent_registry.json에서 관리되는 핵심 Agent입니다.";
      const lockIcon = document.createElement("i");
      lockIcon.setAttribute("data-lucide", "lock-keyhole");
      lockIcon.setAttribute("aria-hidden", "true");
      protectedLabel.append(lockIcon, createElement("span", "", "설정 보호"));
      manageCell.append(protectedLabel);
    }
    row.append(manageCell);
    tableBody.append(row);

    const item = createElement("div", "compact-item");
    const body = createElement("div", "");
    body.append(createElement("strong", "", text(agent.name)));
    body.append(createElement("span", "", `${(agent.bounded_actions || []).length} bounded actions`));
    item.append(body, createElement("span", "source-label", text(agent.source, "config")));
    snapshot.append(item);
    validation.append(new Option(text(agent.name), text(agent.name)));
  });
  if (window.lucide) window.lucide.createIcons();
}

async function loadAgents() {
  const payload = await apiRequest(API.agents);
  state.agents = agentRows(payload);
  renderAgents();
  return state.agents;
}

function renderHistory() {
  const list = byID("recent-list");
  list.replaceChildren();
  if (state.history.length === 0) {
    list.append(createElement("p", "empty-state", "저장된 결과가 없습니다."));
    return;
  }
  state.history.forEach((entry) => {
    const item = createElement("div", "activity-item");
    const marker = createElement("span", "activity-marker");
    marker.dataset.status = entry.status.toLowerCase();
    const body = createElement("div", "");
    body.append(createElement("strong", "", entry.title));
    body.append(createElement("span", "", entry.detail));
    const time = document.createElement("time");
    time.dateTime = entry.at;
    time.textContent = new Date(entry.at).toLocaleString("ko-KR", { hour12: false });
    item.append(marker, body, time);
    list.append(item);
  });
}

function renderPlanner(payload) {
  byID("planner-empty").hidden = true;
  byID("planner-summary").hidden = false;
  const status = text(payload.status, payload.valid ? "COMPLETED" : "NOT_EXECUTED");
  const statusElement = byID("planner-status");
  statusElement.textContent = status;
  statusElement.dataset.status = status.toLowerCase();
  byID("planner-deployment-id").textContent = text(payload.deployment?.deployment_id, "배포 ID 없음");
  byID("planner-model").textContent = text(payload.generation?.actual_model);
  byID("planner-request-guard").textContent = text(payload.request_guard?.status);
  byID("planner-manifest-guard").textContent = payload.generation?.guard_valid ? "approved" : text(payload.generation?.guard_reason, "rejected");
  byID("planner-target").textContent = text(payload.deployment?.target_profile_id, payload.manifest?.spec?.target_profile_id);
  byID("planner-latency").textContent = payload.generation?.latency_ms === undefined ? "-" : `${payload.generation.latency_ms} ms`;
  byID("planner-log-count").textContent = String(Array.isArray(payload.logs) ? payload.logs.length : 0);
  byID("planner-json").textContent = pretty(payload);
}

function renderAction(payload) {
  const guard = payload.guard || {};
  const proposal = payload.decision?.proposal || {};
  const handoff = payload.handoff || {};
  const status = text(guard.status, payload.status);
  const decision = byID("guard-decision");
  decision.dataset.status = guard.valid ? "approved" : status.toLowerCase();
  byID("guard-status").textContent = status.toUpperCase();
  byID("guard-action").textContent = text(proposal.action, "Action 없음");
  byID("guard-reason").textContent = text(guard.reason, proposal.reason);
  byID("action-model").textContent = text(payload.decision?.actual_model);
  byID("action-confidence").textContent = proposal.confidence === undefined ? "-" : `${Math.round(proposal.confidence * 100)}%`;
  byID("action-target").textContent = text(proposal.target_vm_id, payload.vm_compatibility?.target_vm_id);
  byID("action-handoff").textContent = `${text(handoff.agent, "not registered")} / ${text(handoff.execution_status, payload.status)}`;
  byID("action-json").textContent = pretty(payload);
  byID("metric-guard").textContent = status.toUpperCase();
  byID("metric-guard-detail").textContent = text(proposal.action, payload.status);
  state.lastGuard = guard;
  if (payload.correlation_id) {
    byID("feedback-form").elements.correlation_id.value = payload.correlation_id;
  }
}

async function submitPlanner(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    natural_language_request: data.get("natural_language_request"),
    app_version_id: data.get("app_version_id"),
    candidate_id: data.get("candidate_id"),
    requested_by: "ai-ops-geon-control-app",
    poll_interval_ms: Number(data.get("poll_interval_ms")),
    max_poll_attempts: Number(data.get("max_poll_attempts")),
  };
  const target = String(data.get("target_profile_id") || "").trim();
  if (target) body.target_profile_id = target;
  try {
    localStorage.setItem(APP_VERSION_KEY, body.app_version_id);
  } catch (_error) {
    // Saving the convenience value is optional.
  }
  setBusy(form, true, "Qwen 판단 중...");
  try {
    const payload = await apiRequest(API.planner, { method: "POST", body: JSON.stringify(body) });
    renderPlanner(payload);
    addHistory("planner", payload.status, "AppDeploy 배포 계획", payload.deployment?.deployment_id || payload.generation?.guard_reason);
    showToast(`Planner 완료: ${text(payload.status)}`, payload.valid ? "success" : "warning");
  } catch (error) {
    const payload = error.payload || { valid: false, message: error.message };
    renderPlanner(payload);
    addHistory("planner", "failed", "AppDeploy 배포 계획 실패", error.message);
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function submitAction(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  let observations;
  try {
    observations = JSON.parse(String(data.get("observations") || "{}"));
  } catch (_error) {
    showToast("Observations JSON 형식을 확인해 주세요.", "error");
    return;
  }
  const accelerator = data.get("accelerator");
  const body = {
    workload: data.get("workload"),
    candidate_id: data.get("candidate_id"),
    observations,
    target_vm: {
      id: data.get("vm_id"),
      source: "control_app_input",
      evidence_status: data.get("evidence_status"),
      provider: data.get("provider"),
      region: data.get("region"),
      instance_type: data.get("instance_type"),
      accelerator,
      gpu_model: accelerator === "gpu" ? data.get("gpu_model") : "",
      gpu_memory_mib: accelerator === "gpu" ? Number(data.get("gpu_memory_mib")) : 0,
      collected_at: new Date().toISOString(),
      performance: { status: data.get("performance_status") },
    },
  };
  setBusy(form, true, "Qwen Action 생성 중...");
  try {
    const payload = await apiRequest(API.actionProposals, { method: "POST", body: JSON.stringify(body) });
    renderAction(payload);
    addHistory("guard", payload.guard?.status, "Qwen Action / Go Guard", payload.decision?.proposal?.action);
    showToast(`Go Guard: ${text(payload.guard?.status)}`, payload.guard?.valid ? "success" : "warning");
  } catch (error) {
    const payload = error.payload || { valid: false, message: error.message };
    byID("action-json").textContent = pretty(payload);
    addHistory("guard", "failed", "Action Proposal 실패", error.message);
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function submitActionValidation(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const agent = String(data.get("agent_name") || "");
  const action = String(data.get("action") || "");
  if (!agent) {
    showToast("먼저 Agent를 등록해 주세요.", "warning");
    return;
  }
  setBusy(form, true, "검증 중...");
  try {
    const path = `${API.agents}/${encodeURIComponent(agent)}/actions/${encodeURIComponent(action)}/validate`;
    const payload = await apiRequest(path, { method: "POST" });
    byID("action-json").textContent = pretty(payload);
    const synthetic = {
      status: payload.valid ? "approved" : "rejected",
      guard: { valid: payload.valid, status: payload.valid ? "approved" : "rejected", reason: "Agent Registry bounded action validation" },
      decision: { proposal: { action: payload.action }, actual_model: "not used" },
      handoff: { agent: payload.agent, execution_status: "not_executed" },
    };
    renderAction(synthetic);
    addHistory("validation", synthetic.status, "Bounded Action 검증", `${payload.agent}: ${payload.action}`);
    showToast(payload.valid ? "허용된 Action입니다." : "허용되지 않은 Action입니다.", payload.valid ? "success" : "warning");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function submitAgentRegistration(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    name: data.get("name"),
    version: data.get("version"),
    role: data.get("role"),
    responsibilities: ["Execute only approved bounded actions"],
    endpoint: data.get("endpoint"),
    invocation_path: data.get("invocation_path"),
    capabilities: parseList(data.get("capabilities")),
    bounded_actions: parseList(data.get("bounded_actions")),
    reward_signals: ["execution_success", "slo_recovery"],
    enabled: true,
  };
  setBusy(form, true, "등록 중...");
  try {
    const payload = await apiRequest(API.agents, { method: "POST", body: JSON.stringify(body) });
    byID("agent-dialog").close();
    await loadAgents();
    addHistory("registry", "registered", "외부 Agent 등록", payload.name);
    showToast(`${payload.name} 등록 완료`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function submitFeedback(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    correlation_id: data.get("correlation_id"),
    executor: data.get("executor"),
    status: data.get("status"),
  };
  ["external_execution_id", "message"].forEach((field) => {
    const value = String(data.get(field) || "").trim();
    if (value) body[field] = value;
  });
  ["latency_ms", "throughput_rps"].forEach((field) => {
    const value = String(data.get(field) || "").trim();
    if (value) body[field] = Number(value);
  });
  setBusy(form, true, "기록 중...");
  try {
    const payload = await apiRequest(API.feedback, { method: "POST", body: JSON.stringify(body) });
    byID("feedback-json").textContent = pretty(payload);
    addHistory("feedback", payload.status, "실행 Feedback", payload.external_execution_id || payload.correlation_id);
    showToast("Feedback가 기록되었습니다.", "success");
  } catch (error) {
    byID("feedback-json").textContent = pretty(error.payload || { valid: false, message: error.message });
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function deleteRuntimeAgent(name, button) {
  if (!window.confirm(`Runtime Agent '${name}'을 geon에서 삭제할까요?`)) return;
  button.disabled = true;
  try {
    await apiRequest(`${API.agents}/${encodeURIComponent(name)}`, { method: "DELETE" });
    await loadAgents();
    showToast(`Runtime Agent '${name}'을 삭제했습니다.`, "success");
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
}

function setAutonomyBusy(busy) {
  state.autonomyBusy = busy;
  const status = state.autonomyStatus || {};
  byID("autonomy-start").disabled = busy || Boolean(status.running);
  byID("autonomy-stop").disabled = busy || !status.running;
  ["autonomy-run-cycle", "autonomy-emergency-stop", "autonomy-refresh", "clear-autonomy-events"].forEach((id) => { byID(id).disabled = busy; });
  const submit = byID("autonomy-form").querySelector('button[type="submit"]');
  submit.disabled = busy || Boolean(status.running);
}

function syncAutonomyForm(config) {
  if (!config) return;
  const form = byID("autonomy-form");
  const mode = form.querySelector(`input[name="mode"][value="${config.mode}"]`);
  if (mode) mode.checked = true;
  const values = {
    deployment_id: config.deployment_id,
    max_latency_ms: config.slo?.max_latency_ms,
    min_throughput_rps: config.slo?.min_throughput_rps,
    max_error_rate: config.slo?.max_error_rate,
    poll_interval_seconds: config.poll_interval_seconds,
    consecutive_violations: config.consecutive_violations,
    cooldown_seconds: config.cooldown_seconds,
    max_actions_per_deployment: config.max_actions_per_deployment,
    max_metric_age_seconds: config.max_metric_age_seconds,
    standby_target_profile_id: config.standby_target_profile_id,
    rollback_app_version_id: config.rollback_app_version_id,
  };
  Object.entries(values).forEach(([name, value]) => {
    if (form.elements[name]) form.elements[name].value = value ?? "";
  });
  state.autonomyConfigLoaded = true;
}

function renderAutonomyStatus(payload, forceFormSync = false) {
  state.autonomyStatus = payload;
  const runtime = payload.state || {};
  const evaluation = runtime.last_evaluation || {};
  const metric = evaluation.metric || {};
  const decision = runtime.last_decision || {};
  const guard = runtime.last_guard || {};
  const execution = runtime.last_execution || {};
  const config = payload.config || {};
  const loopStatus = byID("autonomy-loop-status");
  const loopLabel = payload.execution_locked ? "LOCKED" : payload.running ? "RUNNING" : "STOPPED";
  loopStatus.textContent = loopLabel;
  loopStatus.dataset.status = payload.execution_locked ? "blocked" : payload.running ? "running" : "stopped";
  byID("autonomy-connection").textContent = payload.appdeploy_configured ? "AppDeploy connected" : "AppDeploy not configured";
  byID("autonomy-connection").dataset.status = payload.appdeploy_configured ? "ok" : "offline";
  byID("autonomy-last-metric").textContent = displayTimestamp(runtime.last_metric_timestamp, "No metric");
  byID("autonomy-latency").textContent = metric.latency_ms === undefined ? "-" : Number(metric.latency_ms).toFixed(2);
  byID("autonomy-throughput").textContent = metric.throughput_rps === undefined ? "-" : Number(metric.throughput_rps).toFixed(2);
  const errorRate = metric.request_count > 0 ? Number(metric.error_count || 0) / Number(metric.request_count) : null;
  byID("autonomy-error-rate").textContent = errorRate === null ? "-" : errorRate.toFixed(3);
  byID("autonomy-evaluation-status").textContent = text(evaluation.status);
  byID("autonomy-consecutive").textContent = String(runtime.consecutive_violations || 0);
  byID("autonomy-cooldown").textContent = displayTimestamp(runtime.cooldown_until);
  byID("autonomy-action-budget").textContent = `${runtime.automatic_action_count || 0} / ${config.max_actions_per_deployment || 0}`;
  byID("autonomy-qwen-action").textContent = text(decision.action);
  byID("autonomy-qwen-confidence").textContent = decision.confidence === undefined ? "-" : `${Math.round(Number(decision.confidence) * 100)}%`;
  byID("autonomy-guard-status").textContent = text(guard.status);
  byID("autonomy-execution-status").textContent = text(execution.status);
  byID("autonomy-latest-json").textContent = pretty(payload.latest_event || { status: "no_event" });
  byID("autonomy-start").disabled = state.autonomyBusy || Boolean(payload.running);
  byID("autonomy-stop").disabled = state.autonomyBusy || !payload.running;
  byID("autonomy-form").querySelector('button[type="submit"]').disabled = state.autonomyBusy || Boolean(payload.running);
  if (forceFormSync || !state.autonomyConfigLoaded) syncAutonomyForm(config);
}

function renderAutonomyEvents(payload) {
  const timeline = byID("autonomy-timeline");
  const events = Array.isArray(payload.events) ? [...payload.events].reverse() : [];
  timeline.replaceChildren();
  if (events.length === 0) {
    timeline.append(createElement("p", "empty-state", "기록된 자율 제어 이벤트가 없습니다."));
    return;
  }
  events.forEach((entry) => {
    const row = createElement("article", "timeline-event");
    const timeElement = document.createElement("time");
    timeElement.dateTime = entry.timestamp || "";
    timeElement.textContent = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString("ko-KR", { hour12: false }) : "-";
    const stage = createElement("span", "timeline-event-stage", text(entry.stage));
    const body = createElement("div", "timeline-event-body");
    const status = createElement("strong", "", text(entry.status));
    status.dataset.status = String(entry.status || "").toLowerCase();
    body.append(status, createElement("span", "", text(entry.reason)));
    const details = document.createElement("details");
    details.append(createElement("summary", "", "JSON"), createElement("pre", "", pretty(entry)));
    row.append(timeElement, stage, body, details);
    timeline.append(row);
  });
}

async function loadAutonomy(forceFormSync = false) {
  const [status, events] = await Promise.all([apiRequest(API.autonomyStatus), apiRequest(API.autonomyEvents)]);
  renderAutonomyStatus(status, forceFormSync);
  renderAutonomyEvents(events);
  return status;
}

function stopAutonomyPolling() {
  if (state.autonomyTimer) window.clearInterval(state.autonomyTimer);
  state.autonomyTimer = null;
}

function startAutonomyPolling() {
  stopAutonomyPolling();
  void loadAutonomy().catch((error) => showToast(error.message, "error"));
  state.autonomyTimer = window.setInterval(() => {
    if (state.activeView !== "autonomy" || state.autonomyBusy) return;
    void loadAutonomy().catch((error) => showToast(error.message, "error"));
  }, 3000);
}

async function submitAutonomyConfig(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    mode: data.get("mode"),
    poll_interval_seconds: Number(data.get("poll_interval_seconds")),
    consecutive_violations: Number(data.get("consecutive_violations")),
    cooldown_seconds: Number(data.get("cooldown_seconds")),
    max_actions_per_deployment: Number(data.get("max_actions_per_deployment")),
    max_metric_age_seconds: Number(data.get("max_metric_age_seconds")),
    deployment_id: String(data.get("deployment_id") || "").trim(),
    standby_target_profile_id: String(data.get("standby_target_profile_id") || "").trim(),
    rollback_app_version_id: String(data.get("rollback_app_version_id") || "").trim(),
    slo: {
      max_latency_ms: Number(data.get("max_latency_ms")),
      min_throughput_rps: Number(data.get("min_throughput_rps")),
      max_error_rate: Number(data.get("max_error_rate")),
    },
  };
  setAutonomyBusy(true);
  try {
    const payload = await apiRequest(API.autonomyConfig, { method: "PUT", body: JSON.stringify(body) });
    renderAutonomyStatus(payload, true);
    showToast("Autonomy 정책을 저장했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setAutonomyBusy(false);
    await loadAutonomy().catch(() => {});
  }
}

async function runAutonomyControl(path, successMessage) {
  setAutonomyBusy(true);
  try {
    const payload = await apiRequest(path, { method: "POST" });
    if (path === API.autonomyCycles) byID("autonomy-latest-json").textContent = pretty(payload);
    showToast(successMessage, payload.status === "partial_failure" ? "warning" : "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setAutonomyBusy(false);
    await loadAutonomy().catch(() => {});
  }
}

async function clearAutonomyEvents() {
  if (!window.confirm("geon의 자율 제어 이벤트만 삭제할까요? Loop 설정과 AppDeploy 자원은 유지됩니다.")) return;
  setAutonomyBusy(true);
  try {
    const payload = await apiRequest(API.autonomyEvents, { method: "DELETE" });
    renderAutonomyEvents(payload);
    showToast(`${Number(payload.deleted_count || 0)}개의 자율 제어 이벤트를 삭제했습니다.`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setAutonomyBusy(false);
    await loadAutonomy().catch(() => {});
  }
}

async function refreshDashboard() {
  const button = byID("refresh-button");
  button.disabled = true;
  try {
    const requests = [loadHealth(), loadAgents()];
    if (state.activeView === "autonomy") requests.push(loadAutonomy());
    await Promise.all(requests);
    byID("last-updated").textContent = new Date().toLocaleString("ko-KR", { hour12: false });
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function bindEvents() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.viewTarget));
  });
  byID("refresh-button").addEventListener("click", refreshDashboard);
  byID("refresh-agents").addEventListener("click", async () => {
    try {
      await loadAgents();
      showToast("Agent Registry를 새로고침했습니다.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
  byID("clear-history").addEventListener("click", () => {
    if (!window.confirm("이 브라우저에 저장된 geon 시험 기록만 삭제할까요?")) return;
    state.history = [];
    writeHistory();
    renderHistory();
  });
  byID("agent-table-body").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-agent]");
    if (!button) return;
    void deleteRuntimeAgent(button.dataset.deleteAgent, button);
  });
  byID("planner-form").addEventListener("submit", submitPlanner);
  byID("action-form").addEventListener("submit", submitAction);
  byID("action-validation-form").addEventListener("submit", submitActionValidation);
  byID("feedback-form").addEventListener("submit", submitFeedback);
  byID("autonomy-form").addEventListener("submit", submitAutonomyConfig);
  byID("autonomy-start").addEventListener("click", () => runAutonomyControl(API.autonomyStart, "Autonomy loop를 시작했습니다."));
  byID("autonomy-stop").addEventListener("click", () => runAutonomyControl(API.autonomyStop, "Autonomy loop를 중지했습니다."));
  byID("autonomy-run-cycle").addEventListener("click", () => runAutonomyControl(API.autonomyCycles, "Autonomy cycle을 실행했습니다."));
  byID("autonomy-emergency-stop").addEventListener("click", () => {
    if (!window.confirm("Autonomy loop를 즉시 중지하고 Monitor Only로 전환할까요?")) return;
    void runAutonomyControl(API.autonomyEmergencyStop, "Emergency Stop이 적용되었습니다.");
  });
  byID("clear-autonomy-events").addEventListener("click", () => void clearAutonomyEvents());
  byID("autonomy-refresh").addEventListener("click", async () => {
    setAutonomyBusy(true);
    try {
      await loadAutonomy();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setAutonomyBusy(false);
    }
  });
  byID("agent-registration-form").addEventListener("submit", submitAgentRegistration);
  byID("open-agent-dialog").addEventListener("click", () => byID("agent-dialog").showModal());
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(byID(button.dataset.copyTarget).textContent);
        showToast("JSON을 클립보드에 복사했습니다.", "success");
      } catch (_error) {
        showToast("클립보드 복사 권한을 확인해 주세요.", "error");
      }
    });
  });
  window.addEventListener("beforeunload", stopAutonomyPolling);
}

async function initialize() {
  bindEvents();
  renderHistory();
  const savedAppVersion = localStorage.getItem(APP_VERSION_KEY);
  if (savedAppVersion) byID("planner-form").elements.app_version_id.value = savedAppVersion;
  if (window.lucide) window.lucide.createIcons();
  await refreshDashboard();
}

void initialize();
