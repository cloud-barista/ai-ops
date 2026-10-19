"use strict";

(function initializeLLMOpDemo() {
  const contract = window.LLMOpDemoContract;
  if (!contract) {
    document.body.textContent = "LLM_Op demo contract를 불러오지 못했습니다.";
    return;
  }

  const state = {
    scenario: contract.SCENARIOS[0],
    safeguardCaptured: 0,
    proposalCaptured: 0,
    safeguardValue: null,
    proposalValue: null,
    finalResult: null,
  };

  const elements = {
    scenarioList: document.getElementById("scenario-list"),
    scenarioCount: document.getElementById("scenario-count"),
    title: document.getElementById("selected-scenario-title"),
    category: document.getElementById("selected-scenario-category"),
    expected: document.getElementById("selected-expected-status"),
    description: document.getElementById("selected-scenario-description"),
    serviceName: document.getElementById("selected-service-name"),
    serviceTask: document.getElementById("selected-service-task"),
    readiness: document.getElementById("selected-readiness"),
    request: document.getElementById("selected-user-request"),
    context: document.getElementById("selected-context-json"),
    learning: document.getElementById("selected-learning"),
    source: document.getElementById("selected-source"),
    catalogID: document.getElementById("selected-catalog-id"),
    start: document.getElementById("start-scenario"),
    reset: document.getElementById("reset-scenario"),
    preflightPanel: document.getElementById("preflight-panel"),
    preflightStatus: document.getElementById("preflight-status"),
    preflightReason: document.getElementById("preflight-reason"),
    preflightCode: document.getElementById("preflight-code"),
    safeguardPanel: document.getElementById("safeguard-panel"),
    safeguardStatus: document.getElementById("safeguard-status"),
    safeguardPrompt: document.getElementById("safeguard-prompt"),
    safeguardResponse: document.getElementById("safeguard-response"),
    fillSafeguard: document.getElementById("fill-safeguard-fixture"),
    validateSafeguard: document.getElementById("validate-safeguard"),
    safeguardValidation: document.getElementById("safeguard-validation"),
    proposalPanel: document.getElementById("proposal-panel"),
    proposalStatus: document.getElementById("proposal-status"),
    proposalPrompt: document.getElementById("proposal-prompt"),
    proposalResponse: document.getElementById("proposal-response"),
    fillProposal: document.getElementById("fill-proposal-fixture"),
    validateProposal: document.getElementById("validate-proposal"),
    proposalValidation: document.getElementById("proposal-validation"),
    resultPanel: document.getElementById("result-panel"),
    finalStatus: document.getElementById("final-status"),
    finalSummary: document.getElementById("final-summary"),
    finalJSON: document.getElementById("final-result-json"),
    ledgerSafeguard: document.getElementById("ledger-safeguard"),
    ledgerProposal: document.getElementById("ledger-proposal"),
    copyFinal: document.getElementById("copy-final-result"),
    catalogGrid: document.getElementById("catalog-category-grid"),
    contractVersion: document.getElementById("contract-version"),
    toastRegion: document.getElementById("toast-region"),
  };

  function pretty(value) {
    return JSON.stringify(value, null, 2);
  }

  function setText(element, value) {
    element.textContent = value;
  }

  function setStatus(element, value, tone) {
    element.textContent = value;
    element.classList.remove("is-waiting", "is-passed", "is-rejected", "is-clarify");
    element.classList.add(tone || "is-waiting");
  }

  function setValidation(element, message, kind) {
    element.textContent = message;
    element.classList.remove("is-error", "is-success");
    if (kind) element.classList.add(kind);
  }

  function setPanelLocked(panel, locked) {
    panel.classList.toggle("is-locked", locked);
    for (const control of panel.querySelectorAll("button, textarea")) {
      control.disabled = locked;
    }
  }

  function markRail(current, rejected) {
    const order = ["input", "preflight", "safeguard", "proposal", "handoff"];
    const currentIndex = order.indexOf(current);
    for (const item of document.querySelectorAll("[data-demo-stage]")) {
      const itemIndex = order.indexOf(item.dataset.demoStage);
      item.classList.remove("is-current", "is-complete", "is-rejected");
      if (itemIndex < currentIndex) item.classList.add("is-complete");
      if (itemIndex === currentIndex) item.classList.add(rejected ? "is-rejected" : "is-current");
    }
  }

  function showToast(message) {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    elements.toastRegion.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3200);
  }

  async function copyText(value, successMessage) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
      } else {
        const temporary = document.createElement("textarea");
        temporary.value = value;
        temporary.setAttribute("readonly", "");
        temporary.style.position = "fixed";
        temporary.style.opacity = "0";
        document.body.appendChild(temporary);
        temporary.select();
        const copied = document.execCommand("copy");
        temporary.remove();
        if (!copied) throw new Error("copy command rejected");
      }
      showToast(successMessage);
    } catch (_error) {
      showToast("자동 복사가 거부되었습니다. 텍스트 영역을 직접 선택해 복사해 주세요.");
    }
  }

  async function sha256(value) {
    if (!window.crypto || !window.crypto.subtle || typeof TextEncoder === "undefined") {
      return "unavailable_in_this_browser_context";
    }
    const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  function serviceForScenario() {
    return contract.getService(state.scenario.service_id);
  }

  function renderScenarioList() {
    elements.scenarioList.replaceChildren();
    contract.SCENARIOS.forEach((scenario, index) => {
      const button = document.createElement("button");
      const number = document.createElement("span");
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      const category = document.createElement("small");

      button.type = "button";
      button.className = "scenario-card";
      button.dataset.scenarioId = scenario.id;
      button.classList.toggle("is-active", scenario.id === state.scenario.id);
      number.className = "scenario-card-index";
      number.textContent = String(index + 1).padStart(2, "0");
      title.textContent = scenario.title;
      category.textContent = scenario.category;
      copy.append(title, category);
      button.append(number, copy);
      button.addEventListener("click", () => selectScenario(scenario.id));
      elements.scenarioList.appendChild(button);
    });
    setText(elements.scenarioCount, contract.SCENARIOS.length);
  }

  function renderSelectedScenario() {
    const scenario = state.scenario;
    const service = serviceForScenario();
    setText(elements.title, scenario.title);
    setText(elements.category, scenario.category);
    setText(elements.expected, scenario.expected_status);
    setText(elements.description, scenario.description);
    setText(elements.serviceName, service.display_name);
    const workloadLabel = service.task + " · " + service.workload_model_ref;
    setText(elements.serviceTask, workloadLabel);
    elements.serviceTask.title = workloadLabel;
    setText(elements.readiness, scenario.readiness);
    elements.request.value = scenario.request;
    setText(elements.context, pretty(scenario.operation_context));
    setText(elements.learning, scenario.learning);
    setText(elements.source, scenario.source);
    setText(elements.catalogID, scenario.catalog_id || "browser materialized extension");
  }

  function renderCatalogSummary() {
    elements.catalogGrid.replaceChildren();
    for (const category of contract.CATALOG_SUMMARY.categories) {
      const card = document.createElement("div");
      const count = document.createElement("strong");
      const text = document.createElement("span");
      const label = document.createElement("b");
      const code = document.createElement("small");
      card.className = "catalog-category";
      count.textContent = category.count;
      label.textContent = category.label;
      code.textContent = category.name;
      text.append(label, code);
      card.append(count, text);
      elements.catalogGrid.appendChild(card);
    }
  }

  function resetRun() {
    state.safeguardCaptured = 0;
    state.proposalCaptured = 0;
    state.safeguardValue = null;
    state.proposalValue = null;
    state.finalResult = null;

    elements.safeguardPrompt.value = "";
    elements.safeguardResponse.value = "";
    elements.proposalPrompt.value = "";
    elements.proposalResponse.value = "";
    setText(elements.ledgerSafeguard, "0");
    setText(elements.ledgerProposal, "0");
    setStatus(elements.preflightStatus, "WAITING", "is-waiting");
    setText(elements.preflightReason, "시나리오를 시작하면 prepare-only 범위와 보안 경계를 먼저 확인합니다.");
    setText(elements.preflightCode, "-");
    setStatus(elements.safeguardStatus, "LOCKED", "is-waiting");
    setStatus(elements.proposalStatus, "LOCKED", "is-waiting");
    setValidation(elements.safeguardValidation, "아직 응답을 검증하지 않았습니다.");
    setValidation(elements.proposalValidation, "Safeguard allow를 기다리고 있습니다.");
    setPanelLocked(elements.safeguardPanel, true);
    setPanelLocked(elements.proposalPanel, true);
    setPanelLocked(elements.resultPanel, true);
    setStatus(elements.finalStatus, "WAITING", "is-waiting");
    setText(elements.finalSummary, "두 단계의 검증 결과가 여기에 표시됩니다.");
    setText(elements.finalJSON, "아직 결과가 없습니다.");
    markRail("input", false);
  }

  function selectScenario(id) {
    const scenario = contract.getScenario(id);
    if (!scenario) return;
    state.scenario = scenario;
    renderScenarioList();
    renderSelectedScenario();
    resetRun();
    document.getElementById("scenario-lab").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function commonEvidence() {
    return {
      contract_version: contract.CONTRACT_VERSION,
      prompt_version: contract.PROMPT_VERSION,
      mode: "manual_copy_paste",
      scenario_id: state.scenario.id,
      candidate_binding: contract.MODEL_BINDING,
      call_ledger: {
        page_model_api_calls: 0,
        operator_managed_external_ai_calls: "not_observed_by_page",
        safeguard_outputs_received: state.safeguardCaptured,
        proposal_outputs_received: state.proposalCaptured,
        appdeploy_posts: 0,
      },
      disclaimer: "Browser validation mirrors the demo contract; authoritative Go guards must revalidate raw outputs.",
    };
  }

  function clearFinalForContinuation() {
    state.finalResult = null;
    setPanelLocked(elements.resultPanel, true);
    setStatus(elements.finalStatus, "WAITING", "is-waiting");
    setText(elements.finalSummary, "두 번째 LLM 출력을 기다리고 있습니다.");
    setText(elements.finalJSON, "아직 두 번째 출력 결과가 없습니다.");
  }

  async function finish(result, summary, tone, railStage, railRejected) {
    const promptDigests = {};
    if (elements.safeguardPrompt.value) {
      promptDigests.safeguard_sha256 = await sha256(elements.safeguardPrompt.value);
    }
    if (elements.proposalPrompt.value) {
      promptDigests.proposal_sha256 = await sha256(elements.proposalPrompt.value);
    }
    state.finalResult = Object.assign({}, commonEvidence(), result, { prompt_digests: promptDigests });
    setPanelLocked(elements.resultPanel, false);
    setStatus(elements.finalStatus, result.status, tone);
    setText(elements.finalSummary, summary);
    setText(elements.finalJSON, pretty(state.finalResult));
    markRail(railStage, railRejected);
  }

  function startScenario() {
    resetRun();
    const scenario = state.scenario;
    setText(elements.preflightReason, scenario.preflight.reason);
    setText(elements.preflightCode, scenario.preflight.code);

    if (scenario.preflight.status === "rejected") {
      setStatus(elements.preflightStatus, "REJECTED", "is-rejected");
      finish({
        status: "REQUEST_REJECTED",
        stage: "REQUEST_PRECHECK_REJECTED",
        decision: {
          action: "reject_unsafe_request",
          reason_code: scenario.preflight.code,
          reason: scenario.preflight.reason,
        },
        second_llm_output: null,
        prepared_request: null,
        submission_mode: "not_submitted",
      }, "결정적 사전 검사에서 중단했습니다. 두 LLM 입력은 생성·전송하지 않았습니다.", "is-rejected", "preflight", true);
      return;
    }

    setStatus(elements.preflightStatus, "PASSED", "is-passed");
    elements.safeguardPrompt.value = contract.buildCombinedPrompt(scenario, "safeguard");
    setPanelLocked(elements.safeguardPanel, false);
    setStatus(elements.safeguardStatus, "INPUT READY", "is-passed");
    markRail("safeguard", false);
    elements.safeguardPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function validateSafeguard() {
    const raw = elements.safeguardResponse.value;
    if (!raw.trim()) {
      setValidation(elements.safeguardValidation, "LLM raw JSON 응답을 붙여넣어 주세요.", "is-error");
      return;
    }
    state.safeguardCaptured = 1;
    setText(elements.ledgerSafeguard, "1");
    const validation = contract.validateSafeguardResponse(raw);
    if (!validation.ok) {
      setStatus(elements.safeguardStatus, "INVALID", "is-rejected");
      setValidation(elements.safeguardValidation, validation.errors.join(" "), "is-error");
      setPanelLocked(elements.proposalPanel, true);
      setStatus(elements.proposalStatus, "LOCKED", "is-waiting");
      await finish({
        status: "MODEL_UNAVAILABLE",
        stage: "SAFEGUARD_OUTPUT_CONTRACT",
        validation_errors: validation.errors,
        second_llm_output: null,
        prepared_request: null,
        submission_mode: "not_submitted",
      }, "첫 번째 raw 출력이 Safeguard JSON 계약을 통과하지 못했습니다. 원문을 사람이 고쳐 승인 증적으로 만들 수 없습니다.", "is-rejected", "safeguard", true);
      return;
    }

    state.safeguardValue = validation.value;
    const outcome = contract.evaluateSafeguard(validation.value);
    setValidation(elements.safeguardValidation, "Safeguard JSON 계약을 통과했습니다: " + validation.value.decision, "is-success");

    if (outcome.terminal) {
      const clarification = outcome.status === "CLARIFICATION_REQUIRED";
      setStatus(
        elements.safeguardStatus,
        clarification ? "CLARIFY" : "REJECTED",
        clarification ? "is-clarify" : "is-rejected",
      );
      await finish({
        status: outcome.status,
        stage: "SAFEGUARD_COMPLETE",
        safeguard_output: validation.value,
        second_llm_output: null,
        prepared_request: null,
        submission_mode: "not_submitted",
        expected_status: state.scenario.expected_status,
        expected_status_matched: outcome.status === state.scenario.expected_status,
      }, clarification
        ? "필수 자원값이 모호하여 사용자 명확화가 필요합니다. 두 번째 LLM 단계는 열리지 않았습니다."
        : "Safeguard가 요청을 거부했습니다. 두 번째 LLM 단계는 열리지 않았습니다.",
      clarification ? "is-clarify" : "is-rejected", "safeguard", !clarification);
      return;
    }

    setStatus(elements.safeguardStatus, "ALLOWED", "is-passed");
    clearFinalForContinuation();
    elements.proposalPrompt.value = contract.buildCombinedPrompt(state.scenario, "proposal");
    setPanelLocked(elements.proposalPanel, false);
    setStatus(elements.proposalStatus, "INPUT READY", "is-passed");
    setValidation(elements.proposalValidation, "Safeguard allow가 검증되었습니다. 두 번째 raw JSON을 입력하세요.", "is-success");
    markRail("proposal", false);
    elements.proposalPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function validateProposal() {
    const raw = elements.proposalResponse.value;
    if (!raw.trim()) {
      setValidation(elements.proposalValidation, "두 번째 LLM raw JSON 응답을 붙여넣어 주세요.", "is-error");
      return;
    }
    state.proposalCaptured = 1;
    setText(elements.ledgerProposal, "1");
    const validation = contract.validateProposalResponse(raw, state.scenario);
    if (!validation.ok) {
      setStatus(elements.proposalStatus, "INVALID", "is-rejected");
      setValidation(elements.proposalValidation, validation.errors.join(" "), "is-error");
      await finish({
        status: "MANIFEST_REJECTED",
        stage: "PROPOSAL_OUTPUT_CONTRACT",
        safeguard_output: state.safeguardValue,
        validation_errors: validation.errors,
        second_llm_output: null,
        prepared_request: null,
        submission_mode: "not_submitted",
      }, "두 번째 raw 출력이 parser·bounded proposal 계약을 통과하지 못했습니다. AppDeploy 인계 초안은 없습니다.", "is-rejected", "proposal", true);
      return;
    }

    state.proposalValue = validation.value;
    const outcome = contract.evaluateProposal(validation.value, state.scenario);
    const matchesExpected = outcome.status === state.scenario.expected_status;
    setValidation(
      elements.proposalValidation,
      "Proposal JSON과 exact 자원 계약을 통과했습니다." +
        (matchesExpected ? "" : " 단, 선택한 fixture의 기대 경로와는 다릅니다."),
      "is-success",
    );

    if (outcome.status === "HANDOFF_READY") {
      setStatus(elements.proposalStatus, "VALIDATED", "is-passed");
      await finish(Object.assign({}, outcome.handoff, {
        stage: "SECOND_LLM_OUTPUT_VALIDATED",
        safeguard_output: state.safeguardValue,
        expected_status: state.scenario.expected_status,
        expected_status_matched: matchesExpected,
      }), state.scenario.readiness === "unknown"
        ? "두 번째 LLM 출력까지 검증했습니다. 인계 body는 준비됐지만 서버 readiness는 증명되지 않았고 POST하지 않았습니다."
        : "두 번째 LLM 출력과 fresh readiness 모순 여부를 확인했습니다. AppDeploy용 body 초안만 준비했으며 POST하지 않았습니다.",
      "is-passed", "handoff", false);
      return;
    }

    const clarification = outcome.status === "CLARIFICATION_REQUIRED";
    setStatus(
      elements.proposalStatus,
      clarification ? "CLARIFY" : "REJECTED",
      clarification ? "is-clarify" : "is-rejected",
    );
    await finish({
      status: outcome.status,
      stage: "SECOND_LLM_OUTPUT_VALIDATED",
      safeguard_output: state.safeguardValue,
      second_llm_output: validation.value,
      prepared_request: null,
      submission_mode: "not_submitted",
      expected_status: state.scenario.expected_status,
      expected_status_matched: matchesExpected,
    }, "두 번째 LLM이 create가 아닌 종료 action을 제안했습니다. Manifest와 인계 body는 만들지 않았습니다.",
    clarification ? "is-clarify" : "is-rejected", "proposal", !clarification);
  }

  function promptPart(stage, part) {
    const parts = contract.buildPromptParts(state.scenario, stage);
    if (part === "system") return parts.system;
    if (part === "user") return parts.user;
    return contract.buildCombinedPrompt(state.scenario, stage);
  }

  for (const button of document.querySelectorAll("[data-copy-stage]")) {
    button.addEventListener("click", () => {
      copyText(
        promptPart(button.dataset.copyStage, button.dataset.copyPart),
        button.dataset.copyPart === "combined" ? "전체 LLM 입력을 복사했습니다." : button.dataset.copyPart.toUpperCase() + " 입력을 복사했습니다.",
      );
    });
  }

  elements.start.addEventListener("click", startScenario);
  elements.reset.addEventListener("click", () => {
    resetRun();
    elements.preflightPanel.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  elements.fillSafeguard.addEventListener("click", () => {
    if (!state.scenario.safeguard_fixture) return;
    elements.safeguardResponse.value = state.scenario.safeguard_fixture;
    setValidation(elements.safeguardValidation, "저장된 예시 raw 응답을 넣었습니다. 검증 버튼을 누르세요.");
  });
  elements.validateSafeguard.addEventListener("click", validateSafeguard);
  elements.fillProposal.addEventListener("click", () => {
    if (!state.scenario.proposal_fixture) return;
    elements.proposalResponse.value = state.scenario.proposal_fixture;
    setValidation(elements.proposalValidation, "저장된 예시 raw 응답을 넣었습니다. 검증 버튼을 누르세요.");
  });
  elements.validateProposal.addEventListener("click", validateProposal);
  elements.copyFinal.addEventListener("click", () => {
    copyText(elements.finalJSON.textContent, "검증·인계 결과를 복사했습니다.");
  });

  elements.contractVersion.textContent =
    contract.CONTRACT_VERSION + " · " + contract.PROMPT_VERSION;
  renderScenarioList();
  renderSelectedScenario();
  renderCatalogSummary();
  resetRun();
})();
