"use strict";

const API = Object.freeze({
  health: "/healthz",
  agents: "/api/v1/agents",
  trustedAutomationRuns: "/api/v1/agent-control/trusted-automation-runs",
  applicationContexts: "/api/v1/agent-control/application-contexts",
  resourceRecommendations: "/api/v1/agent-control/resource-recommendations",
  deploymentStatus: "/api/v1/agent-control/deployment-status",
  optimizationFeedback: "/api/v1/agent-control/optimization-feedback",
  agentControlFlows: "/api/v1/agent-control/flows",
});

const VIEW_LABELS = Object.freeze({
  "agent-control": ["KHU AUTOMATION AGENT", "AI 응용 배포 자동화"],
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

const STRUCTURED_APP_SPEC_SAMPLE = Object.freeze({
  app_id: "structured-ai-service",
  app_version: "1.0.0",
  workload_type: "LLM_INFERENCE",
  cpu_cores: 4,
  memory_mib: 8192,
  storage_gib: 20,
  accelerator_type: "GPU",
  accelerator_count: 1,
  accelerator_memory_mib: 16384,
  replicas_min: 1,
  replicas_max: 1,
});

const state = {
  flows: [],
  agents: [],
  eligibleDecisionAgents: [],
  eligibleOperationAgents: [],
  agentDefaults: {},
  activeFlowID: "",
  activeAutomationRun: null,
  activeTrustedResult: null,
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

function setAutomationInputMode(mode) {
  document
    .querySelectorAll("[data-automation-input-section]")
    .forEach((section) => {
      const active = section.dataset.automationInputSection === mode;
      section.hidden = !active;
      section.querySelectorAll("textarea, input").forEach((field) => {
        field.disabled = !active;
        field.required = active;
      });
    });
}

function buildAutomationRunPayload() {
  const decisionAgent = byID("decision-agent-select").value.trim();
  if (!decisionAgent) {
    throw new Error("배포 판단 Agent를 선택하세요.");
  }
  const mode = document.querySelector(
    'input[name="automation_input_mode"]:checked',
  )?.value;
  if (mode === "structured") {
    return {
      input_type: "structured",
      requested_by: "geon-web",
      decision_agent: decisionAgent,
      app_spec: parseProtocolMessage(
        "automation-app-spec-json",
        "구조화 App Spec",
      ),
    };
  }
  const request = byID("automation-request").value.trim();
  if (!request) {
    throw new Error("배포·운영 요구사항을 입력하세요.");
  }
  return {
    input_type: "natural_language",
    request,
    requested_by: "geon-web",
    decision_agent: decisionAgent,
  };
}

function activeFlow() {
  return state.flows.find((flow) => flow.correlation_id === state.activeFlowID) || null;
}

function desiredDeploymentSpec(flow) {
  return (
    flow?.desired_deployment_spec ||
    flow?.deployment_request?.data?.deployment_request?.deployment_manifest ||
    null
  );
}

function manifestRevisions(flow) {
  const revisions = Array.isArray(flow?.manifest_revisions)
    ? [...flow.manifest_revisions]
    : [];
  if (!revisions.length && flow?.deployment_request) {
    revisions.push({
      revision: 1,
      phase: "INITIAL",
      trigger_action: "DEPLOY",
      deployment_request: flow.deployment_request,
    });
  }
  return revisions.sort((left, right) => Number(left.revision) - Number(right.revision));
}

function manifestPayload(revision) {
  return (
    revision?.deployment_request?.data?.deployment_request?.deployment_manifest ||
    null
  );
}

function renderManifestOutput(prefix, flow) {
  const revisions = manifestRevisions(flow);
  const flowID = text(flow?.correlation_id, "Flow 미생성");
  const initial = revisions.find((revision) => Number(revision.revision) === 1) || null;
  const optimized = [...revisions]
    .reverse()
    .find((revision) => revision.phase === "OPTIMIZED") || null;
  const initialStatus = byID(`${prefix}manifest-initial-status`);
  const initialJSON = byID(`${prefix}manifest-initial-json`);
  const optimizedStatus = byID(`${prefix}manifest-optimized-status`);
  const optimizedJSON = byID(`${prefix}manifest-optimized-json`);
  byID(`${prefix}manifest-initial-flow-id`).textContent = flowID;
  byID(`${prefix}manifest-optimized-flow-id`).textContent = flowID;

  initialStatus.textContent = initial
    ? `생성 완료 · Revision ${text(initial.revision)}`
    : "생성 대기";
  initialJSON.textContent = initial
    ? pretty(manifestPayload(initial))
    : "DEPLOY 승인 후 생성됩니다.";
  optimizedStatus.textContent = optimized
    ? `생성 완료 · Revision ${text(optimized.revision)} · ${text(optimized.trigger_action)}`
    : "Feedback 후 생성 대기";
  optimizedJSON.textContent = optimized
    ? pretty(manifestPayload(optimized))
    : "승인된 SCALE_OUT 또는 SCALE_IN 판단 후 생성됩니다.";
}

function renderManifestOutputs(flow) {
  renderManifestOutput("", flow);
  renderManifestOutput("experiment-", flow);
}

function renderAgentControlStages(record) {
  const flow = record?.flow || record;
  const linkedRun =
    state.activeAutomationRun?.flow?.correlation_id === flow?.correlation_id
      ? state.activeAutomationRun
      : null;
  const revisions = manifestRevisions(flow);
  const hasInitialManifest = revisions.some(
    (revision) => Number(revision.revision) === 1,
  );
  const hasOptimizedManifest = revisions.some(
    (revision) => revision.phase === "OPTIMIZED",
  );
  const complete = {
    requirement: Boolean(
      record?.requirement_analysis ||
      linkedRun?.requirement_analysis ||
      flow?.application_context,
    ),
    recommendation: Boolean(
      record?.resource_recommendation ||
      linkedRun?.resource_recommendation ||
      flow?.resource_recommendation,
    ),
    decision: Boolean(
      flow?.decision ||
      flow?.agent_execution ||
      flow?.agent_authorization?.authorized === false ||
      record?.status === "COMPLETED",
    ),
    "initial-manifest": hasInitialManifest,
    "deployment-status": Boolean(flow?.deployment_status),
    "optimization-feedback": Boolean(flow?.optimization_feedback),
    operation: Boolean(flow?.operation_agent_execution),
    "optimized-manifest": hasOptimizedManifest,
  };
  const blocked = new Set();
  if (record?.status === "FAILED" && !complete.requirement) {
    [
      "requirement",
      "recommendation",
      "decision",
      "initial-manifest",
      "deployment-status",
      "optimization-feedback",
      "operation",
      "optimized-manifest",
    ].forEach((stage) => blocked.add(stage));
  } else if (record?.status === "FAILED" && !complete.recommendation) {
    [
      "recommendation",
      "decision",
      "initial-manifest",
      "deployment-status",
      "optimization-feedback",
      "operation",
      "optimized-manifest",
    ].forEach((stage) => blocked.add(stage));
  } else if ([
    "AGENT_AUTHORIZATION_REJECTED",
    "AGENT_EXECUTION_FAILED",
    "AGENT_RESULT_REJECTED",
  ].includes(flow?.state)) {
    blocked.add("decision");
    [
      "initial-manifest",
      "deployment-status",
      "optimization-feedback",
      "operation",
      "optimized-manifest",
    ].forEach((stage) => blocked.add(stage));
  } else if (
    flow?.decision?.action &&
    flow.decision.action !== "DEPLOY"
  ) {
    [
      "initial-manifest",
      "deployment-status",
      "optimization-feedback",
      "operation",
      "optimized-manifest",
    ].forEach((stage) => blocked.add(stage));
  } else if (record?.deployment_submission?.status === "FAILED") {
    [
      "initial-manifest",
      "deployment-status",
      "optimization-feedback",
      "operation",
      "optimized-manifest",
    ].forEach((stage) => blocked.add(stage));
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
    requested_operation_agent: flow.requested_operation_agent,
    operation_agent_execution: flow.operation_agent_execution,
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

function renderOperationEvidence(flow) {
  const container = byID("experiment-operation-evidence");
  const execution = flow?.operation_agent_execution;
  if (!execution) {
    container.hidden = false;
    byID("experiment-operation-agent").textContent = "Feedback 전송 대기";
    byID("experiment-operation-request-guard").textContent = "-";
    byID("experiment-operation-result-guard").textContent = "-";
    byID("experiment-operation-scaling-guard").textContent = "-";
    byID("experiment-operation-scaling").textContent = "-";
    byID("experiment-operation-reason").textContent =
      "성능 Feedback 전송 후 생성됩니다.";
    byID("experiment-operation-evidence-list").textContent = "-";
    return;
  }

  const decision = flow.scaling_decision || {};
  const hasRecommendation = Boolean(decision.action);
  container.hidden = false;
  byID("experiment-operation-agent").textContent = [
    text(execution.agent_name),
    text(execution.source),
    text(execution.status),
  ].join(" · ");
  byID("experiment-operation-request-guard").textContent = text(
    execution.request_guard?.status,
  );
  byID("experiment-operation-result-guard").textContent = text(
    execution.result_guard?.status,
  );
  byID("experiment-operation-scaling-guard").textContent = text(
    execution.scaling_guard?.status,
  );
  byID("experiment-operation-scaling").textContent = hasRecommendation
    ? `${text(decision.action)} ${text(decision.current_replicas)} -> ${text(decision.desired_replicas)}`
    : "권고 없음";
  byID("experiment-operation-reason").textContent = text(
    decision.reason,
    "권고 없음",
  );
  byID("experiment-operation-evidence-list").textContent = Array.isArray(
    decision.evidence,
  ) && decision.evidence.length
    ? decision.evidence.join(", ")
    : "권고 없음";
}

function syncFeedbackControls(flow) {
  const statusSubmit = byID("deployment-status-submit");
  const feedbackSubmit = byID("optimization-feedback-submit");
  const statusBlocked = !flow || Boolean(flow.deployment_status);
  statusSubmit.disabled = statusBlocked;
  statusSubmit.toggleAttribute("disabled", statusBlocked);
  statusSubmit.title = !flow
    ? "먼저 전체 실험 실행으로 Revision 1을 생성하세요."
    : flow.deployment_status
      ? "배포 상태가 이미 같은 Flow에 연결되었습니다."
      : "Revision 1에 배포 상태를 연결합니다.";
  const feedbackBlocked = !flow?.deployment_status || Boolean(flow.optimization_feedback);
  feedbackSubmit.disabled = feedbackBlocked;
  feedbackSubmit.toggleAttribute("disabled", feedbackBlocked);
  feedbackSubmit.title = !flow?.deployment_status
    ? "배포 상태를 먼저 전송하세요."
    : flow.optimization_feedback
      ? "성능 Feedback이 이미 같은 Flow에 연결되었습니다."
      : "성능 Feedback을 전송하여 운영 최적화 판단을 실행합니다.";
}

function renderExperimentDetail(flow) {
  syncFeedbackControls(flow);
  if (!flow) {
    byID("experiment-agent-summary").textContent = "Flow를 선택하세요.";
    byID("experiment-decision-summary").textContent = "Flow를 선택하세요.";
    byID("experiment-scaling-summary").textContent =
      "배포 상태와 성능 Feedback을 기다리고 있습니다.";
    byID("experiment-flow-json").textContent = "{}";
    renderReasoningComparison(null);
    renderFeedback(null);
    renderOperationEvidence(null);
    renderManifestOutputs(null);
    loadFeedbackSamples(null);
    return;
  }
  const decision = flow.decision || {};
  const guard = flow.guard || {};
  const execution = flow.agent_execution || {};
  byID("experiment-agent-summary").textContent = execution.agent_name
    ? `${execution.agent_name} · ${text(execution.source)} · ${text(execution.status)}`
    : text(flow.agent_authorization?.agent_name, "Agent 증거 없음");
  byID("experiment-decision-summary").textContent =
    `${text(decision.action, flow.state)} · Guard ${text(guard.status)}`;
  byID("experiment-scaling-summary").textContent = scalingSummary(flow);
  byID("experiment-flow-json").textContent = pretty(flow);
  renderReasoningComparison(flow.reasoning_comparison);
  renderFeedback(flow);
  renderOperationEvidence(flow);
  renderManifestOutputs(flow);
  loadFeedbackSamples(flow);
}

function renderAgentControlFlow(flow) {
  if (!flow) {
    byID("agent-control-flow-id").textContent = "실행 대기";
    byID("agent-control-status").textContent = "WAITING";
    byID("automation-analysis-mode").textContent = "-";
    byID("agent-control-authorization").textContent = "-";
    byID("agent-control-action").textContent = "-";
    byID("agent-control-guard").textContent = "-";
    byID("agent-control-adapter").textContent = "-";
    byID("agent-control-adapter-status").textContent = "-";
    byID("agent-control-candidate").textContent = "-";
    byID("agent-control-agent-name").textContent = "-";
    byID("agent-control-agent-source").textContent = "-";
    byID("agent-control-dispatch-status").textContent = "-";
    byID("agent-control-agent-latency").textContent = "-";
    byID("agent-control-request-guard").textContent = "-";
    byID("agent-control-result-guard").textContent = "-";
    byID("agent-control-reason").textContent =
      "요청을 입력한 뒤 자동 분석 및 판단을 실행하세요.";
    byID("agent-control-result-json").textContent =
      "아직 실행 결과가 없습니다.";
    renderManifestOutputs(null);
    renderAgentControlStages(null);
    renderExperimentDetail(null);
    return;
  }

  state.activeFlowID = flow.correlation_id;
  const authorization = flow.agent_authorization || {};
  const execution = flow.agent_execution || {};
  const decision = flow.decision || {};
  const guard = flow.guard || {};
  const linkedRun =
    state.activeAutomationRun?.flow?.correlation_id === flow.correlation_id
      ? state.activeAutomationRun
      : null;
  const deploymentSubmission =
    flow.deployment_submission || linkedRun?.deployment_submission;
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
  byID("agent-control-agent-name").textContent = text(
    execution.agent_name,
    authorization.agent_name,
  );
  byID("agent-control-agent-source").textContent = text(execution.source);
  byID("agent-control-dispatch-status").textContent = text(execution.status);
  byID("agent-control-agent-latency").textContent =
    execution.latency_ms === undefined ? "-" : `${execution.latency_ms} ms`;
  byID("agent-control-request-guard").textContent = text(
    execution.request_guard?.status,
  );
  byID("agent-control-result-guard").textContent = text(
    execution.result_guard?.status,
  );
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
    requested_decision_agent: flow.requested_decision_agent,
    requested_operation_agent: flow.requested_operation_agent,
    agent_authorization: flow.agent_authorization,
    agent_execution: flow.agent_execution,
    decision: flow.decision,
    guard: flow.guard,
    desired_deployment_spec: desiredDeploymentSpec(flow),
    deployment_request: flow.deployment_request,
    manifest_revisions: flow.manifest_revisions,
    deployment_submission: deploymentSubmission,
    operation_agent_execution: flow.operation_agent_execution,
    scaling_decision: flow.scaling_decision,
  });
  renderAgentControlStages(flow);
  renderExperimentDetail(flow);
}

function renderAutomationRun(run, trustedResult = null) {
  state.activeAutomationRun = run || null;
  state.activeTrustedResult = trustedResult || null;
  if (!run) {
	state.activeFlowID = "";
	renderAgentControlFlow(null);
	byID("llmop-safeguard-status").textContent = text(
	  trustedResult?.safeguard?.status,
	);
	byID("agent-control-status").textContent = text(
	  trustedResult?.status,
	  "WAITING",
	);
	byID("automation-application-profile-json").textContent =
	  "아직 생성되지 않았습니다.";
	byID("automation-resource-recommendation-json").textContent =
	  "아직 생성되지 않았습니다.";
	byID("agent-control-result-json").textContent = trustedResult
	  ? pretty(trustedResult)
	  : "아직 실행 결과가 없습니다.";
	byID("deployment-status-json").value = "";
	byID("optimization-feedback-json").value = "";
    return;
  }

  const flow = run.flow || null;
  renderAgentControlFlow(flow);
  const analysis = run.requirement_analysis || {};
  const recommendation = run.resource_recommendation || {};
  const submission = run.deployment_submission || {};
  const mode = analysis.evidence?.mode || analysis.mode || "-";
	byID("llmop-safeguard-status").textContent = text(
	  trustedResult?.safeguard?.status,
	);
  byID("agent-control-flow-id").textContent =
    flow?.correlation_id || run.correlation_id || run.run_id;
  byID("agent-control-status").textContent = text(
    flow?.state,
    run.status,
  );
  byID("automation-analysis-mode").textContent = text(mode);
  byID("agent-control-adapter").textContent = text(submission.adapter);
  byID("agent-control-adapter-status").textContent = text(submission.status);
  byID("agent-control-candidate").textContent = text(
    flow?.decision?.selected_candidate_id ||
      recommendation.resource_recommendation?.selected_candidate_id,
  );
  byID("automation-application-profile-json").textContent = pretty(
    analysis.application_profile || {},
  );
  byID("automation-resource-recommendation-json").textContent = pretty(
    recommendation,
  );
  byID("agent-control-result-json").textContent = pretty({
	llm_op_safeguard: trustedResult?.safeguard,
	trusted_orchestration_status: trustedResult?.status,
    run_id: run.run_id,
    correlation_id: run.correlation_id,
    trace_id: run.trace_id,
    status: run.status,
    analyzer: analysis.evidence || { mode: analysis.mode },
    requested_decision_agent: run.input?.decision_agent,
    requested_operation_agent: flow?.requested_operation_agent,
    agent_authorization: flow?.agent_authorization,
    agent_execution: flow?.agent_execution,
    decision: flow?.decision,
    guard: flow?.guard,
    desired_deployment_spec:
      run.desired_deployment_spec || desiredDeploymentSpec(flow),
    deployment_request: flow?.deployment_request,
    manifest_revisions: flow?.manifest_revisions,
    deployment_submission: run.deployment_submission,
    operation_agent_execution: flow?.operation_agent_execution,
    scaling_decision: flow?.scaling_decision,
  });
  renderAgentControlStages(run);
}

async function submitAutomationRun(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const completion = byID("automation-run-completion");
  completion.hidden = true;
  completion.textContent = "";
  setBusy(form, true, "자동 실행 중...");
  try {
    const trustedResult = await apiRequest(API.trustedAutomationRuns, {
      method: "POST",
      body: JSON.stringify({
		app_version_id: "appver-geon-poc-001",
		candidate_id: "qwen3.5-ops-planner",
		input: buildAutomationRunPayload(),
	  }),
    });
	const run = trustedResult.automation_run;
	if (!run) {
	  renderAutomationRun(null, trustedResult);
	  byID("agent-control-status").textContent = text(trustedResult.status);
	  byID("agent-control-result-json").textContent = pretty(trustedResult);
	  const safeguardStatus = text(trustedResult.safeguard?.status, trustedResult.status);
	  completion.textContent = `Safeguard 중단 · ${safeguardStatus}`;
	  completion.hidden = false;
	  showToast(completion.textContent, "warning");
	  return;
	}
    if (run.flow) {
      upsertFlow(run.flow);
    }
    renderAutomationRun(run, trustedResult);
    renderExperimentFlows();
    const action = run.flow?.decision?.action || run.flow?.state || run.status;
    const flowID = text(run.flow?.correlation_id, run.correlation_id, "Flow ID 없음");
    const completionLabel =
      action === "DEPLOY"
        ? "Revision 1 생성 완료"
        : `배포 판단 완료 · ${text(action)}`;
    completion.textContent = `Safeguard 승인 · ${completionLabel} · ${flowID}`;
    completion.hidden = false;
    showToast(
      `Safeguard 승인 · ${completionLabel} · ${flowID}`,
      action === "DEPLOY" ? "success" : "warning",
    );
  } catch (error) {
    byID("agent-control-result-json").textContent = pretty(
      error.payload || { message: error.message },
    );
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
    syncFeedbackControls(activeFlow());
  }
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

async function submitProtocolFlow(event) {
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
    syncFeedbackControls(activeFlow());
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

async function loadAgentControlFlows(
  preferredID = state.activeFlowID,
  selectNewest = true,
) {
  try {
    const payload = await apiRequest(API.agentControlFlows);
    state.flows = Array.isArray(payload.flows) ? payload.flows : [];
    state.flows.sort(
      (left, right) =>
        new Date(right.updated_at || 0) - new Date(left.updated_at || 0),
    );
    const selected =
      state.flows.find((flow) => flow.correlation_id === preferredID) ||
      (selectNewest ? state.flows[0] : null) ||
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
    select.dataset.flowId = flow.correlation_id;
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

function decisionAgentSourceLabel(source) {
  return source === "runtime" ? "Runtime" : "Internal";
}

function updateDecisionAgentHelp() {
  const select = byID("decision-agent-select");
  const selected = state.eligibleDecisionAgents.find(
    (agent) => agent.name === select.value,
  );
  byID("decision-agent-help").textContent = selected
    ? `${decisionAgentSourceLabel(selected.source)} Agent · Registry 권한 확인 후 외부 Go Guard를 적용합니다.`
    : "Agent Registry에서 배포 판단 권한을 가진 Agent만 표시합니다.";
}

function renderDecisionAgentOptions() {
  const select = byID("decision-agent-select");
  const previous = select.value;
  select.replaceChildren();
  if (state.eligibleDecisionAgents.length === 0) {
    const option = createElement("option", "", "사용 가능한 배포 판단 Agent가 없습니다.");
    option.value = "";
    option.disabled = true;
    option.selected = true;
    select.append(option);
    select.disabled = true;
    updateDecisionAgentHelp();
    return;
  }

  select.disabled = false;
  state.eligibleDecisionAgents.forEach((agent) => {
    const option = createElement(
      "option",
      "",
      `${agent.name} (${decisionAgentSourceLabel(agent.source)})`,
    );
    option.value = agent.name;
    option.dataset.source = agent.source;
    select.append(option);
  });
  const defaultAgent = state.agentDefaults.ai_application_automation;
  const selectedName = state.eligibleDecisionAgents.some(
    (agent) => agent.name === previous,
  )
    ? previous
    : state.eligibleDecisionAgents.some((agent) => agent.name === defaultAgent)
      ? defaultAgent
      : state.eligibleDecisionAgents[0].name;
  select.value = selectedName;
  updateDecisionAgentHelp();
}

function updateOperationAgentHelp() {
  const select = byID("optimization-operation-agent-select");
  const selected = state.eligibleOperationAgents.find(
    (agent) => agent.name === select.value,
  );
  byID("optimization-operation-agent-help").textContent = selected
    ? `${decisionAgentSourceLabel(selected.source)} Agent · Registry 권한과 Scaling Guard를 적용합니다.`
    : "Registry에서 스케일링 권한을 가진 Agent만 표시합니다.";
}

function renderOperationAgentOptions() {
  const select = byID("optimization-operation-agent-select");
  const previous = select.value;
  select.replaceChildren();
  if (state.eligibleOperationAgents.length === 0) {
    const option = createElement("option", "", "사용 가능한 운영 최적화 Agent가 없습니다.");
    option.value = "";
    option.disabled = true;
    option.selected = true;
    select.append(option);
    select.disabled = true;
    updateOperationAgentHelp();
    return;
  }

  select.disabled = false;
  state.eligibleOperationAgents.forEach((agent) => {
    const option = createElement(
      "option",
      "",
      `${agent.name} (${decisionAgentSourceLabel(agent.source)})`,
    );
    option.value = agent.name;
    option.dataset.source = agent.source;
    select.append(option);
  });
  const defaultAgent = state.agentDefaults.ai_application_operation_optimization;
  const selectedName = state.eligibleOperationAgents.some(
    (agent) => agent.name === previous,
  )
    ? previous
    : state.eligibleOperationAgents.some((agent) => agent.name === defaultAgent)
      ? defaultAgent
      : state.eligibleOperationAgents[0].name;
  select.value = selectedName;
  updateOperationAgentHelp();
}

async function loadAgents() {
  try {
    const payload = await apiRequest(API.agents);
    state.agents = Array.isArray(payload.agents) ? payload.agents : [];
    state.eligibleDecisionAgents = Array.isArray(payload.eligible_decision_agents)
      ? payload.eligible_decision_agents
      : [];
    state.eligibleOperationAgents = Array.isArray(payload.eligible_operation_agents)
      ? payload.eligible_operation_agents
      : [];
    state.agentDefaults = payload.defaults || {};
    byID("core-default-agent").textContent = text(
      state.agentDefaults.ai_application_automation,
      "미설정",
    );
    byID("core-operation-agent").textContent = text(
      state.agentDefaults.ai_application_operation_optimization,
      "미설정",
    );
    renderAgents();
    renderDecisionAgentOptions();
    renderOperationAgentOptions();
  } catch (error) {
    state.eligibleDecisionAgents = [];
    state.eligibleOperationAgents = [];
    renderDecisionAgentOptions();
    renderOperationAgentOptions();
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

function loadFeedbackSamples(flow = activeFlow()) {
  const selectedFlow = flow?.correlation_id ? flow : activeFlow();
  if (!selectedFlow?.correlation_id) {
    byID("deployment-status-json").value = "";
    byID("optimization-feedback-json").value = "";
    return;
  }
  const samples = buildFeedbackSamples(selectedFlow);
  byID("deployment-status-json").value = pretty(samples.status);
  byID("optimization-feedback-json").value = pretty(samples.feedback);
}

function requireSelectedFlowMessage(body, label) {
  const flow = activeFlow();
  if (!flow) {
    throw new Error(`${label}: 먼저 실험 Flow를 선택하세요.`);
  }
  if (body.correlation_id !== flow.correlation_id) {
    throw new Error(
      `${label}: 현재 Flow(${flow.correlation_id})와 JSON correlation_id(${text(body.correlation_id)})가 다릅니다.`,
    );
  }
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
    requireSelectedFlowMessage(body, "Deployment Status");
    const flow = await apiRequest(API.deploymentStatus, {
      method: "POST",
      body: JSON.stringify(body),
    });
    upsertFlow(flow);
    renderAgentControlFlow(flow);
    renderExperimentFlows();
    byID("deployment-status-submit").disabled = true;
    byID("optimization-feedback-submit").disabled = false;
    showToast("배포 상태를 Flow에 연결했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
    syncFeedbackControls(activeFlow());
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
    requireSelectedFlowMessage(body, "Optimization Feedback");
    const selectedOperationAgent = byID("optimization-operation-agent-select").value;
    const query = selectedOperationAgent
      ? `?${new URLSearchParams({ operation_agent: selectedOperationAgent }).toString()}`
      : "";
    const flow = await apiRequest(`${API.optimizationFeedback}${query}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    upsertFlow(flow);
    renderAgentControlFlow(flow);
    renderExperimentFlows();
    byID("deployment-status-submit").disabled = true;
    byID("optimization-feedback-submit").disabled = true;
    showToast(
      `스케일링 판단: ${text(flow.scaling_decision?.action, "결과 없음")}`,
      "success",
    );
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
    syncFeedbackControls(activeFlow());
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
  byID("automation-run-form").addEventListener(
    "submit",
    submitAutomationRun,
  );
  byID("decision-agent-select").addEventListener(
    "change",
    updateDecisionAgentHelp,
  );
  byID("optimization-operation-agent-select").addEventListener(
    "change",
    updateOperationAgentHelp,
  );
  document
    .querySelectorAll('input[name="automation_input_mode"]')
    .forEach((radio) => {
      radio.addEventListener("change", () => {
        if (radio.checked) setAutomationInputMode(radio.value);
      });
    });
  byID("automation-flow-form").addEventListener(
    "submit",
    submitProtocolFlow,
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
    () => loadFeedbackSamples(activeFlow()),
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
  byID("automation-app-spec-json").value = pretty(STRUCTURED_APP_SPEC_SAMPLE);
  setAutomationInputMode("natural_language");
  renderAgentControlFlow(null);
  renderAutomationRun(null);
  switchView("agent-control");
  window.lucide?.createIcons();
  await Promise.all([
    refreshHealth(),
    loadAgentControlFlows("", false),
    loadAgents(),
  ]);
}

document.addEventListener("DOMContentLoaded", initialize);
