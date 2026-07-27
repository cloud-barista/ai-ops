# User-Request-First Control Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep geon compatible with the latest AppDeploy deployment contract, make Manifest Workflow the Agent Control entry point, connect all screens through one active `run_id`, and render automatic Feedback from ControlRun, AppDeploy, executor, and Autonomy evidence.

**Architecture:** Keep `controlrun.Run` as the authoritative execution record and avoid a duplicate automatic Feedback store. Reorder the embedded web application around the user request, add a deterministic Manifest stage flow, propagate one selected Run into Registry, post-deployment, and Feedback views, and retain the existing Automation Feedback API only for verified external executor callbacks.

**Tech Stack:** Go 1.25, Echo v4, embedded HTML/CSS/vanilla JavaScript, existing ControlRun store, Agent Registry and Dispatcher, Qwen Planner, Go Request/Manifest Guards, existing AppDeploy and Autonomy APIs.

## Global Constraints

- Do not modify the AppDeploy repository or move final VM Target and Runtime Adapter selection into geon.
- Use AppDeploy commit `9b91502` as the verified compatibility baseline. Its new VM scheduler boundary is additive and does not replace `POST /api/v1/deployments`.
- Keep Manifest generation usable when AppDeploy is unavailable.
- Preserve the current `ControlRun` as the authoritative source for request, Guard, Agent, Manifest, deployment, logs, and correlation evidence.
- Do not duplicate ControlRun stages into a second automatic Feedback database.
- Keep the existing Automation Feedback POST contract for approved external executor callbacks.
- Keep Autonomous Loop optional and available only for a `DEPLOYED` Run with a non-empty `deployment_id`.
- Do not add Job Scheduling, a `JobSchedulingAgent`, a multi-Agent DAG, or a general workflow engine.
- Do not implement online Qwen retraining or automatic fine-tuning.
- Preserve the untracked `config/inference_optimization.json` without staging, editing, or deleting it.
- Use TDD for every behavior change.

---

### Task 1: Align geon with the latest AppDeploy response contract

**Files:**
- Modify: `go/service-control-api/internal/appdeploy/models.go`
- Modify: `go/service-control-api/internal/appdeploy/client_test.go`
- Modify: `go/service-control-api/internal/api/control_run_api_test.go`

**Interfaces:**
- Consumes: AppDeploy `POST /api/v1/deployments`
- Consumes: AppDeploy `GET /api/v1/deployments/{deployment_id}`
- Produces: `PlacementDecision`
- Produces: `ResourceAllocation`
- Extends: `DeploymentResponse.Placement`

- [ ] **Step 1: Record the verified AppDeploy compatibility boundary**

Use the current AppDeploy `AppDeploy/internal/model/types.go` contract from commit `9b91502`:

```text
DeploymentCreateRequest.manifest
DeploymentResponse.target_profile_id
DeploymentResponse.placement
DeploymentResponse.runtime_id
PlacementDecision.target_vm_id
PlacementDecision.target_profile_id
PlacementDecision.allocation
PlacementDecision.source
PlacementDecision.score
PlacementDecision.reason
PlacementDecision.selected_at
PlacementDecision.external_decision_id
```

The new `internal/scheduler` package is an additive VM-side scheduling boundary. geon must not call it directly or move AppDeploy Target selection into geon.

- [ ] **Step 2: Write a failing latest-contract client test**

Extend `TestClientUsesAppDeployDeploymentContract` so the create request still contains only `manifest` and has no required Target hint. Make the status fixture return:

```json
{
  "request_id": "req-2",
  "deployment_id": "dep-1",
  "app_version_id": "appver-test",
  "target_profile_id": "target-gpu-001",
  "status": "RUNNING",
  "runtime_id": "runtime-gpu-vm",
  "placement": {
    "target_vm_id": "vm-gpu-001",
    "target_profile_id": "target-gpu-001",
    "allocation": {
      "cpu_cores": 2,
      "memory_bytes": 4294967296,
      "gpu_count": 1,
      "storage_bytes": 10737418240
    },
    "source": "local",
    "score": 0.92,
    "reason": "matched GPU and resource requirements",
    "selected_at": "2026-07-27T05:00:00Z"
  }
}
```

Assert:

```go
if status.TargetProfileID != "target-gpu-001" ||
    status.Placement == nil ||
    status.Placement.TargetVMID != "vm-gpu-001" ||
    status.Placement.Allocation.GPUCount != 1 ||
    status.RuntimeID != "runtime-gpu-vm" {
    t.Fatalf("latest AppDeploy response was not decoded: %#v", status)
}
```

