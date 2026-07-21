"use strict";

const API = Object.freeze({
  health: "/healthz",
  agents: "/api/v1/agents",
  planner: "/api/v1/planner/deployments",
  actionProposals: "/api/v1/automation/action-proposals",
  feedback: "/api/v1/automation/feedback",
});

const HISTORY_KEY = "geon-agent-control-history-v1";
const APP_VERSION_KEY = "geon-agent-control-app-version-id";
const VIEW_LABELS = Object.freeze({
  overview: ["CONTROL PLANE", "운영 개요"],
  planner: ["QWEN TO APPDEPLOY", "Deployment Planner"],
  agents: ["AGENT REGISTRY AND GO GUARD", "Agents & Guard"],
  feedback: ["EXECUTION FEEDBACK", "Feedback"],
});

const state = {
  agents: [],
  history: readHistory(),
  lastGuard: null,
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
    cell.colSpan = 5;
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
    tableBody.append(row);

    const item = createElement("div", "compact-item");
    const body = createElement("div", "");
    body.append(createElement("strong", "", text(agent.name)));
    body.append(createElement("span", "", `${(agent.bounded_actions || []).length} bounded actions`));
    item.append(body, createElement("span", "source-label", text(agent.source, "config")));
    snapshot.append(item);
    validation.append(new Option(text(agent.name), text(agent.name)));
  });
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

async function refreshDashboard() {
  const button = byID("refresh-button");
  button.disabled = true;
  try {
    await Promise.all([loadHealth(), loadAgents()]);
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
    state.history = [];
    writeHistory();
    renderHistory();
  });
  byID("planner-form").addEventListener("submit", submitPlanner);
  byID("action-form").addEventListener("submit", submitAction);
  byID("action-validation-form").addEventListener("submit", submitActionValidation);
  byID("feedback-form").addEventListener("submit", submitFeedback);
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
