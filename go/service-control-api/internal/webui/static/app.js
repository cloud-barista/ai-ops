"use strict";

const API = Object.freeze({
  health: "/healthz",
  agents: "/api/v1/agents",
  applicationContexts: "/api/v1/agent-control/application-contexts",
  resourceRecommendations: "/api/v1/agent-control/resource-recommendations",
  deploymentStatus: "/api/v1/agent-control/deployment-status",
  optimizationFeedback: "/api/v1/agent-control/optimization-feedback",
  agentControlFlows: "/api/v1/agent-control/flows",
});

const VIEW_LABELS = Object.freeze({
  "agent-control": ["KHU AUTOMATION AGENT", "AI 응용 자동화 에이전트"],
  agents: ["AGENT REGISTRY POLICY", "Agent 및 정책"],
  results: ["DECISION EVIDENCE", "실험 결과"],
});

const APPLICATION_CONTEXT_SAMPLE = Object.freeze({
  contract_version: "1.0",
  message_id: "msg-context-demo-001",
  message_type: "application.context.created",
  occurred_at: "2026-07-29T03:00:00Z",
  correlation_id: "flow-demo-001",
  trace_id: "trace-demo-001",
  source: { system: "khu-ai-app", component: "application-profile-generator" },
  target: { system: "khu-agent-control", component: "automation-agent" },
  data: {
    application_profile: {
      profile_id: "profile-demo-001",
      app_id: "chat-service",
      app_version: "1.0.0",
      workload: {
        task_type: "LLM_INFERENCE",
        request_pattern: "ONLINE",
        expected_rps: 5,
      },
      requirements: {
        compute: {
          cpu_cores_min: 8,
          memory_mib_min: 32768,
          storage_gib_min: 100,
        },
        accelerator: {
          required: true,
          type: "GPU",
          count_min: 1,
          memory_mib_min_per_device: 24576,
        },
        deployment: {
          replicas_min: 1,
          replicas_max: 2,
          isolation: "ONE_MAJOR_APP_PER_VM",
        },
        slo: {
          latency_p95_ms_max: 2000,
          throughput_rps_min: 5,
        },
        cost: {
          currency: "KRW",
          cost_per_hour_max: 3000,
        },
      },
      analysis: {
        confidence: 0.91,
        assumptions: [],
        missing_fields: [],
        warnings: [],
      },
    },
    model_recommendation: {
      recommendation_id: "model-rec-demo-001",
      selected_model: {
        model_id: "qwen2.5-7b-instruct",
        model_version: "1",
        source: "HUGGING_FACE",
      },
      inference_configuration: {
        runtime_engine: "VLLM",
        precision: "FP16",
        max_batch_size: 8,
        max_concurrency: 20,
        tensor_parallel_size: 1,
        replicas: 1,
      },
    },
  },
});

const RESOURCE_RECOMMENDATION_SAMPLE = Object.freeze({
  contract_version: "1.0",
  message_id: "msg-resource-demo-001",
  message_type: "resource.recommendation.created",
  occurred_at: "2026-07-29T03:01:00Z",
  correlation_id: "flow-demo-001",
  trace_id: "trace-demo-001",
  causation_id: "msg-context-demo-001",
  source: { system: "khu-resource-service", component: "resource-recommender" },
  target: { system: "khu-agent-control", component: "automation-agent" },
  data: {
    resource_recommendation: {
      recommendation_id: "resource-rec-demo-001",
      profile_id: "profile-demo-001",
      snapshot_id: "snapshot-demo-001",
      status: "FOUND",
      selected_candidate_id: "candidate-demo-001",
      candidates: [
        {
          candidate_id: "candidate-demo-001",
          rank: 1,
          feasible: true,
          desired_infrastructure: {
            node_count: 1,
            cpu_cores_per_node: 8,
            memory_mib_per_node: 32768,
            storage_gib_per_node: 100,
            accelerator: {
              type: "GPU",
              count: 1,
              memory_mib_min_per_device: 24576,
            },
            isolation: "ONE_MAJOR_APP_PER_VM",
          },
          resource_hints: ["vm-gpu-01"],
          scores: {
            resource_fit: 0.96,
            slo_headroom: 0.84,
            cost_efficiency: 0.83,
            availability: 0.98,
            total: 0.9,
          },
          rejection_reasons: [],
        },
      ],
    },
  },
});