- [ ] **Step 3: Run the focused test and verify failure**

```bash
cd go/service-control-api
go test ./internal/appdeploy -run TestClientUsesAppDeployDeploymentContract -count=1
```

Expected: FAIL because geon currently ignores `placement` and `runtime_id`.

- [ ] **Step 4: Add compatible response models**

Add to `internal/appdeploy/models.go`:

```go
type ResourceAllocation struct {
    CPUCores     float64 `json:"cpu_cores,omitempty"`
    MemoryBytes  int64   `json:"memory_bytes,omitempty"`
    GPUCount     int     `json:"gpu_count,omitempty"`
    StorageBytes int64   `json:"storage_bytes,omitempty"`
}

type PlacementDecision struct {
    TargetVMID         string             `json:"target_vm_id"`
    TargetProfileID    string             `json:"target_profile_id,omitempty"`
    Allocation         ResourceAllocation `json:"allocation"`
    Source             string             `json:"source"`
    Score              float64            `json:"score"`
    Reason             string             `json:"reason"`
    SelectedAt         time.Time          `json:"selected_at"`
    ExternalDecisionID string             `json:"external_decision_id,omitempty"`
}
```

Extend `DeploymentResponse`:

```go
Placement *PlacementDecision `json:"placement,omitempty"`
RuntimeID string             `json:"runtime_id,omitempty"`
```

Do not add any required request field and do not send `runtime_profile_id`.

- [ ] **Step 5: Preserve scheduler-selected Target evidence in ControlRun**

Update the existing ControlRun API fixture to return `placement` and `runtime_id`, then assert the deployment evidence stored and returned by geon contains:

```text
target_profile_id = target-selected
placement.target_vm_id = vm-selected
placement.source = local
runtime_id = runtime-selected
```

No Target hint is required in the original geon request.

- [ ] **Step 6: Run compatibility tests**

```bash
go test ./internal/appdeploy -count=1
go test ./internal/api -run TestControlRunAPISubmitsApprovedManifest -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add \
  go/service-control-api/internal/appdeploy/models.go \
  go/service-control-api/internal/appdeploy/client_test.go \
  go/service-control-api/internal/api/control_run_api_test.go
git commit -m "feat: align geon with appdeploy placement responses"
```

---

### Task 2: Return diagnostic Agent Guard rejection responses

**Files:**
- Modify: `go/service-control-api/internal/api/agent_execution_models.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`
- Modify: `go/service-control-api/docs/swagger/docs.go`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`

**Interfaces:**
- Consumes: `AgentExecutionResponse`, `GuardDecision`
- Produces: `AgentExecutionErrorResponse`
- Produces: `agentExecutionError(message string, result AgentExecutionResponse) AgentExecutionErrorResponse`

- [ ] **Step 1: Write a failing rejected-result API test**

Add `TestExternalAgentExecutionAPIRejectionReturnsRunAndGuardReason` to `internal/api/server_test.go`.

Use an `httptest.Server` runtime Agent that returns a completed result with a deliberately wrong `run_id`:

```go
external := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
    var dispatched AgentDispatchRequest
    if err := json.NewDecoder(request.Body).Decode(&dispatched); err != nil {
        t.Fatalf("decode Agent request: %v", err)
    }
    writer.Header().Set("Content-Type", "application/json")
    fmt.Fprintf(writer, `{
        "run_id":"run-mismatched",
        "agent":%q,
        "status":"completed",
        "proposal":{"action":%q},
        "result":{"review":"approved"},
        "domain_validation":"not_registered"
    }`, dispatched.Agent, dispatched.Action)
}))
```

Register `ExternalResearchAgent` with capability `deployment_review` and bounded action `review_deployment_plan`, then POST to `/api/v1/agents/ExternalResearchAgent/execute`.

Assert:

```go
if response.Code != http.StatusUnprocessableEntity {
    t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
}
result := decodeObject(t, response.Body.Bytes())
if !strings.HasPrefix(result["run_id"].(string), "run-") {
    t.Fatalf("missing run_id: %#v", result)
}
if result["message"] != "Agent result was rejected" {
    t.Fatalf("unexpected message: %#v", result)
}
if !strings.Contains(result["reason"].(string), "run_id does not match") {
    t.Fatalf("missing Guard reason: %#v", result)
}
```

- [ ] **Step 2: Run the focused test and verify failure**

```bash
cd go/service-control-api
go test ./internal/api -run TestExternalAgentExecutionAPIRejectionReturnsRunAndGuardReason -count=1
```

Expected: FAIL because the current `jsonError` response contains only `valid` and `message`.

- [ ] **Step 3: Add the diagnostic error contract**

Add to `agent_execution_models.go`:

```go
type AgentExecutionErrorResponse struct {
    AgentExecutionResponse
    Valid   bool   `json:"valid"`
    Message string `json:"message"`
    Reason  string `json:"reason,omitempty"`
}

