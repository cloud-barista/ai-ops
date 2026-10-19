(() => {
  let configured = false;
  let enabled = false;
  let currentID = "";
  let preparedApp = "";
  let busy = false;
  const deliveries = new Map();
  const field = byID("flow-delivery-app-version");
  const status = byID("flow-delivery-status");
  const output = byID("flow-delivery-json");
  const prepare = byID("flow-delivery-prepare");
  const submit = byID("flow-delivery-submit");
  const refresh = byID("flow-delivery-refresh");
  const accept = byID("flow-delivery-accept");

  window.renderFlowDelivery = (flow) => {
    const id = flow?.correlation_id || "";
    if (currentID !== id) {
      currentID = id;
      preparedApp = "";
      accept.checked = false;
      field.value = "";
      output.textContent = "{}";
      status.textContent = id ? `선택 Flow: ${id} · 전송 전` : "Flow를 선택하세요.";
    }
    const record = deliveries.get(id);
    const deployed = flow?.deployment_status?.source?.system === "appdeployer";
    const approved = flow?.state === "DEPLOY_APPROVED" && flow.manifest_revisions?.length === 1 && !flow.deployment_status;
    prepare.disabled = busy || !configured || !approved || !field.value.trim();
    submit.disabled = busy || !enabled || !approved || !accept.checked || preparedApp !== field.value.trim() || !preparedApp || Boolean(record);
    refresh.disabled = busy || !configured || !id;
    field.disabled = busy || Boolean(record) || deployed;
    if (record) {
      field.value = record.request?.manifest?.spec?.app_version_id || "";
      output.textContent = pretty(record);
      byID("deployment-status-submit").disabled = true;
      byID("load-agent-control-feedback-sample").disabled = true;
      status.textContent = `${id} · ${record.status} · ${record.deployment?.deployment_id || "응답 확인 필요"} · ${record.deployment?.status || ""}`;
    } else if (deployed) {
      byID("deployment-status-submit").disabled = true;
      byID("load-agent-control-feedback-sample").disabled = true;
      status.textContent = `${id} · AppDeployer 배포 연결됨 · 최신 상태 조회 가능`;
    } else {
      byID("load-agent-control-feedback-sample").disabled = false;
    }
  };

  field.addEventListener("input", () => { preparedApp = ""; window.renderFlowDelivery(activeFlow()); });
  accept.addEventListener("change", () => window.renderFlowDelivery(activeFlow()));
  async function run(action) {
    const id = currentID;
    const app = field.value.trim();
    if (!id || busy) return;
    busy = true;
    window.renderFlowDelivery(activeFlow());
    try {
      const base = `/api/v1/agent-control/flows/${encodeURIComponent(id)}/appdeploy`;
      const result = await apiRequest(action === "refresh" ? base : `${base}/${action}`, action === "refresh" ? {} : {method: "POST", body: JSON.stringify({app_version_id: app, accept_projection_limits: accept.checked})});
      if (result.status !== "PREPARED") deliveries.set(id, result);
      if (currentID !== id) return;
      preparedApp = result.status === "PREPARED" ? app : "";
      output.textContent = pretty(result);
      status.textContent = `${id} · ${result.status}`;
      if (result.flow) { upsertFlow(result.flow); renderAgentControlFlow(result.flow); renderExperimentFlows(); }
      showToast(action === "prepare" ? "App Registry와 승인 Manifest를 확인했습니다." : action === "submit" ? "AppDeployer 응답을 확인했습니다. 배포 상태를 조회하세요." : "AppDeployer 배포 상태를 확인했습니다.", "success");
    } catch (error) {
      if (currentID === id) { preparedApp = ""; status.textContent = error.message; }
      showToast(error.message, "error");
    } finally { busy = false; window.renderFlowDelivery(activeFlow()); }
  }
  prepare.addEventListener("click", () => run("prepare"));
  submit.addEventListener("click", () => run("submit"));
  refresh.addEventListener("click", () => run("refresh"));
  apiRequest("/api/v1/agent-control/integration").then((config) => {
    configured = config.configured;
    enabled = config.submit_enabled;
    byID("flow-delivery-availability").textContent = !configured ? "AppDeployer 미설정" : enabled ? "AppDeployer 전송 사용 · 실행 중인 서비스에 배포를 요청합니다." : "요청 확인 전용 · 서버의 배포 전송 설정 꺼짐";
    window.renderFlowDelivery(activeFlow());
  }).catch(() => { byID("flow-delivery-availability").textContent = "연결 설정 조회 실패"; });
})();