const state = {
  flows: [],
  agents: [],
  activeFlowID: "",
  activeView: "agent-control",
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

function displayTimestamp(value, fallback = "-") {
  if (!value || String(value).startsWith("0001-01-01")) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return date.toLocaleString("ko-KR", { hour12: false });
}

function createElement(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined) element.textContent = content;
  return element;
}

function showToast(message, tone = "info") {
  const titles = {
    success: "완료",
    warning: "확인 필요",
    error: "요청 실패",
    info: "알림",
  };
  const toast = createElement("div", `toast is-${tone}`);
  const body = createElement("div");
  body.append(
    createElement("strong", "", titles[tone] || titles.info),
    createElement("span", "", message),
  );
  toast.append(body);
  byID("toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function setBusy(form, busy, label) {
  const button = form.querySelector('button[type="submit"]');
  if (!button) return;
  const span = button.querySelector("span");
  if (span) {
    if (!span.dataset.defaultLabel) span.dataset.defaultLabel = span.textContent;
    span.textContent = busy ? label : span.dataset.defaultLabel;
  } else {
    if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent;
    button.textContent = busy ? label : button.dataset.defaultLabel;
  }
  button.disabled = busy;
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
    const message =
      payload.message ||
      payload.error?.message ||
      `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function parseProtocolMessage(inputID, label) {
  const raw = byID(inputID).value.trim();
  if (!raw) throw new Error(`${label} JSON을 입력하세요.`);
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`${label} JSON 형식이 올바르지 않습니다: ${error.message}`);
  }
}

function loadAgentControlSamples() {
  byID("application-context-json").value = pretty(APPLICATION_CONTEXT_SAMPLE);
  byID("resource-recommendation-json").value = pretty(RESOURCE_RECOMMENDATION_SAMPLE);
}

function activeFlow() {
  return state.flows.find((flow) => flow.correlation_id === state.activeFlowID) || null;
}

function desiredDeploymentSpec(flow) {
  return (
    flow?.deployment_request?.data?.deployment_request?.deployment_manifest ||
    null
  );
}

function renderAgentControlStages(flow) {
  const complete = {
    application: Boolean(flow?.application_context),
    resource: Boolean(flow?.resource_recommendation),
    authorization: Boolean(flow?.agent_authorization),
    planner: Boolean(flow?.decision),
    guard: Boolean(flow?.guard),
    manifest: Boolean(flow?.deployment_request),
  };
  const blocked = new Set();
  if (flow?.state === "AGENT_AUTHORIZATION_REJECTED") {
    ["authorization", "planner", "guard", "manifest"].forEach((stage) =>
      blocked.add(stage),
    );
  } else if (flow?.state === "REJECTED") {
    ["guard", "manifest"].forEach((stage) => blocked.add(stage));
  } else if (flow?.state === "RETRY_REQUIRED") {
    blocked.add("manifest");
  }

  document.querySelectorAll("[data-agent-control-stage]").forEach((item) => {
    const stage = item.dataset.agentControlStage;
    item.classList.toggle("is-complete", complete[stage]);
    item.classList.toggle("is-blocked", blocked.has(stage));
    item.classList.toggle(
      "is-waiting",
      !complete[stage] && !blocked.has(stage),
    );
  });
}

function renderReasoningComparison(comparison) {
  if (!comparison) {
    byID("reasoning-rule-action").textContent = "-";
    byID("reasoning-simple-action").textContent = "-";
    byID("reasoning-validated-action").textContent = "-";
    byID("reasoning-comparison-json").textContent =
      "Flow를 선택한 뒤 비교 실험을 실행하세요.";
    return;
  }
  const rule = comparison.rule_based || {};
  const simple = comparison.simple_inference || {};
  const validated = comparison.validated_inference || {};
  byID("reasoning-rule-action").textContent = text(rule.action);
  byID("reasoning-simple-action").textContent = simple.action
    ? `${simple.action} · ${text(simple.execution_status)}`
    : text(simple.execution_status);
  byID("reasoning-validated-action").textContent = validated.action
    ? `${validated.action} · ${text(validated.guard_status)}`
    : text(validated.execution_status);
  byID("reasoning-comparison-json").textContent = pretty(comparison);
}

function renderFeedback(flow) {
  const summary = flow?.feedback_summary;
  const summaryElement = byID("agent-control-feedback-summary");
  summaryElement.replaceChildren();
  if (!summary) {
    summaryElement.append(
      createElement("span", "", "배포 결과"),
      createElement("strong", "", "아직 수신되지 않았습니다."),
    );
    byID("agent-control-feedback-json").textContent =
      "배포 상태와 성능 Feedback이 같은 Flow에 기록됩니다.";
    return;
  }
  summaryElement.append(
    createElement(
      "span",
      "",
      `${summary.success ? "성공" : "확인 필요"} · ${text(summary.deployment_state)}`,
    ),
    createElement("strong", "", text(summary.cause)),
  );
  byID("agent-control-feedback-json").textContent = pretty({
    deployment_status: flow.deployment_status,
    optimization_feedback: flow.optimization_feedback,
    feedback_summary: summary,
    scaling_decision: flow.scaling_decision,
  });
}

function scalingSummary(flow) {
  const scaling = flow?.scaling_decision;
  if (!scaling) {
    return "배포 상태와 성능 Feedback을 기다리고 있습니다.";
  }
  return `${text(scaling.action)} · ${text(scaling.current_replicas)} → ${text(
    scaling.desired_replicas,
  )} · ${text(scaling.reason)}`;
}

function renderExperimentDetail(flow) {
  if (!flow) {
    byID("experiment-decision-summary").textContent = "Flow를 선택하세요.";
    byID("experiment-scaling-summary").textContent =
      "배포 상태와 성능 Feedback을 기다리고 있습니다.";
    byID("experiment-flow-json").textContent = "{}";
    renderReasoningComparison(null);
    renderFeedback(null);
    return;
  }
  const decision = flow.decision || {};
  const guard = flow.guard || {};
  byID("experiment-decision-summary").textContent =
    `${text(decision.action, flow.state)} · Guard ${text(guard.status)}`;
  byID("experiment-scaling-summary").textContent = scalingSummary(flow);
  byID("experiment-flow-json").textContent = pretty(flow);
  renderReasoningComparison(flow.reasoning_comparison);
  renderFeedback(flow);
}

function renderAgentControlFlow(flow) {
  if (!flow) {
    byID("agent-control-flow-id").textContent = "실행 대기";
    byID("agent-control-status").textContent = "WAITING";
    byID("agent-control-authorization").textContent = "-";
    byID("agent-control-action").textContent = "-";
    byID("agent-control-guard").textContent = "-";
    byID("agent-control-candidate").textContent = "-";
    byID("agent-control-reason").textContent =
      "두 입력을 확인한 뒤 배포 판단을 실행하세요.";
    byID("agent-control-result-json").textContent =
      "아직 실행 결과가 없습니다.";
    renderAgentControlStages(null);
    renderExperimentDetail(null);
    return;
  }

  state.activeFlowID = flow.correlation_id;
  const authorization = flow.agent_authorization || {};
  const decision = flow.decision || {};
  const guard = flow.guard || {};
  const correction = decision.correction_request;
  byID("agent-control-flow-id").textContent =
    flow.correlation_id || "ID 없음";
  byID("agent-control-status").textContent = text(flow.state);
  byID("agent-control-status").dataset.status = String(
    flow.state || "",
  ).toLowerCase();
  byID("agent-control-authorization").textContent =
    authorization.authorized === true
      ? "승인"
      : authorization.authorized === false
        ? "거부"
        : "-";
  byID("agent-control-action").textContent = text(decision.action);
  byID("agent-control-guard").textContent = text(guard.status);
  byID("agent-control-candidate").textContent = text(
    decision.selected_candidate_id,
  );
  byID("agent-control-reason").textContent =
    authorization.authorized === false
      ? text(authorization.reason)
      : correction
        ? `${text(decision.reason)} 수정 대상: ${text(correction.target)}`
        : text(decision.reason, "두 입력을 기다리고 있습니다.");
  byID("agent-control-result-json").textContent = pretty({
    correlation_id: flow.correlation_id,
    trace_id: flow.trace_id,
    profile_id: flow.profile_id,
    state: flow.state,
    agent_authorization: flow.agent_authorization,
    decision: flow.decision,
    guard: flow.guard,
    desired_deployment_spec: desiredDeploymentSpec(flow),
  });
  renderAgentControlStages(flow);
  renderExperimentDetail(flow);
}

function validateJoinedInputs(applicationContext, resourceRecommendation) {
  if (applicationContext.correlation_id !== resourceRecommendation.correlation_id) {
    throw new Error("두 입력의 correlation_id가 같아야 합니다.");
  }
  const applicationProfileID =
    applicationContext.data?.application_profile?.profile_id;
  const resourceProfileID =
    resourceRecommendation.data?.resource_recommendation?.profile_id;
  if (!applicationProfileID || applicationProfileID !== resourceProfileID) {
    throw new Error("두 입력의 profile_id가 같아야 합니다.");
  }
}

async function submitAutomationFlow(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true, "판단 중...");
  try {
    const applicationContext = parseProtocolMessage(
      "application-context-json",
      "Application Context",
    );
    const resourceRecommendation = parseProtocolMessage(
      "resource-recommendation-json",
      "Resource Recommendation",
    );
    validateJoinedInputs(applicationContext, resourceRecommendation);

    await apiRequest(API.applicationContexts, {
      method: "POST",
      body: JSON.stringify(applicationContext),
    });
    const flow = await apiRequest(API.resourceRecommendations, {
      method: "POST",
      body: JSON.stringify(resourceRecommendation),
    });
    upsertFlow(flow);
    renderAgentControlFlow(flow);
    renderExperimentFlows();
    showToast(
      `${text(flow.decision?.action, flow.state)} 결정이 생성되었습니다.`,
      flow.state === "DEPLOY_APPROVED" ? "success" : "warning",
    );
  } catch (error) {
    byID("agent-control-result-json").textContent = pretty(
      error.payload || { message: error.message },
    );
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

function upsertFlow(flow) {
  const index = state.flows.findIndex(
    (item) => item.correlation_id === flow.correlation_id,
  );
  if (index >= 0) {
    state.flows[index] = flow;
  } else {
    state.flows.push(flow);
  }
  state.flows.sort(
    (left, right) =>
      new Date(right.updated_at || 0) - new Date(left.updated_at || 0),
  );
  state.activeFlowID = flow.correlation_id;
}

async function loadAgentControlFlows(preferredID = state.activeFlowID) {
  try {
    const payload = await apiRequest(API.agentControlFlows);
    state.flows = Array.isArray(payload.flows) ? payload.flows : [];
    state.flows.sort(
      (left, right) =>
        new Date(right.updated_at || 0) - new Date(left.updated_at || 0),
    );
    const selected =
      state.flows.find((flow) => flow.correlation_id === preferredID) ||
      state.flows[0] ||
      null;
    state.activeFlowID = selected?.correlation_id || "";
    renderExperimentFlows();
    if (selected) renderAgentControlFlow(selected);
    return selected;
  } catch (error) {
    showToast(error.message, "error");
    return null;
  }
}

function renderExperimentFlows() {
  const container = byID("experiment-flow-list");
  container.replaceChildren();
  if (!state.flows.length) {
    container.append(
      createElement("p", "empty-state", "저장된 실험 결과가 없습니다."),
    );
    renderExperimentDetail(null);
    return;
  }

  for (const flow of state.flows) {
    const row = createElement(
      "div",
      `flow-list-item${flow.correlation_id === state.activeFlowID ? " is-active" : ""}`,
    );
    const select = createElement("button", "flow-select");
    select.type = "button";
    select.dataset.flowID = flow.correlation_id;
    select.append(
      createElement("strong", "", flow.correlation_id),
      createElement(
        "span",
        "",
        `${text(flow.decision?.action, flow.state)} · ${displayTimestamp(flow.updated_at)}`,
      ),
    );
    const remove = createElement("button", "icon-button danger-icon");
    remove.type = "button";
    remove.dataset.deleteFlow = flow.correlation_id;
    remove.title = "실험 기록 삭제";
    remove.setAttribute("aria-label", `${flow.correlation_id} 삭제`);
    remove.innerHTML = '<i data-lucide="trash-2" aria-hidden="true"></i>';
    row.append(select, remove);
    container.append(row);
  }
  window.lucide?.createIcons();
  renderExperimentDetail(activeFlow());
}

async function deleteAgentControlFlow(correlationID) {
  try {
    await apiRequest(
      `${API.agentControlFlows}/${encodeURIComponent(correlationID)}`,
      { method: "DELETE" },
    );
    if (state.activeFlowID === correlationID) state.activeFlowID = "";
    await loadAgentControlFlows();
    showToast("실험 기록을 삭제했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function clearAgentControlFlows() {
  if (!state.flows.length) {
    showToast("삭제할 실험 기록이 없습니다.", "info");
    return;
  }
  try {
    await apiRequest(API.agentControlFlows, { method: "DELETE" });
    state.flows = [];
    state.activeFlowID = "";
    renderExperimentFlows();
    renderAgentControlFlow(null);
    showToast("전체 실험 기록을 삭제했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderAgents() {
  const body = byID("agent-table-body");
  body.replaceChildren();
  byID("agent-count").textContent = String(state.agents.length);
  if (!state.agents.length) {
    const row = document.createElement("tr");
    const cell = createElement("td", "empty-cell", "등록된 Agent가 없습니다.");
    cell.colSpan = 7;
    row.append(cell);
    body.append(row);
    return;
  }

  for (const agent of state.agents) {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.append(
      createElement("strong", "", agent.name),
      createElement("small", "", agent.korean_name || agent.version || ""),
    );
    const source = createElement(
      "span",
      `source-badge is-${agent.source || "configuration"}`,
      agent.source === "runtime" ? "runtime" : "configuration",
    );
    const sourceCell = document.createElement("td");
    sourceCell.append(source);
    const manageCell = document.createElement("td");
    if (agent.source === "runtime") {
      const remove = createElement("button", "icon-button danger-icon");
      remove.type = "button";
      remove.dataset.deleteAgent = agent.name;
      remove.title = "Runtime Agent 삭제";
      remove.setAttribute("aria-label", `${agent.name} 삭제`);
      remove.innerHTML = '<i data-lucide="trash-2" aria-hidden="true"></i>';
      manageCell.append(remove);
    } else {
      manageCell.textContent = "정책 파일";
    }
    [
      nameCell,
      sourceCell,
      createElement("td", "", agent.role),
      createElement("td", "list-cell", (agent.capabilities || []).join(", ")),
      createElement("td", "list-cell", (agent.bounded_actions || []).join(", ")),
      createElement(
        "td",
        agent.enabled ? "status-enabled" : "status-disabled",
        agent.enabled ? "enabled" : "disabled",
      ),
      manageCell,
    ].forEach((cell) => row.append(cell));
    body.append(row);
  }
  window.lucide?.createIcons();
}

async function loadAgents() {
  try {
    const payload = await apiRequest(API.agents);
    state.agents = Array.isArray(payload.agents) ? payload.agents : [];
    renderAgents();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function submitAgentRegistration(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true, "등록 중...");
  try {
    const data = new FormData(form);
    const enabled = true;
    await apiRequest(API.agents, {
      method: "POST",
      body: JSON.stringify({
        name: data.get("name"),
        version: data.get("version"),
        role: data.get("role"),
        endpoint: data.get("endpoint"),
        invocation_path: data.get("invocation_path"),
        auth_token_env: data.get("auth_token_env"),
        capabilities: parseList(data.get("capabilities")),
        bounded_actions: parseList(data.get("bounded_actions")),
        responsibilities: [],
        reward_signals: [],
        enabled,
      }),
    });
    byID("agent-dialog").close();
    await loadAgents();
    showToast("Runtime Agent를 등록했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function deleteAgent(name) {
  try {
    await apiRequest(`${API.agents}/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    await loadAgents();
    showToast(`${name} Agent를 삭제했습니다.`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function runReasoningComparison(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const flow = activeFlow();
  if (!flow?.correlation_id || !flow?.decision) {
    showToast("먼저 배포 판단 Flow를 선택하세요.", "warning");
    return;
  }
  setBusy(form, true, "비교 중...");
  try {
    const candidateID = form.elements.candidate_id.value.trim();
    const comparison = await apiRequest(
      `${API.agentControlFlows}/${encodeURIComponent(
        flow.correlation_id,
      )}/reasoning-comparisons`,
      {
        method: "POST",
        body: JSON.stringify({ candidate_id: candidateID }),
      },
    );
    renderReasoningComparison(comparison);
    await loadAgentControlFlows(flow.correlation_id);
    const unavailable =
      comparison.simple_inference?.execution_status === "provider_unavailable";
    showToast(
      unavailable
        ? "Qwen 연결을 확인하세요. 규칙 기반 결과는 기록됐습니다."
        : "규칙 기반, Qwen, Guard 결과를 비교했습니다.",
      unavailable ? "warning" : "success",
    );
  } catch (error) {
    byID("reasoning-comparison-json").textContent = pretty(
      error.payload || { message: error.message },
    );
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

function buildFeedbackSamples(flow = activeFlow()) {
  const correlationID = flow?.correlation_id || "flow-demo-001";
  const traceID = flow?.trace_id || "trace-demo-001";
  const decisionID =
    flow?.decision?.decision_id || `decision-${correlationID}`;
  const deploymentID =
    flow?.deployment_status?.data?.deployment_status?.deployment_id ||
    `deployment-${correlationID}`;
  const now = new Date();
  const startedAt = new Date(now.getTime() - 20 * 60 * 1000).toISOString();
  const endedAt = now.toISOString();
  const statusMessageID = `msg-deployment-status-${correlationID}`;
  return {
    status: {
      contract_version: "1.0",
      message_id: statusMessageID,
      message_type: "deployment.status.changed",
      occurred_at: startedAt,
      correlation_id: correlationID,
      trace_id: traceID,
      causation_id:
        flow?.deployment_request?.message_id ||
        `msg-deploy-request-${correlationID}`,
      source: {
        system: "deployment-orchestrator",
        component: "runtime-adapter",
      },
      target: { system: "khu-agent-control", component: "automation-agent" },
      data: {
        deployment_status: {
          deployment_id: deploymentID,
          decision_id: decisionID,
          state: "RUNNING",
          actual_infrastructure: {
            provider: "MOCK",
            region: "kr-central-1",
            resource_ids: ["vm-gpu-01"],
          },
          message: "응용이 실행 중입니다.",
          updated_at: startedAt,
        },
      },
    },
    feedback: {
      contract_version: "1.0",
      message_id: `msg-optimization-feedback-${correlationID}`,
      message_type: "optimization.feedback.created",
      occurred_at: endedAt,
      correlation_id: correlationID,
      trace_id: traceID,
      causation_id: statusMessageID,
      source: { system: "deployment-orchestrator", component: "monitoring" },
      target: { system: "khu-agent-control", component: "automation-agent" },
      data: {
        optimization_feedback: {
          feedback_id: `feedback-${correlationID}`,
          decision_id: decisionID,
          deployment_id: deploymentID,
          outcome: "SUCCEEDED",
          observation_window: {
            started_at: startedAt,
            ended_at: endedAt,
          },
          metrics: {
            resource: {
              cpu_average_percent: 81.2,
              memory_peak_mib: 28600,
              accelerator_average_percent: 91.5,
              accelerator_memory_peak_mib: 22900,
            },
            inference: {
              latency_p95_ms: 2600,
              throughput_rps: 6.2,
              error_rate_percent: 0.2,
            },
            cost: {
              currency: "KRW",
              estimated_cost: 833.33,
            },
          },
          slo_violations: ["latency_p95_ms"],
          created_at: endedAt,
        },
      },
    },
  };
}

function loadFeedbackSamples() {
  const samples = buildFeedbackSamples();
  byID("deployment-status-json").value = pretty(samples.status);
  byID("optimization-feedback-json").value = pretty(samples.feedback);
}

async function submitDeploymentStatus(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true, "전송 중...");
  try {
    const body = parseProtocolMessage(
      "deployment-status-json",
      "Deployment Status",
    );
    const flow = await apiRequest(API.deploymentStatus, {
      method: "POST",
      body: JSON.stringify(body),
    });
    upsertFlow(flow);
    renderAgentControlFlow(flow);
    renderExperimentFlows();
    showToast("배포 상태를 Flow에 연결했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

async function submitOptimizationFeedback(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true, "판단 중...");
  try {
    const body = parseProtocolMessage(
      "optimization-feedback-json",
      "Optimization Feedback",
    );
    const flow = await apiRequest(API.optimizationFeedback, {
      method: "POST",
      body: JSON.stringify(body),
    });
    upsertFlow(flow);
    renderAgentControlFlow(flow);
    renderExperimentFlows();
    showToast(
      `스케일링 판단: ${text(flow.scaling_decision?.action, "NO_ACTION")}`,
      "success",
    );
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}

function switchView(view) {
  if (!VIEW_LABELS[view]) return;
  state.activeView = view;
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.viewTarget === view);
  });
  document.querySelectorAll("[data-view]").forEach((section) => {
    const active = section.dataset.view === view;
    section.hidden = !active;
    section.classList.toggle("is-active", active);
  });
  byID("view-eyebrow").textContent = VIEW_LABELS[view][0];
  byID("view-title").textContent = VIEW_LABELS[view][1];
  if (view === "agents") loadAgents();
  if (view === "results") loadAgentControlFlows();
}

async function refreshHealth() {
  const pill = byID("health-pill");
  const sidebarDot = byID("sidebar-health-dot");
  try {
    await apiRequest(API.health);
    pill.classList.add("is-healthy");
    pill.classList.remove("is-error");
    pill.querySelector("span:last-child").textContent = "정상";
    sidebarDot.classList.add("is-healthy");
  } catch (_error) {
    pill.classList.add("is-error");
    pill.classList.remove("is-healthy");
    pill.querySelector("span:last-child").textContent = "연결 오류";
    sidebarDot.classList.remove("is-healthy");
  }
}

async function refreshCurrentView() {
  await refreshHealth();
  if (state.activeView === "agents") await loadAgents();
  if (state.activeView === "results") await loadAgentControlFlows();
}

function bindEvents() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.viewTarget));
  });
  byID("refresh-button").addEventListener("click", refreshCurrentView);
  byID("automation-flow-form").addEventListener(
    "submit",
    submitAutomationFlow,
  );
  byID("load-agent-control-sample").addEventListener("click", () => {
    loadAgentControlSamples();
    renderAgentControlFlow(null);
  });
  byID("refresh-agents").addEventListener("click", loadAgents);
  byID("open-agent-dialog").addEventListener("click", () =>
    byID("agent-dialog").showModal(),
  );
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  byID("agent-registration-form").addEventListener(
    "submit",
    submitAgentRegistration,
  );
  byID("agent-table-body").addEventListener("click", (event) => {
    const button = event.target.closest("[data-delete-agent]");
    if (button) deleteAgent(button.dataset.deleteAgent);
  });
  byID("refresh-experiment-flows").addEventListener(
    "click",
    () => loadAgentControlFlows(),
  );
  byID("clear-experiment-flows").addEventListener(
    "click",
    clearAgentControlFlows,
  );
  byID("experiment-flow-list").addEventListener("click", (event) => {
    const remove = event.target.closest("[data-delete-flow]");
    if (remove) {
      deleteAgentControlFlow(remove.dataset.deleteFlow);
      return;
    }
    const select = event.target.closest("[data-flow-id]");
    if (!select) return;
    state.activeFlowID = select.dataset.flowId;
    renderExperimentFlows();
    renderAgentControlFlow(activeFlow());
    loadFeedbackSamples();
  });
  byID("reasoning-comparison-form").addEventListener(
    "submit",
    runReasoningComparison,
  );
  byID("load-agent-control-feedback-sample").addEventListener(
    "click",
    loadFeedbackSamples,
  );
  byID("deployment-status-form").addEventListener(
    "submit",
    submitDeploymentStatus,
  );
  byID("optimization-feedback-form").addEventListener(
    "submit",
    submitOptimizationFeedback,
  );
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = byID(button.dataset.copyTarget);
      try {
        await navigator.clipboard.writeText(target.textContent);
        showToast("JSON을 복사했습니다.", "success");
      } catch (_error) {
        showToast("브라우저에서 클립보드 권한을 허용하세요.", "warning");
      }
    });
  });
}

async function initialize() {
  bindEvents();
  loadAgentControlSamples();
  loadFeedbackSamples();
  renderAgentControlFlow(null);
  switchView("agent-control");
  window.lucide?.createIcons();
  await Promise.all([refreshHealth(), loadAgentControlFlows(), loadAgents()]);
}

document.addEventListener("DOMContentLoaded", initialize);