func agentExecutionError(
    message string,
    result AgentExecutionResponse,
) AgentExecutionErrorResponse {
    reason := result.ResultGuard.Reason
    if reason == "" {
        reason = result.RequestGuard.Reason
    }
    return AgentExecutionErrorResponse{
        AgentExecutionResponse: result,
        Valid:                  false,
        Message:                message,
        Reason:                 reason,
    }
}
```

Do not include the Go error string because it may contain upstream transport details. Return only Guard reasons already intended for the user.

- [ ] **Step 4: Return the diagnostic body for authorization and result rejection**

Change `RestPostAgentExecute`:

```go
case errors.Is(err, errAgentExecutionUnauthorized):
    return context.JSON(
        http.StatusForbidden,
        agentExecutionError("Agent execution was not authorized", result),
    )
case errors.Is(err, errAgentExecutionResultRejected):
    return context.JSON(
        http.StatusUnprocessableEntity,
        agentExecutionError("Agent result was rejected", result),
    )
```

Update Swagger annotations:

```go
// @Failure 403 {object} AgentExecutionErrorResponse
// @Failure 422 {object} AgentExecutionErrorResponse
```

- [ ] **Step 5: Run focused and package tests**

```bash
go test ./internal/api -run 'TestExternalAgentExecutionAPI(RejectionReturnsRunAndGuardReason|Flow)' -count=1
go test ./internal/api -count=1
```

Expected: PASS.

- [ ] **Step 6: Regenerate Swagger**

From the repository root:

```bash
make swag
```

Confirm `AgentExecutionErrorResponse` appears in all three generated Swagger files and `ExecuteAgent` uses it for HTTP 403 and 422.

- [ ] **Step 7: Commit**

```bash
git add \
  go/service-control-api/internal/api/agent_execution_models.go \
  go/service-control-api/internal/api/server.go \
  go/service-control-api/internal/api/server_test.go \
  go/service-control-api/docs/swagger/docs.go \
  go/service-control-api/docs/swagger/swagger.json \
  go/service-control-api/docs/swagger/swagger.yaml
git commit -m "fix: expose agent guard rejection evidence"
```

---

### Task 3: Make Manifest Workflow the first web screen

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Produces: `MANIFEST_STAGE_ORDER`
- Produces: `renderManifestStageFlow(run)`
- Consumes: existing `controlrun.Run.stages`

- [ ] **Step 1: Replace the old navigation-order test with failing user-first contracts**

Add or update tests in `internal/webui/webui_test.go`:

```go
func TestControlAppStartsWithManifestWorkflow(t *testing.T)
func TestControlAppShowsManifestStagesInExecutionOrder(t *testing.T)
```

The first test must assert:

```go
html := requestBody(t, server, "/")
plannerNav := strings.Index(html, `data-view-target="planner"`)
agentsNav := strings.Index(html, `data-view-target="agents"`)
autonomyNav := strings.Index(html, `data-view-target="autonomy"`)
feedbackNav := strings.Index(html, `data-view-target="feedback"`)
guideNav := strings.Index(html, `data-view-target="overview"`)

if !(plannerNav < agentsNav && agentsNav < autonomyNav &&
    autonomyNav < feedbackNav && feedbackNav < guideNav) {
    t.Fatalf("unexpected user workflow navigation order")
}
if !strings.Contains(html, `<section class="view is-active" data-view="planner">`) {
    t.Fatal("Manifest Workflow must be the default view")
}
if !strings.Contains(html, `<section class="view" data-view="overview" hidden>`) {
    t.Fatal("Overview must be a separate Guide view")
}
```

The second test must assert the HTML and JavaScript contain, in order:

```text
user_request
request_guard
agent_registry
agent_dispatch
qwen_planner
manifest_guard
```

- [ ] **Step 2: Run the focused web tests and verify failure**

```bash
cd go/service-control-api
go test ./internal/webui -run 'TestControlApp(StartsWithManifestWorkflow|ShowsManifestStagesInExecutionOrder)' -count=1
```

Expected: FAIL because Overview and Agents currently precede Planner.

- [ ] **Step 3: Reorder and relabel the navigation**

In `index.html`, use this order:

```html
<button class="nav-item is-active" type="button" data-view-target="planner" aria-label="Manifest Workflow">
  <i data-lucide="route" aria-hidden="true"></i><span>Manifest Workflow</span>
