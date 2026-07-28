"use strict";

const API = Object.freeze({
  health: "/healthz",
  agents: "/api/v1/agents",
  controlRuns: "/api/v1/control-runs",
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

const APP_VERSION_KEY = "geon-agent-control-app-version-id";
const ACTIVE_RUN_KEY = "geon-agent-control-active-run-id";
const VIEW_LABELS = Object.freeze({
  planner: ["USER REQUEST TO GUARDED MANIFEST", "Manifest Workflow"],
  agents: ["AGENT REGISTRY AND GO GUARD", "Agents & Guard"],
  autonomy: ["OPTIONAL POST-DEPLOYMENT EXPERIMENT", "Post-deployment"],
  feedback: ["AUTOMATIC RUN EVIDENCE", "Feedback"],
  overview: ["WORKFLOW GUIDE", "Guide"],
});

const { buildManifestStageViewModel } = window.ManifestStages;

const state = {
  agents: [],
  controlRuns: [],
  activeRunID: "",
  lastPlannerRun: null,
  feedbackRecords: [],
  autonomyEvents: [],
  activeView: "planner",
  autonomyTimer: null,
  autonomyConfigLoaded: false,
  autonomyBusy: false,
  autonomyStatus: null,
  activeExecutionAgent: "",
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
  if (viewName === "feedback") {
    void loadFeedbackView().catch((error) => showToast(error.message, "error"));
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
  const validation = byID("validation-agent");
  tableBody.replaceChildren();
  validation.replaceChildren();

  if (state.agents.length === 0) {
    const row = document.createElement("tr");
    const cell = createElement("td", "empty-cell", "등록된 Agent가 없습니다.");
    cell.colSpan = 7;
    row.append(cell);
    tableBody.append(row);
    validation.append(new Option("등록 Agent 없음", ""));
    return;
  }

  const selectedAgent = activeControlRun()?.selected_agent?.name || "";
  state.agents.forEach((agent) => {
    const row = document.createElement("tr");
    row.setAttribute("data-selected-agent", String(agent.name === selectedAgent));
    const nameCell = document.createElement("td");
    nameCell.append(createElement("strong", "", text(agent.name)));
    if (agent.korean_name) nameCell.append(createElement("small", "table-subtitle", agent.korean_name));
    if (agent.name === selectedAgent) nameCell.append(createElement("small", "selected-agent-label", "selected by active Run"));
    row.append(nameCell);
    const source = createElement("span", "mini-badge", agent.source === "runtime" ? "external" : "internal");
    const sourceCell = document.createElement("td");
    sourceCell.append(source);
    row.append(sourceCell);
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
    if (agent.enabled !== false) {
      const executeButton = createElement("button", "icon-button");
      executeButton.type = "button";
      executeButton.setAttribute("data-execute-agent", text(agent.name));
      executeButton.title = `${text(agent.name)} 실행`;
      executeButton.setAttribute("aria-label", `${text(agent.name)} 실행`);
      const executeIcon = document.createElement("i");
      executeIcon.setAttribute("data-lucide", "play");
      executeIcon.setAttribute("aria-hidden", "true");
      executeButton.append(executeIcon);
      manageCell.append(executeButton);
    }
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
    }
    row.append(manageCell);
    tableBody.append(row);

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

function controlRunRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.runs)) return payload.runs;
  if (Array.isArray(payload.items)) return payload.items;
  return [];
}

function activeControlRun() {
  return state.controlRuns.find((run) => run.run_id === state.activeRunID) || null;
}

function isDeployedRun(run) {
  return Boolean(
    run &&
    run.status === "DEPLOYED" &&
    run.deployment?.deployment_id,
  );
}

function setActiveControlRun(run) {
  const existingRunIndex = state.controlRuns.findIndex((entry) => entry.run_id === run?.run_id);
  if (existingRunIndex >= 0) state.controlRuns[existingRunIndex] = run;
  state.activeRunID = run?.run_id || "";
  state.lastPlannerRun = run || null;
  if (state.activeRunID) {
    localStorage.setItem(ACTIVE_RUN_KEY, state.activeRunID);
  } else {
    localStorage.removeItem(ACTIVE_RUN_KEY);
  }
  renderControlRunTimeline(run);
  renderPlanner(run);
  syncRunLinkedForms(run);
  renderAgents();
  renderPostDeploymentReadiness(run);
  renderAutomaticRunFeedback(run);
}

function renderManifestStageFlow(run) {
  const flow = byID("manifest-stage-flow");
  flow.replaceChildren();

  buildManifestStageViewModel(run).forEach((stage) => {
    const item = createElement("li", "manifest-stage");
    item.dataset.status = stage.status;
    item.append(
      createElement("span", "manifest-stage-index", String(stage.index)),
      createElement("strong", "", stage.label),
      createElement("small", "", stage.reason),
    );
    flow.append(item);
  });
}

function renderControlRunTimeline(run) {
  renderManifestStageFlow(run);
  const timeline = byID("control-run-timeline");
  timeline.replaceChildren();
  byID("selected-run-id").textContent = run?.run_id || "선택된 Run 없음";
  byID("selected-planner-agent").textContent = run?.selected_agent?.name
    ? `Manifest Planner: ${run.selected_agent.name}`
    : "선택된 Planner Agent 없음";
  if (!run || !Array.isArray(run.stages) || run.stages.length === 0) {
    timeline.append(createElement("li", "empty-state", "Run을 선택하면 각 검증 단계가 표시됩니다."));
    return;
  }
  run.stages.forEach((stage, index) => {
    const item = createElement("li", "control-run-stage");
    item.dataset.status = String(stage.status || "unknown").toLowerCase();
    item.append(
      createElement("span", "control-run-stage-index", String(index + 1)),
      createElement("strong", "", text(stage.name)),
      createElement("small", "", `${text(stage.status)} · ${text(stage.reason, "completed")}`),
    );
    timeline.append(item);
  });
}

function syncRunLinkedForms(run) {
  const ready = isDeployedRun(run);
  const actionRunInput = byID("action-form").elements.run_id;
  if (actionRunInput) actionRunInput.value = ready ? run.run_id : "";
  const autonomyForm = byID("autonomy-form");
  autonomyForm.elements.run_id.value = ready ? run.run_id : "";
  autonomyForm.elements.deployment_id.value = ready ? run.deployment.deployment_id : "";
}

function renderControlRuns(payload = state.controlRuns) {
  const list = byID("control-run-list");
  const runs = Array.isArray(payload) ? payload : controlRunRows(payload);
  state.controlRuns = runs;
  list.replaceChildren();
  if (runs.length === 0) {
    list.append(createElement("p", "empty-state", "서버에 저장된 ControlRun이 없습니다."));
    populateAutonomyRunOptions();
    setActiveControlRun(null);
    return;
  }
  runs.forEach((run) => {
    const item = createElement("div", "activity-item");
    item.setAttribute("data-run-id", text(run.run_id));
    const marker = createElement("span", "activity-marker");
    marker.dataset.status = String(run.status || "unknown").toLowerCase();
    const body = createElement("div", "");
    const selectButton = createElement("button", "run-select-button", text(run.run_id));
    selectButton.type = "button";
    selectButton.setAttribute("data-select-run", text(run.run_id));
    body.append(selectButton);
    body.append(createElement("span", "", `${text(run.status)} · ${text(run.request?.app_version_id)}`));
    const time = document.createElement("time");
    time.dateTime = run.updated_at || "";
    time.textContent = displayTimestamp(run.updated_at);
    const deleteButton = createElement("button", "icon-button danger-icon record-delete-button");
    deleteButton.type = "button";
    deleteButton.setAttribute("data-delete-run", text(run.run_id));
    deleteButton.title = `${text(run.run_id)} 삭제`;
    deleteButton.setAttribute("aria-label", `${text(run.run_id)} ControlRun 삭제`);
    const deleteIcon = document.createElement("i");
    deleteIcon.setAttribute("data-lucide", "trash-2");
    deleteIcon.setAttribute("aria-hidden", "true");
    deleteButton.append(deleteIcon);
    item.append(marker, body, time, deleteButton);
    list.append(item);
  });
  populateAutonomyRunOptions();
  const remembered = localStorage.getItem(ACTIVE_RUN_KEY);
  const selected = runs.find((run) => run.run_id === remembered) || runs[0] || null;
  if (remembered && !runs.some((run) => run.run_id === remembered)) localStorage.removeItem(ACTIVE_RUN_KEY);
  setActiveControlRun(selected);
  if (window.lucide) window.lucide.createIcons();
}

function populateAutonomyRunOptions() {
  const select = byID("autonomy-run-id");
  const current = select.value;
  select.replaceChildren(new Option("배포된 Run을 선택하세요", ""));
  state.controlRuns
    .filter((run) => run.status === "DEPLOYED" && run.deployment?.deployment_id)
    .forEach((run) => select.append(new Option(`${run.run_id} · ${run.deployment.deployment_id}`, run.run_id)));
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function populateFeedbackRunOptions(run) {
  const select = byID("feedback-run-id");
  select.replaceChildren(new Option("Select a ControlRun", ""));
  state.controlRuns.forEach((entry) => {
    select.append(new Option(`${entry.run_id} | ${text(entry.status)}`, entry.run_id));
  });
  select.value = run?.run_id || "";
}

async function loadControlRuns() {
  const payload = await apiRequest(API.controlRuns);
  renderControlRuns(payload);
  return state.controlRuns;
}

async function selectControlRun(runID) {
  const run = await apiRequest(`${API.controlRuns}/${encodeURIComponent(runID)}`);
  setActiveControlRun(run);
}

async function deleteControlRun(runID, button) {
  if (!window.confirm(`ControlRun '${runID}'을 geon에서 삭제할까요? AppDeploy와 VM 자원은 삭제되지 않습니다.`)) return;
  button.disabled = true;
  try {
    await apiRequest(`${API.controlRuns}/${encodeURIComponent(runID)}`, { method: "DELETE" });
    if (state.activeRunID === runID) {
      setActiveControlRun(null);
    }
    await loadControlRuns();
    showToast("ControlRun 기록을 삭제했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
}

async function clearControlRuns() {
  if (!window.confirm("geon의 ControlRun 기록을 모두 삭제할까요? AppDeploy와 VM 자원은 유지됩니다.")) return;
  const button = byID("clear-history");
  button.disabled = true;
  try {
    const payload = await apiRequest(API.controlRuns, { method: "DELETE" });
    setActiveControlRun(null);
    renderControlRuns(payload);
    showToast(`${Number(payload.deleted_count || 0)}개의 ControlRun 기록을 삭제했습니다.`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function renderPlanner(payload) {
  renderManifestStageFlow(payload);
  if (!payload) {
    byID("planner-empty").hidden = false;
    byID("planner-summary").hidden = true;
    byID("planner-submit").disabled = true;
    return;
  }
  byID("planner-empty").hidden = true;
  byID("planner-summary").hidden = false;
  const status = text(payload.status, payload.valid ? "COMPLETED" : "NOT_EXECUTED");
  const statusElement = byID("planner-status");
  statusElement.textContent = status;
  statusElement.dataset.status = status.toLowerCase();
  const deploymentLabel = payload.deployment?.deployment_id ? ` · ${payload.deployment.deployment_id}` : "";
  byID("planner-deployment-id").textContent = `${text(payload.run_id, "Run ID 없음")}${deploymentLabel}`;
  byID("planner-model").textContent = text(payload.generation?.actual_model);
  byID("planner-agent").textContent = text(payload.selected_agent?.name);
  byID("planner-request-guard").textContent = text(payload.request_guard?.status);
  byID("planner-manifest-guard").textContent = payload.generation?.guard_valid ? "approved" : text(payload.generation?.guard_reason, "rejected");
  byID("planner-target").textContent = text(payload.deployment?.target_profile_id, payload.manifest?.spec?.target_profile_id);
  byID("planner-latency").textContent = payload.generation?.latency_ms === undefined ? "-" : `${payload.generation.latency_ms} ms`;
  byID("planner-log-count").textContent = String(Array.isArray(payload.logs) ? payload.logs.length : 0);
  byID("planner-json").textContent = pretty(payload);
  const submitButton = byID("planner-submit");
  submitButton.disabled = !["MANIFEST_APPROVED", "APPDEPLOY_FAILED"].includes(status);
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
    requested_by: "ai-ops-geon-planner",
    agent_name: data.get("agent_name"),
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
    const payload = await apiRequest(API.controlRuns, { method: "POST", body: JSON.stringify(body) });
    setActiveControlRun(payload);
    await loadControlRuns();
    await loadFeedbackView().catch(() => {});
    showToast(`Manifest 생성 완료: ${text(payload.status)}`, payload.status === "MANIFEST_APPROVED" ? "success" : "warning");
  } catch (error) {
    const payload = error.payload || { valid: false, message: error.message };
    if (payload.run_id) setActiveControlRun(payload);
    else renderPlanner(payload);
    await loadControlRuns().catch(() => {});
    await loadFeedbackView().catch(() => {});
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function submitPlannerRun() {
  const run = activeControlRun() || state.lastPlannerRun;
  if (!run?.run_id) {
    showToast("먼저 DeploymentManifest를 생성해 주세요.", "warning");
    return;
  }
  const form = byID("planner-form");
  const data = new FormData(form);
  const button = byID("planner-submit");
  const originalLabel = button.textContent.trim();
  button.disabled = true;
  button.textContent = "AppDeploy 제출 중...";
  try {
    const body = {
      poll_interval_ms: Number(data.get("poll_interval_ms")),
      max_poll_attempts: Number(data.get("max_poll_attempts")),
    };
    const payload = await apiRequest(`${API.controlRuns}/${encodeURIComponent(run.run_id)}/submit`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    setActiveControlRun(payload);
    await loadControlRuns();
    await loadFeedbackView().catch(() => {});
    showToast(`AppDeploy 제출 완료: ${text(payload.deployment?.deployment_id)}`, "success");
  } catch (error) {
    if (error.payload?.run_id) setActiveControlRun(error.payload);
    await loadControlRuns().catch(() => {});
    await loadFeedbackView().catch(() => {});
    showToast(error.message, "error");
  } finally {
    button.textContent = originalLabel;
    button.disabled = !["MANIFEST_APPROVED", "APPDEPLOY_FAILED"].includes(state.lastPlannerRun?.status);
    if (window.lucide) window.lucide.createIcons();
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
    run_id: String(data.get("run_id") || "").trim(),
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
    await loadControlRuns().catch(() => {});
    showToast(`Go Guard: ${text(payload.guard?.status)}`, payload.guard?.valid ? "success" : "warning");
  } catch (error) {
    const payload = error.payload || { valid: false, message: error.message };
    byID("action-json").textContent = pretty(payload);
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
    auth_token_env: String(data.get("auth_token_env") || "").trim(),
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
    showToast(`${payload.name} 등록 완료`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

function openAgentExecution(agentName) {
  const agent = state.agents.find((item) => item.name === agentName);
  if (!agent) {
    showToast("선택한 Agent를 찾을 수 없습니다.", "error");
    return;
  }
  state.activeExecutionAgent = agent.name;
  byID("agent-execution-agent").textContent = agent.name;
  byID("agent-execution-source").textContent = agent.source === "runtime" ? "external" : "internal";
  const status = byID("agent-execution-status");
  status.textContent = "READY";
  status.dataset.status = "pending";
  byID("agent-execution-run-id").textContent = "-";
  byID("agent-execution-result").textContent = "아직 Agent 실행 결과가 없습니다.";

  const capability = byID("agent-execution-capability");
  capability.replaceChildren();
  (agent.capabilities || []).forEach((value) => capability.append(new Option(value, value)));
  const action = byID("agent-execution-action");
  action.replaceChildren();
  (agent.bounded_actions || []).forEach((value) => action.append(new Option(value, value)));

  const isManifestAgent = agent.name === "AIApplicationAutomationAgent";
  if (isManifestAgent) {
    capability.value = "deployment_manifest_planning";
    action.value = "generate_deployment_manifest";
    byID("agent-execution-input").value = pretty({
      natural_language_request: "Mock 환경에서 CPU 1, 메모리 1Gi, GPU 0, 스토리지 1Gi인 AI 응용 배포 계획을 생성해 주세요.",
      app_version_id: localStorage.getItem(APP_VERSION_KEY) || "appver-example",
      candidate_id: "qwen3.5-ops-planner",
      requested_by: "ai-ops-geon-planner",
    });
  } else {
    byID("agent-execution-input").value = "{}";
  }
  byID("agent-execution-dialog").showModal();
}

async function submitAgentExecution(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const agentName = state.activeExecutionAgent;
  let input;
  try {
    input = JSON.parse(byID("agent-execution-input").value || "{}");
  } catch (_error) {
    showToast("Input JSON 형식을 확인해 주세요.", "error");
    return;
  }
  const body = {
    capability: byID("agent-execution-capability").value,
    action: byID("agent-execution-action").value,
    input,
    context: { requested_by: "geon-agent-control" },
  };
  setBusy(form, true, "실행 중...");
  const status = byID("agent-execution-status");
  status.textContent = "EXECUTING";
  status.dataset.status = "pending";
  try {
    const payload = await apiRequest(
      `${API.agents}/${encodeURIComponent(agentName)}/execute`,
      { method: "POST", body: JSON.stringify(body) },
    );
    const executionStatus = text(payload.execution?.status, "completed");
    status.textContent = executionStatus.toUpperCase();
    status.dataset.status = executionStatus;
    byID("agent-execution-run-id").textContent = text(payload.run_id);
    byID("agent-execution-result").textContent = pretty(payload);
    await loadControlRuns();
    showToast(`Agent 실행 완료: ${executionStatus}`, executionStatus === "completed" ? "success" : "warning");
  } catch (error) {
    const payload = error.payload || { message: error.message };
    status.textContent = "FAILED";
    status.dataset.status = "failed";
    byID("agent-execution-run-id").textContent = text(payload.run_id);
    byID("agent-execution-result").textContent = pretty(payload);
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
    await loadFeedbackView();
    await loadControlRuns().catch(() => {});
    showToast("Feedback가 기록되었습니다.", "success");
  } catch (error) {
    byID("feedback-json").textContent = pretty(error.payload || { valid: false, message: error.message });
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

function renderAutomationFeedback(payload) {
  const list = byID("feedback-record-list");
  state.feedbackRecords = Array.isArray(payload.feedback) ? payload.feedback : [];
  list.replaceChildren();
  if (state.feedbackRecords.length === 0) {
    list.append(createElement("p", "empty-state", "기록된 Feedback이 없습니다."));
    return;
  }
  state.feedbackRecords.forEach((record) => {
    const item = createElement("div", "activity-item feedback-record-item");
    const marker = createElement("span", "activity-marker");
    marker.dataset.status = String(record.status || "unknown").toLowerCase();
    const body = createElement("div", "");
    body.append(createElement("strong", "", text(record.correlation_id)));
    body.append(createElement("span", "", `${text(record.status)} · ${text(record.executor)} · Run ${text(record.run_id)}`));
    const time = document.createElement("time");
    time.dateTime = record.received_at || "";
    time.textContent = displayTimestamp(record.received_at);
    const deleteButton = createElement("button", "icon-button danger-icon record-delete-button");
    deleteButton.type = "button";
    deleteButton.setAttribute("data-delete-feedback", text(record.correlation_id));
    deleteButton.title = `${text(record.correlation_id)} 삭제`;
    deleteButton.setAttribute("aria-label", `${text(record.correlation_id)} Feedback 삭제`);
    const deleteIcon = document.createElement("i");
    deleteIcon.setAttribute("data-lucide", "trash-2");
    deleteIcon.setAttribute("aria-hidden", "true");
    deleteButton.append(deleteIcon);
    item.append(marker, body, time, deleteButton);
    list.append(item);
  });
  if (window.lucide) window.lucide.createIcons();
}

function automaticFeedbackEntries(run) {
  if (!run) return [];
  const stageEntries = (run.stages || []).map((stage) => ({
    source: "control_run",
    stage: stage.name,
    status: stage.status,
    reason: stage.reason,
    timestamp: stage.ended_at || stage.started_at,
    details: stage.details || {},
  }));
  const executorEntries = state.feedbackRecords
    .filter((record) => record.run_id === run.run_id)
    .map((record) => ({
      source: "executor_feedback",
      stage: "execution_feedback",
      status: record.status,
      reason: record.message,
      timestamp: record.received_at,
      details: record,
    }));
  const autonomyEntries = state.autonomyEvents
    .filter((event) => event.run_id === run.run_id)
    .map((event) => ({
      source: "autonomy",
      stage: event.stage,
      status: event.status,
      reason: event.reason,
      timestamp: event.timestamp,
      details: event,
    }));
  return [...stageEntries, ...executorEntries, ...autonomyEntries]
    .sort((left, right) => String(left.timestamp).localeCompare(String(right.timestamp)));
}

function renderAutomaticRunFeedback(run) {
  const summary = byID("automatic-feedback-summary");
  const list = byID("automatic-feedback-list");
  const output = byID("automatic-feedback-json");
  populateFeedbackRunOptions(run);
  list.replaceChildren();
  if (!run) {
    summary.textContent = "No ControlRun selected.";
    list.append(createElement("p", "empty-state", "Select a ControlRun to inspect its evidence."));
    output.textContent = pretty({});
    return;
  }

  const guardState = run.generation?.guard_valid === true ? "approved" : run.generation?.guard_valid === false ? "rejected" : "-";
  summary.textContent = [
    `Run ${text(run.run_id)}`,
    `Status ${text(run.status)}`,
    `App version ${text(run.request?.app_version_id)}`,
    `Agent ${text(run.selected_agent?.name)}`,
    `Model ${text(run.generation?.actual_model)}`,
    `Manifest Guard ${guardState}`,
    `Deployment ${text(run.deployment?.deployment_id)}`,
    `Logs ${(run.logs || []).length}`,
  ].join(" | ");
  const executorFeedback = state.feedbackRecords.filter((record) => record.run_id === run.run_id);
  const autonomyEvents = state.autonomyEvents.filter((event) => event.run_id === run.run_id);
  const entries = automaticFeedbackEntries(run);
  entries.forEach((entry) => {
    const row = createElement("div", "activity-item automatic-feedback-entry");
    const marker = createElement("span", "activity-marker");
    marker.dataset.status = String(entry.status || "unknown").toLowerCase();
    const body = createElement("div", "");
    body.append(createElement("strong", "", text(entry.stage)));
    body.append(createElement("span", "", `${text(entry.source)} | ${text(entry.status)} | ${text(entry.reason)}`));
    const time = document.createElement("time");
    time.dateTime = entry.timestamp || "";
    time.textContent = displayTimestamp(entry.timestamp);
    row.append(marker, body, time);
    list.append(row);
  });
  if (entries.length === 0) list.append(createElement("p", "empty-state", "No evidence is available for this ControlRun."));
  output.textContent = pretty({ run, executor_feedback: executorFeedback, autonomy_events: autonomyEvents, entries });
}

async function loadAutomationFeedback() {
  const payload = await apiRequest(API.feedback);
  renderAutomationFeedback(payload);
  return payload;
}

async function loadFeedbackView() {
  const [feedbackPayload, autonomyPayload] = await Promise.all([
    apiRequest(API.feedback),
    apiRequest(API.autonomyEvents),
  ]);
  state.feedbackRecords = Array.isArray(feedbackPayload.feedback) ? feedbackPayload.feedback : [];
  state.autonomyEvents = Array.isArray(autonomyPayload.events) ? autonomyPayload.events : [];
  renderAutomationFeedback(feedbackPayload);
  renderAutomaticRunFeedback(activeControlRun());
}

async function deleteAutomationFeedback(correlationID, button) {
  if (!window.confirm(`Feedback '${correlationID}'을 geon에서 삭제할까요?`)) return;
  button.disabled = true;
  try {
    await apiRequest(`${API.feedback}/${encodeURIComponent(correlationID)}`, { method: "DELETE" });
    await loadFeedbackView();
    showToast("Feedback 기록을 삭제했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
}

async function clearAutomationFeedback() {
  if (!window.confirm("geon에 저장된 실행 Feedback 기록을 모두 삭제할까요?")) return;
  const button = byID("clear-feedback-records");
  button.disabled = true;
  try {
    const payload = await apiRequest(API.feedback, { method: "DELETE" });
    await loadFeedbackView();
    showToast(`${Number(payload.deleted_count || 0)}개의 Feedback 기록을 삭제했습니다.`, "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
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
  const ready = isDeployedRun(activeControlRun());
  byID("autonomy-start").disabled = !ready || busy || Boolean(status.running);
  byID("autonomy-stop").disabled = !ready || busy || !status.running;
  ["autonomy-run-cycle", "autonomy-emergency-stop", "autonomy-refresh", "clear-autonomy-events"].forEach((id) => { byID(id).disabled = !ready || busy; });
  const submit = byID("autonomy-form").querySelector('button[type="submit"]');
  submit.disabled = !ready || busy || Boolean(status.running);
}

function renderPostDeploymentReadiness(run) {
  const ready = isDeployedRun(run);
  const banner = byID("post-deployment-readiness");
  banner.dataset.status = ready ? "ready" : "blocked";
  banner.textContent = ready
    ? `${run.run_id} · ${run.deployment.deployment_id} 연결됨`
    : "먼저 승인된 Manifest를 AppDeploy에 제출하고 배포 완료 상태를 확인하세요.";

  const form = byID("autonomy-form");
  form.elements.run_id.value = ready ? run.run_id : "";
  form.elements.deployment_id.value = ready ? run.deployment.deployment_id : "";
  form.querySelectorAll("button").forEach((button) => {
    button.disabled = !ready || state.autonomyBusy;
  });
  const status = state.autonomyStatus || {};
  byID("autonomy-start").disabled = !ready || state.autonomyBusy || Boolean(status.running);
  byID("autonomy-stop").disabled = !ready || state.autonomyBusy || !status.running;
  ["autonomy-run-cycle", "autonomy-emergency-stop", "autonomy-refresh", "clear-autonomy-events"].forEach((id) => {
    byID(id).disabled = !ready || state.autonomyBusy;
  });
}

function syncAutonomyForm(config) {
  if (!config) return;
  const form = byID("autonomy-form");
  const mode = form.querySelector(`input[name="mode"][value="${config.mode}"]`);
  if (mode) mode.checked = true;
  const values = {
    run_id: config.run_id,
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
  if (forceFormSync || !state.autonomyConfigLoaded) syncAutonomyForm(config);
  renderPostDeploymentReadiness(activeControlRun());
}

function renderAutonomyEvents(payload) {
  const timeline = byID("autonomy-timeline");
  state.autonomyEvents = Array.isArray(payload.events) ? payload.events : [];
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
    const titleRow = createElement("div", "record-title-row");
    const deleteButton = createElement("button", "icon-button danger-icon record-delete-button");
    deleteButton.type = "button";
    deleteButton.setAttribute("data-delete-event", String(entry.sequence));
    deleteButton.title = `Event #${text(entry.sequence)} 삭제`;
    deleteButton.setAttribute("aria-label", `Event #${text(entry.sequence)} 삭제`);
    const deleteIcon = document.createElement("i");
    deleteIcon.setAttribute("data-lucide", "trash-2");
    deleteIcon.setAttribute("aria-hidden", "true");
    deleteButton.append(deleteIcon);
    titleRow.append(status, deleteButton);
    body.append(titleRow, createElement("span", "", `${text(entry.reason)} · Run ${text(entry.run_id)}`));
    const details = document.createElement("details");
    details.append(createElement("summary", "", "JSON"), createElement("pre", "", pretty(entry)));
    row.append(timeElement, stage, body, details);
    timeline.append(row);
  });
  if (window.lucide) window.lucide.createIcons();
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
    run_id: String(data.get("run_id") || "").trim(),
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

async function deleteAutonomyEvent(sequence, button) {
  if (!window.confirm(`Autonomy Event #${sequence}을 삭제할까요?`)) return;
  button.disabled = true;
  try {
    await apiRequest(`${API.autonomyEvents}/${encodeURIComponent(sequence)}`, { method: "DELETE" });
    await loadAutonomy();
    showToast(`Autonomy Event #${sequence}을 삭제했습니다.`, "success");
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
}

async function refreshDashboard() {
  const button = byID("refresh-button");
  button.disabled = true;
  try {
    const requests = [loadHealth(), loadAgents(), loadControlRuns()];
    if (state.activeView === "autonomy") requests.push(loadAutonomy());
    if (state.activeView === "feedback") requests.push(loadFeedbackView());
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
  byID("clear-history").addEventListener("click", () => void clearControlRuns());
  byID("control-run-list").addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-delete-run]");
    if (deleteButton) {
      void deleteControlRun(deleteButton.dataset.deleteRun, deleteButton);
      return;
    }
    const selectButton = event.target.closest("[data-select-run]");
    if (!selectButton) return;
    void selectControlRun(selectButton.dataset.selectRun).catch((error) => showToast(error.message, "error"));
  });
  byID("agent-table-body").addEventListener("click", (event) => {
    const executeButton = event.target.closest("[data-execute-agent]");
    if (executeButton) {
      openAgentExecution(executeButton.dataset.executeAgent);
      return;
    }
    const deleteButton = event.target.closest("[data-delete-agent]");
    if (!deleteButton) return;
    void deleteRuntimeAgent(deleteButton.dataset.deleteAgent, deleteButton);
  });
  byID("planner-form").addEventListener("submit", submitPlanner);
  byID("planner-submit").addEventListener("click", () => void submitPlannerRun());
  byID("action-form").addEventListener("submit", submitAction);
  byID("action-validation-form").addEventListener("submit", submitActionValidation);
  byID("feedback-form").addEventListener("submit", submitFeedback);
  byID("feedback-run-id").addEventListener("change", (event) => {
    const run = state.controlRuns.find((entry) => entry.run_id === event.currentTarget.value);
    setActiveControlRun(run || null);
  });
  byID("feedback-record-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-feedback]");
    if (!button) return;
    void deleteAutomationFeedback(button.dataset.deleteFeedback, button);
  });
  byID("clear-feedback-records").addEventListener("click", () => void clearAutomationFeedback());
  byID("autonomy-form").addEventListener("submit", submitAutonomyConfig);
  byID("autonomy-run-id").addEventListener("change", (event) => {
    const run = state.controlRuns.find((entry) => entry.run_id === event.currentTarget.value);
    setActiveControlRun(run || null);
  });
  byID("autonomy-start").addEventListener("click", () => runAutonomyControl(API.autonomyStart, "Autonomy loop를 시작했습니다."));
  byID("autonomy-stop").addEventListener("click", () => runAutonomyControl(API.autonomyStop, "Autonomy loop를 중지했습니다."));
  byID("autonomy-run-cycle").addEventListener("click", () => runAutonomyControl(API.autonomyCycles, "Autonomy cycle을 실행했습니다."));
  byID("autonomy-emergency-stop").addEventListener("click", () => {
    if (!window.confirm("Autonomy loop를 즉시 중지하고 Monitor Only로 전환할까요?")) return;
    void runAutonomyControl(API.autonomyEmergencyStop, "Emergency Stop이 적용되었습니다.");
  });
  byID("clear-autonomy-events").addEventListener("click", () => void clearAutonomyEvents());
  byID("autonomy-timeline").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-event]");
    if (!button) return;
    void deleteAutonomyEvent(button.dataset.deleteEvent, button);
  });
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
  byID("agent-execution-form").addEventListener("submit", submitAgentExecution);
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
  switchView(state.activeView);
  const savedAppVersion = localStorage.getItem(APP_VERSION_KEY);
  if (savedAppVersion) byID("planner-form").elements.app_version_id.value = savedAppVersion;
  if (window.lucide) window.lucide.createIcons();
  await refreshDashboard();
}

void initialize();