</button>
<button class="nav-item" type="button" data-view-target="agents" aria-label="Agents &amp; Guard">
  <i data-lucide="shield-check" aria-hidden="true"></i><span>Agents &amp; Guard</span>
</button>
<button class="nav-item" type="button" data-view-target="autonomy" aria-label="Post-deployment">
  <i data-lucide="repeat-2" aria-hidden="true"></i><span>Post-deployment</span>
</button>
<button class="nav-item" type="button" data-view-target="feedback" aria-label="Feedback">
  <i data-lucide="activity" aria-hidden="true"></i><span>Feedback</span>
</button>
<button class="nav-item" type="button" data-view-target="overview" aria-label="Guide">
  <i data-lucide="book-open" aria-hidden="true"></i><span>Guide</span>
</button>
```

Move the Planner section before Overview in the document and mark Planner as `is-active`. Mark Overview hidden. Keep data-view names unchanged to avoid breaking existing event delegation.

Update labels in `app.js`:

```js
const VIEW_LABELS = Object.freeze({
  planner: ["USER REQUEST TO GUARDED MANIFEST", "Manifest Workflow"],
  agents: ["AGENT REGISTRY AND GO GUARD", "Agents & Guard"],
  autonomy: ["OPTIONAL POST-DEPLOYMENT EXPERIMENT", "Post-deployment"],
  feedback: ["AUTOMATIC RUN EVIDENCE", "Feedback"],
  overview: ["WORKFLOW GUIDE", "Guide"],
});
```

Set:

```js
activeView: "planner",
```

- [ ] **Step 4: Add the Manifest stage flow**

Add above the Planner result summary:

```html
<ol class="manifest-stage-flow" id="manifest-stage-flow" aria-label="Manifest 처리 단계"></ol>
```

Add to `app.js`:

```js
const MANIFEST_STAGE_ORDER = Object.freeze([
  { key: "user_request", label: "사용자 요청" },
  { key: "request_guard", label: "Request Guard" },
  { key: "agent_registry", label: "Agent Registry" },
  { key: "agent_dispatch", label: "Agent 실행" },
  { key: "qwen_planner", label: "Qwen Planner" },
  { key: "manifest_guard", label: "Manifest Guard" },
]);
```

Implement:

```js
function renderManifestStageFlow(run) {
  const flow = byID("manifest-stage-flow");
  const stages = new Map((run?.stages || []).map((stage) => [stage.name, stage]));
  const blocked = (run?.stages || []).some((stage) => stage.status === "rejected");
  flow.replaceChildren();

  MANIFEST_STAGE_ORDER.forEach((definition, index) => {
    const stage = definition.key === "user_request"
      ? (run ? { status: "approved", reason: "ControlRun request received" } : null)
      : stages.get(definition.key);
    const item = createElement("li", "manifest-stage");
    item.dataset.status = stage?.status || (blocked ? "blocked" : "pending");
    item.append(
      createElement("span", "manifest-stage-index", String(index + 1)),
      createElement("strong", "", definition.label),
      createElement("small", "", stage?.reason || (blocked ? "이전 단계에서 중단" : "대기 중")),
    );
    flow.append(item);
  });
}
```

Call it from `renderPlanner`, `renderControlRunTimeline`, and the empty-state path.

- [ ] **Step 5: Style the stage flow without shifting the existing layout**

Use a stable six-column grid on desktop and a single-column flow on mobile:

```css
.manifest-stage-flow {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line);
  background: var(--line);
}

.manifest-stage {
  display: grid;
  min-width: 0;
  min-height: 92px;
  align-content: start;
  gap: 5px;
  padding: 12px;
  background: var(--surface);
}

@media (max-width: 760px) {
  .manifest-stage-flow {
    grid-template-columns: 1fr;
  }
  .manifest-stage {
    min-height: 0;
  }
}
```

Use existing status colors for `approved`, `rejected`, `blocked`, and `pending`. Do not add decorative cards inside the Planner panel.

- [ ] **Step 6: Run focused and complete web tests**

```bash
go test ./internal/webui -run 'TestControlApp(StartsWithManifestWorkflow|ShowsManifestStagesInExecutionOrder)' -count=1
go test ./internal/webui -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add \
  go/service-control-api/internal/webui/static/index.html \
  go/service-control-api/internal/webui/static/app.js \
  go/service-control-api/internal/webui/static/app.css \
  go/service-control-api/internal/webui/webui_test.go
git commit -m "feat: make manifest workflow the control app entry"
```

---

### Task 4: Synchronize one active ControlRun across all views

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Produces: `ACTIVE_RUN_KEY`
- Produces: `activeControlRun()`
- Produces: `setActiveControlRun(run)`
- Produces: `isDeployedRun(run)`
- Produces: `renderPostDeploymentReadiness(run)`

- [ ] **Step 1: Write failing active-Run web contracts**

Add `TestControlAppConnectsSelectedRunAcrossRegistryAndPostDeployment`.

Assert HTML contains:

```text
id="selected-run-id"
id="selected-planner-agent"
id="post-deployment-readiness"
```

Assert JavaScript contains:

```text
const ACTIVE_RUN_KEY
function setActiveControlRun
function activeControlRun
function renderPostDeploymentReadiness
data-selected-agent
```

- [ ] **Step 2: Run the focused test and verify failure**

```bash
go test ./internal/webui -run TestControlAppConnectsSelectedRunAcrossRegistryAndPostDeployment -count=1
```

Expected: FAIL because the current selection is only partially copied into forms.

- [ ] **Step 3: Add one authoritative web selection helper**

Add:

```js
const ACTIVE_RUN_KEY = "geon-agent-control-active-run-id";

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
}
```

Use this helper in `renderControlRuns`, `selectControlRun`, Planner success/error handling, autonomy Run selection, Run deletion, and clear-all handling.

When loading Run records, restore:

```js
const remembered = localStorage.getItem(ACTIVE_RUN_KEY);
const selected = runs.find((run) => run.run_id === remembered) || runs[0] || null;
setActiveControlRun(selected);
```

If the remembered ID does not exist after server restart, remove it.

- [ ] **Step 4: Highlight the Registry Agent selected by the Run**

In `renderAgents`:

```js
const selectedAgent = activeControlRun()?.selected_agent?.name || "";
row.dataset.selectedAgent = String(agent.name === selectedAgent);
```

Add a small `selected by active Run` label to the selected row. Style it with the existing teal accent and no new card container.

- [ ] **Step 5: Gate Post-deployment using the selected Run**

Add near the Autonomy header:

```html
<div class="readiness-banner" id="post-deployment-readiness" data-status="blocked">
  먼저 승인된 Manifest를 AppDeploy에 제출하고 배포 완료 상태를 확인하세요.
</div>
```

Implement:

```js
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
}
```

Update `syncRunLinkedForms` to clear stale Action and Autonomy IDs when a non-deployed Run is selected.

- [ ] **Step 6: Run web tests**

```bash
go test ./internal/webui -run 'TestControlApp(ConnectsSelectedRun|StartsWithManifestWorkflow)' -count=1
go test ./internal/webui -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add \
  go/service-control-api/internal/webui/static/index.html \
  go/service-control-api/internal/webui/static/app.js \
  go/service-control-api/internal/webui/static/app.css \
  go/service-control-api/internal/webui/webui_test.go
git commit -m "feat: synchronize control run across agent views"
```

---

### Task 5: Render automatic run Feedback without duplicate storage

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Produces: `automaticFeedbackEntries(run)`
- Produces: `renderAutomaticRunFeedback(run)`
- Produces: `loadFeedbackView()`
- Consumes: `GET /api/v1/control-runs`
- Consumes: `GET /api/v1/automation/feedback`
- Consumes: `GET /api/v1/autonomy/events`

- [ ] **Step 1: Write a failing automatic Feedback web contract**

Add `TestControlAppSeparatesAutomaticFeedbackFromExecutorCallback`.

Assert HTML contains:

```text
id="automatic-feedback-summary"
id="automatic-feedback-list"
id="automatic-feedback-json"
id="external-feedback-disclosure"
External Executor Callback Test
```

Assert JavaScript contains:

```text
function automaticFeedbackEntries
function renderAutomaticRunFeedback
function loadFeedbackView
record.run_id === run.run_id
event.run_id === run.run_id
```

- [ ] **Step 2: Run the focused test and verify failure**

```bash
go test ./internal/webui -run TestControlAppSeparatesAutomaticFeedbackFromExecutorCallback -count=1
```

Expected: FAIL because Feedback currently shows only the manual callback form and recorded callback list.

- [ ] **Step 3: Restructure the Feedback HTML**

Make `Automatic Run Feedback` the primary Feedback content:

```html
<section class="panel automatic-feedback-panel" aria-labelledby="automatic-feedback-title">
  <div class="panel-header">
    <div>
      <p class="section-label">CONTROL RUN EVIDENCE</p>
      <h3 id="automatic-feedback-title">Automatic Run Feedback</h3>
    </div>
    <select id="feedback-run-id" aria-label="Feedback ControlRun"></select>
  </div>
  <div class="automatic-feedback-summary" id="automatic-feedback-summary">
    선택된 ControlRun이 없습니다.
  </div>
  <div class="activity-list" id="automatic-feedback-list"></div>
  <pre class="json-output" id="automatic-feedback-json">{}</pre>
</section>
```

Wrap the existing POST form and response JSON in:

```html
<details class="panel executor-feedback-disclosure" id="external-feedback-disclosure">
  <summary>
    <span>
      <span class="section-label">ADVANCED TEST</span>
      <strong>External Executor Callback Test</strong>
    </span>
    <i data-lucide="chevron-down" aria-hidden="true"></i>
  </summary>
  <!-- existing verified callback form and response -->
</details>
```

Keep recorded external callbacks below the disclosure, but label them `Verified Executor Feedback`.

- [ ] **Step 4: Aggregate existing evidence by run_id**

Add `autonomyEvents: []` to `state`.

Implement:

```js
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
```

Implement `renderAutomaticRunFeedback(run)` to:

- Show Run status, app version, selected Agent, actual model, Manifest Guard state, deployment ID, and log count.
- Render each aggregated entry as a row.
- Put the selected Run, matching executor records, matching autonomy events, and computed entries into `automatic-feedback-json`.
- Display an empty state when no Run is selected.

Extend the Task 4 `setActiveControlRun(run)` helper after `renderAutomaticRunFeedback` exists:

```js
function setActiveControlRun(run) {
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
```

- [ ] **Step 5: Load Feedback automatically**

Implement:

```js
async function loadFeedbackView() {
  const [feedbackPayload, autonomyPayload] = await Promise.all([
    apiRequest(API.feedback),
    apiRequest(API.autonomyEvents),
  ]);
  state.feedbackRecords = Array.isArray(feedbackPayload.feedback)
    ? feedbackPayload.feedback
    : [];
  state.autonomyEvents = Array.isArray(autonomyPayload.events)
    ? autonomyPayload.events
    : [];
  renderAutomationFeedback(feedbackPayload);
  renderAutomaticRunFeedback(activeControlRun());
}
```

Update `switchView("feedback")`, `refreshDashboard`, Feedback POST/delete/clear, Planner completion, AppDeploy submission, and Run selection to refresh this projection without requiring a manual Feedback POST.

The `feedback-run-id` change handler must call `setActiveControlRun` with the selected Run.

- [ ] **Step 6: Run web tests**

```bash
go test ./internal/webui -run 'TestControlApp(SeparatesAutomaticFeedback|ConnectsSelectedRun)' -count=1
go test ./internal/webui -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add \
  go/service-control-api/internal/webui/static/index.html \
  go/service-control-api/internal/webui/static/app.js \
  go/service-control-api/internal/webui/static/app.css \
  go/service-control-api/internal/webui/webui_test.go
git commit -m "feat: show automatic control run feedback"
```

---

### Task 6: Update run guides and perform end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`
- Modify: `go/service-control-api/internal/webui/webui_test.go` only if an explicit final contract is missing

**Interfaces:**
- Documents: user-first Manifest Workflow
- Documents: automatic Feedback versus external executor callback
- Documents: optional AppDeploy and Post-deployment boundary

- [ ] **Step 1: Write a failing documentation contract**

Extend `TestControlAppContainsOrderedExperimentGuide` or add `TestControlAppGuideMatchesUserRequestFirstWorkflow`.

Assert the served Guide contains the exact order:

```text
사용자 요청
Request Guard
Agent Registry
Agent Dispatcher
Qwen Planner
Manifest Guard
DeploymentManifest
```

Assert it separately labels:

```text
핵심 Manifest 실험
선택적 AppDeploy 제출
선택적 배포 후 실험
Automatic Run Feedback
```

- [ ] **Step 2: Run the focused test and verify failure if the Guide is incomplete**

```bash
cd go/service-control-api
go test ./internal/webui -run TestControlAppGuideMatchesUserRequestFirstWorkflow -count=1
```

Expected: FAIL until the Guide text exactly reflects the implemented flow.

- [ ] **Step 3: Update the Guide and README files**

Document this exact user procedure:

```text
1. Open Manifest Workflow.
2. Enter the natural-language request and app_version_id.
3. Generate a ControlRun.
4. Observe Request Guard, Agent Registry, Dispatcher, Qwen Planner, and Manifest Guard.
5. Inspect the approved DeploymentManifest.
6. Optionally submit it to AppDeploy.
7. Use Post-deployment only after DEPLOYED.
8. Open Feedback to inspect automatic evidence for the same run_id.
```

State that:

- Agent Registry is a managed internal step and supporting screen, not the user entry point.
- Automatic Feedback is a read projection of existing Run evidence and does not retrain Qwen.
- External Executor Callback Test is optional and requires an approved `correlation_id`.
- AppDeploy remains a separate server and repository.

- [ ] **Step 4: Run all Go tests and static analysis**

```bash
cd go/service-control-api
go test ./... -count=1
go vet ./...

cd ../aiops-guard
go test ./... -count=1
go vet ./...
```

Expected: all commands exit 0.

- [ ] **Step 5: Start the current geon server and run a real Qwen Manifest request**

Use the repository root, configured Ollama candidate, and port `18080`:

```bash
export AIOPS_REPO_ROOT="$(git rev-parse --show-toplevel)"
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
export AIOPS_PLANNER_GUARD_POLICY_PATH="config/planner_guard_policy.json"
export AIOPS_BIND_ADDRESS="127.0.0.1"
export PORT=18080

cd "$AIOPS_REPO_ROOT/go/service-control-api"
go run ./cmd/service-control-api
```

Submit through the web:

```text
Mock 환경에서 CPU 1, 메모리 1Gi, GPU 0, 스토리지 1Gi인 AI 응용 배포 계획을 생성해 주세요.
```

Use:

```text
app_version_id: appver-example
candidate_id: qwen3.5-ops-planner
agent_name: AIApplicationAutomationAgent
```

Expected:

```text
status: MANIFEST_APPROVED
selected_agent.name: AIApplicationAutomationAgent
generation.actual_model: qwen3.5:4b
generation.guard_valid: true
manifest.kind: DeploymentManifest
```

- [ ] **Step 6: Verify the browser workflow on desktop and mobile**

Check `1440x900` and `390x844`.

Desktop and mobile assertions:

- Manifest Workflow is the first and active menu.
- The six-stage flow is visible in the correct order.
- Agent Registry highlights `AIApplicationAutomationAgent` for the selected Run.
- Feedback automatically displays the Request Guard, Registry, Dispatcher, Qwen, and Manifest Guard stages without submitting the callback form.
- Post-deployment is blocked for `MANIFEST_APPROVED` without a deployment ID.
- `Agent result was rejected` responses display `run_id` and the concrete Guard reason.
- No buttons, labels, JSON blocks, or timeline rows overlap.
- `document.documentElement.scrollWidth <= document.documentElement.clientWidth`.
- Browser console has no errors.

AppDeploy does not need to be running for this verification. If it is running, additionally submit the Manifest and confirm `deployment_id`, deployment status, and logs appear in Automatic Run Feedback.

- [ ] **Step 7: Verify repository scope**

```bash
git status --short
git diff --check
git diff --name-only origin/geon...HEAD
```

Confirm:

- No AppDeploy repository file appears.
- `config/inference_optimization.json` remains untracked and unmodified.
- Only geon service-control, generated Swagger, README, spec, and plan files appear.

- [ ] **Step 8: Commit documentation and final contract adjustments**

```bash
git add \
  README.md \
  go/service-control-api/README.md \
  go/service-control-api/internal/webui/webui_test.go
git commit -m "docs: explain user-first control workflow"
```
