# Simplified KHU AI Application Automation PoC Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current platform-like geon web UI into a three-screen research PoC that independently produces guarded deployment decisions, desired deployment specifications, and minimal post-deployment scaling decisions.

**Architecture:** Keep the existing Go Agent Control service and compatibility APIs authoritative. Add a small deterministic scaling evaluator to the existing `agentcontrol.Flow`, then replace the web entry points with one joined-input automation form, one policy-oriented Registry screen, and one experiment-results screen. Remove legacy UI code without deleting its backend APIs.

**Tech Stack:** Go 1.26, Echo, embedded HTML/CSS/vanilla JavaScript, Common JSON v1.0, Qwen through the existing candidate adapter, Go Guard, Go tests, Node syntax check, Playwright browser verification.

## Global Constraints

- Work only in `C:\Users\geonhae\Documents\Kyunghee-aiops-go` on the existing `geon` branch.
- Unless a step says otherwise, run implementation commands from
  `C:\Users\geonhae\Documents\Kyunghee-aiops-go\go\service-control-api`.
- Do not create another branch or modify the AppDeploy checkout.
- Do not stage, edit, or delete `config/inference_optimization.json`.
- Keep existing AppDeploy, ControlRun, autonomy, action-proposal, and executor-feedback APIs available for compatibility.
- Do not expose AppDeploy submission, VM lifecycle, platform Manifest conversion, or Autonomous Loop controls in the simplified web.
- The core local experiment must run without AppDeploy, CB-Tumblebug, or a VM.
- The web must contain exactly three primary destinations: `자동화 에이전트`, `Agent 및 정책`, and `실험 결과`.
- Agent Registry authorization and Go Guard execute automatically inside the core workflow.
- All visible Korean text must remain UTF-8 and readable.
- `DesiredDeploymentSpec` remains platform-neutral; actual deployment execution belongs to an external executor.
- Use focused tests before implementation and preserve all pre-existing passing API contracts.

---

## File Structure

### Backend decision mechanism

- `go/service-control-api/internal/agentcontrol/models.go`
  Defines scaling actions and the `ScalingDecision` evidence attached to a Flow.
- `go/service-control-api/internal/agentcontrol/scaling.go`
  Contains the pure deterministic scaling evaluator.
- `go/service-control-api/internal/agentcontrol/scaling_test.go`
  Verifies `NO_ACTION`, `SCALE_OUT`, and `SCALE_IN`.
- `go/service-control-api/internal/agentcontrol/service.go`
  Re-evaluates scaling after deployment status or optimization feedback is received.
- `go/service-control-api/internal/agentcontrol/service_test.go`
  Verifies scaling evidence is persisted in the same correlated Flow.

### Flow record lifecycle

- `go/service-control-api/internal/agentcontrol/service.go`
  Adds concurrency-safe delete-one and clear-all Flow operations.
- `go/service-control-api/internal/api/agent_control_api.go`
  Exposes `DELETE /api/v1/agent-control/flows/{correlation_id}` and `DELETE /api/v1/agent-control/flows`.
- `go/service-control-api/internal/api/agent_control_api_test.go`
  Verifies delete responses and that deleted records are no longer listed.
- `go/service-control-api/internal/api/server.go`
  Registers the two delete routes.

### Simplified web

- `go/service-control-api/internal/webui/static/index.html`
  Contains only the three primary views and Agent registration dialog.
- `go/service-control-api/internal/webui/static/app.js`
  Runs the joined-input flow, renders Registry policies, and manages experiment results.
- `go/service-control-api/internal/webui/static/app.css`
  Styles the three-view research interface and removes unused legacy selectors.
- `go/service-control-api/internal/webui/webui_test.go`
  Defines the three-view UI contract and asserts removed platform-oriented elements are absent.

### Contracts and evidence

- `docs/submission/openapi_service_control.yaml`
  Documents `ScalingDecision` and Agent Control Flow deletion.
- `go/service-control-api/internal/api/openapi_contract_test.go`
  Locks the updated submission contract.
- `README.md`
  Describes the minimal independent PoC and three-screen experiment.
- `go/service-control-api/README.md`
  Documents exact local execution and API verification commands.
- `docs/evidence/agent_control_mvp_20260729.md`
  Records the final deployment and scaling decision evidence.

---

### Task 1: Preserve the Agent Registry Authorization Baseline

**Files:**
- Modify: `config/agent_registry.json`
- Modify: `go/service-control-api/internal/agentcontrol/models.go`
- Modify: `go/service-control-api/internal/agentcontrol/service.go`
- Modify: `go/service-control-api/internal/agentcontrol/service_test.go`
- Create: `go/service-control-api/internal/api/agent_control_authorizer.go`
- Create: `go/service-control-api/internal/api/agent_control_authorizer_test.go`
- Modify: `go/service-control-api/internal/api/agent_control_api_test.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`

**Interfaces:**
- Consumes: `config/agent_registry.json` Agent profiles.
- Produces: `Flow.AgentAuthorization *AgentAuthorization` for
  `AIApplicationAutomationAgent`, capability `ai_application_automation`, and
  bounded action `generate_deployment_decision`.

- [ ] **Step 1: Verify the existing focused authorization tests**

Run:

```powershell
go test ./internal/agentcontrol ./internal/api -run 'TestServiceRejectsFlowWhenAutomationAgentIsNotAuthorized|TestAgentControlRegistryAuthorizerRejectsMissingCapability|TestAgentControlInputJoinAPI' -count=1
```

Expected: PASS. These tests already exist in the dirty worktree and prove that an unauthorized Agent cannot create a deployment plan or request.

- [ ] **Step 2: Verify the Registry profile carries the required policy**

Run:

```powershell
rg -n '"ai_application_automation"|"generate_deployment_decision"' ../../config/agent_registry.json
```

Expected: both strings are present under `AIApplicationAutomationAgent`.

- [ ] **Step 3: Stage only the authorization baseline**

Run:

```powershell
git add -- `
  ../../config/agent_registry.json `
  internal/agentcontrol/models.go `
  internal/agentcontrol/service.go `
  internal/agentcontrol/service_test.go `
  internal/api/agent_control_authorizer.go `
  internal/api/agent_control_authorizer_test.go `
  internal/api/agent_control_api_test.go `
  internal/api/service.go `
  ../../docs/submission/openapi_service_control.yaml `
  internal/api/openapi_contract_test.go
git diff --cached --check
```

Expected: no whitespace errors and no staged `config/inference_optimization.json`.

- [ ] **Step 4: Commit the authorization baseline**

Run:

```powershell
git commit -m "feat: authorize KHU automation Agent before planning"
```

Expected: one commit containing the current Registry authorization implementation, while web simplification files remain unstaged.

---

### Task 2: Add Minimal Scaling Decisions to Agent Control Flows

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/models.go`
- Create: `go/service-control-api/internal/agentcontrol/scaling.go`
- Create: `go/service-control-api/internal/agentcontrol/scaling_test.go`
- Modify: `go/service-control-api/internal/agentcontrol/service.go`
- Modify: `go/service-control-api/internal/agentcontrol/service_test.go`

**Interfaces:**
- Consumes: `Flow.ApplicationContext`, `Flow.DeploymentPlan`,
  `Flow.DeploymentStatus`, and `Flow.OptimizationFeedback`.
- Produces:

```go
type ScalingDecision struct {
	Action          string   `json:"action"`
	Reason          string   `json:"reason"`
	CurrentReplicas int      `json:"current_replicas"`
	DesiredReplicas int      `json:"desired_replicas"`
	Evidence        []string `json:"evidence,omitempty"`
	CreatedAt       string   `json:"created_at"`
}
```

- [ ] **Step 1: Write failing pure evaluator tests**

Create `internal/agentcontrol/scaling_test.go` with table-driven cases:

```go
func TestEvaluateScalingDecision(t *testing.T) {
	tests := []struct {
		name       string
		flow       Flow
		wantAction string
		want       int
	}{
		{
			name:       "SLO violation scales out within maximum",
			flow:       scalingTestFlow(1, 1, 3, 85, 92, []string{"latency_p95_ms"}),
			wantAction: ScalingActionScaleOut,
			want:       2,
		},
		{
			name:       "healthy low utilization scales in above minimum",
			flow:       scalingTestFlow(2, 1, 3, 18, 22, nil),
			wantAction: ScalingActionScaleIn,
			want:       1,
		},
		{
			name:       "healthy workload at minimum remains unchanged",
			flow:       scalingTestFlow(1, 1, 3, 55, 61, nil),
			wantAction: ScalingActionNoAction,
			want:       1,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			decision := evaluateScalingDecision(test.flow, fixedScalingTime)
			if decision.Action != test.wantAction || decision.DesiredReplicas != test.want {
				t.Fatalf("decision = %#v, want action=%s replicas=%d", decision, test.wantAction, test.want)
			}
		})
	}
}
```

The helper must construct a RUNNING deployment and matching successful optimization feedback with the supplied accelerator and CPU utilization.

- [ ] **Step 2: Run the evaluator test and verify failure**

Run:

```powershell
go test ./internal/agentcontrol -run TestEvaluateScalingDecision -count=1
```

Expected: FAIL because the scaling constants, type, and evaluator do not exist.

- [ ] **Step 3: Add scaling constants and Flow evidence**

Add to `models.go`:

```go
const (
	ScalingActionNoAction = "NO_ACTION"
	ScalingActionScaleOut = "SCALE_OUT"
	ScalingActionScaleIn  = "SCALE_IN"
)

type ScalingDecision struct {
	Action          string   `json:"action"`
	Reason          string   `json:"reason"`
	CurrentReplicas int      `json:"current_replicas"`
	DesiredReplicas int      `json:"desired_replicas"`
	Evidence        []string `json:"evidence,omitempty"`
	CreatedAt       string   `json:"created_at"`
}
```

Add to `Flow`:

```go
ScalingDecision *ScalingDecision `json:"scaling_decision,omitempty"`
```

- [ ] **Step 4: Implement the deterministic evaluator**

Create `scaling.go` with these ordered rules:

```go
func evaluateScalingDecision(flow Flow, now time.Time) *ScalingDecision {
	if flow.DeploymentStatus == nil {
		return nil
	}

	current, minimum, maximum := scalingReplicaBounds(flow)
	decision := &ScalingDecision{
		Action:          ScalingActionNoAction,
		CurrentReplicas: current,
		DesiredReplicas: current,
		CreatedAt:       now.UTC().Format(time.RFC3339Nano),
	}
	status := flow.DeploymentStatus.Data.DeploymentStatus
	if status.State != DeploymentStateRunning {
		decision.Reason = "Deployment is not RUNNING; scaling is not evaluated."
		decision.Evidence = []string{"deployment_state=" + status.State}
		return decision
	}
	if flow.OptimizationFeedback == nil {
		decision.Reason = "Runtime metrics are required before scaling evaluation."
		decision.Evidence = []string{"optimization_feedback=missing"}
		return decision
	}

	feedback := flow.OptimizationFeedback.Data.OptimizationFeedback
	violated := len(feedback.SLOViolations) > 0 || violatesProfileSLO(flow, feedback)
	if violated {
		if current < maximum {
			decision.Action = ScalingActionScaleOut
			decision.DesiredReplicas = current + 1
			decision.Reason = "SLO evidence requires one bounded replica increase."
		} else {
			decision.Reason = "SLO is violated, but replicas are already at the configured maximum."
		}
		decision.Evidence = append([]string(nil), feedback.SLOViolations...)
		return decision
	}

	resource := feedback.Metrics.Resource
	if current > minimum &&
		resource.CPUAveragePercent < 30 &&
		resource.AcceleratorAveragePercent < 30 {
		decision.Action = ScalingActionScaleIn
		decision.DesiredReplicas = current - 1
		decision.Reason = "Healthy SLO and sustained low utilization allow one bounded replica decrease."
		return decision
	}

	decision.Reason = "SLO and utilization remain within the configured operating range."
	return decision
}
```

`scalingReplicaBounds` must normalize missing or invalid values to at least one replica and never return a maximum below the minimum. `violatesProfileSLO` compares feedback latency and throughput with the `ApplicationProfile` SLO.

- [ ] **Step 5: Persist scaling evidence after feedback**

In both `ReceiveDeploymentStatus` and `ReceiveOptimizationFeedback`, set:

```go
flow.ScalingDecision = evaluateScalingDecision(flow, service.now())
```

Update `cloneFlow` to deep-copy `ScalingDecision` and its `Evidence` slice.

- [ ] **Step 6: Add service integration assertions**

Extend the successful feedback test:

```go
if flow.ScalingDecision == nil {
	t.Fatal("scaling decision was not created")
}
if flow.ScalingDecision.Action != ScalingActionNoAction {
	t.Fatalf("scaling action = %q", flow.ScalingDecision.Action)
}
```

Add a service test that sets `SLOViolations` and expects `SCALE_OUT` with desired replicas equal to two.

- [ ] **Step 7: Run focused and package tests**

Run:

```powershell
go test ./internal/agentcontrol -run 'TestEvaluateScalingDecision|TestService.*Feedback|TestService.*Scaling' -count=1
go test ./internal/agentcontrol -count=1
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add -- internal/agentcontrol/models.go internal/agentcontrol/scaling.go internal/agentcontrol/scaling_test.go internal/agentcontrol/service.go internal/agentcontrol/service_test.go
git diff --cached --check
git commit -m "feat: add bounded scaling decisions to Agent Control"
```

---

### Task 3: Add Deletion for Generated Agent Control Flows

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/service.go`
- Modify: `go/service-control-api/internal/agentcontrol/service_test.go`
- Modify: `go/service-control-api/internal/api/agent_control_api.go`
- Modify: `go/service-control-api/internal/api/agent_control_api_test.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces:
  - `func (service *Service) DeleteFlow(correlationID string) (Flow, bool)`
  - `func (service *Service) ClearFlows() int`
  - `DELETE /api/v1/agent-control/flows/{correlation_id}`
  - `DELETE /api/v1/agent-control/flows`

- [ ] **Step 1: Write failing service deletion tests**

Add:

```go
func TestServiceDeletesGeneratedFlows(t *testing.T) {
	service := NewService()
	createApprovedFlow(t, service)

	deleted, ok := service.DeleteFlow("flow-001")
	if !ok || deleted.CorrelationID != "flow-001" {
		t.Fatalf("deleted = %#v ok=%v", deleted, ok)
	}
	if _, ok := service.GetFlow("flow-001"); ok {
		t.Fatal("deleted Flow remains available")
	}
	if count := service.ClearFlows(); count != 0 {
		t.Fatalf("cleared = %d, want 0", count)
	}
}
```

- [ ] **Step 2: Run the service test and verify failure**

Run:

```powershell
go test ./internal/agentcontrol -run TestServiceDeletesGeneratedFlows -count=1
```

Expected: FAIL because `DeleteFlow` and `ClearFlows` do not exist.

- [ ] **Step 3: Implement concurrency-safe deletion**

Add:

```go
func (service *Service) DeleteFlow(correlationID string) (Flow, bool) {
	service.mu.Lock()
	defer service.mu.Unlock()
	key := strings.TrimSpace(correlationID)
	flow, ok := service.flows[key]
	if !ok {
		return Flow{}, false
	}
	delete(service.flows, key)
	return cloneFlow(flow), true
}

func (service *Service) ClearFlows() int {
	service.mu.Lock()
	defer service.mu.Unlock()
	count := len(service.flows)
	service.flows = map[string]Flow{}
	return count
}
```

Normalize the key once before map access and deletion so whitespace cannot produce inconsistent behavior.

- [ ] **Step 4: Write failing API tests**

Add API tests that:

1. Create a Flow through the existing input-pair helper.
2. Delete `/api/v1/agent-control/flows/flow-api-001` and expect HTTP 200.
3. GET the same Flow and expect HTTP 404.
4. Create two Flows, DELETE `/api/v1/agent-control/flows`, and expect `deleted: 2`.

- [ ] **Step 5: Register and implement delete handlers**

In `server.go`:

```go
server.DELETE(pathAgentControl+"/flows", handler.RestDeleteAgentControlFlows)
server.DELETE(pathAgentControl+"/flows/:correlation_id", handler.RestDeleteAgentControlFlow)
```

Handlers return:

```json
{"deleted":1,"correlation_id":"flow-api-001"}
```

for one record, HTTP 404 for a missing record, and:

```json
{"deleted":2,"flows":[]}
```

for clear-all.

- [ ] **Step 6: Lock the route registration contract**

Extend `internal/api/server_test.go` route expectations with:

```go
"/api/v1/agent-control/flows",
"/api/v1/agent-control/flows/{correlation_id}",
```

and assert that both paths expose a `delete` operation in the route-method
contract test. Existing GET operations on both paths must remain.

- [ ] **Step 7: Run API tests**

Run:

```powershell
go test ./internal/agentcontrol ./internal/api -run 'TestServiceDeletesGeneratedFlows|TestAgentControl.*Delete|TestServer.*Route' -count=1
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add -- internal/agentcontrol/service.go internal/agentcontrol/service_test.go internal/api/agent_control_api.go internal/api/agent_control_api_test.go internal/api/server.go internal/api/server_test.go
git diff --cached --check
git commit -m "feat: delete generated Agent Control flows"
```

---

### Task 4: Replace the Web with Three Research Screens

**Files:**
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`

**Interfaces:**
- Consumes existing Agent Control, Agent Registry, reasoning-comparison,
  deployment-status, optimization-feedback, and Flow deletion APIs.
- Produces three views:
  - `data-view="agent-control"`
  - `data-view="agents"`
  - `data-view="results"`

- [ ] **Step 1: Replace legacy web contract tests with the three-view contract**

Add:

```go
func TestControlAppContainsExactlyThreeResearchViews(t *testing.T) {
	server := echo.New()
	Register(server)
	html := requestBody(t, server, "/")

	for _, expected := range []string{
		`data-view-target="agent-control"`,
		`data-view-target="agents"`,
		`data-view-target="results"`,
		`<span>자동화 에이전트</span>`,
		`<span>Agent 및 정책</span>`,
		`<span>실험 결과</span>`,
		`id="automation-flow-form"`,
		`id="experiment-flow-list"`,
		`id="experiment-flow-json"`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("missing simplified web contract %q", expected)
		}
	}

	for _, removed := range []string{
		`data-view="overview"`,
		`data-view="planner"`,
		`data-view="autonomy"`,
		`data-view="feedback"`,
		`id="planner-form"`,
		`id="action-form"`,
		`id="autonomy-form"`,
		`id="feedback-form"`,
		`고급 PoC 도구`,
		`Submit to AppDeploy`,
	} {
		if strings.Contains(html, removed) {
			t.Fatalf("platform-oriented web element remains %q", removed)
		}
	}
}
```

Delete or rewrite old tests that require Overview, Planner, Autonomy, standalone Action, or external Feedback views. Preserve embedded asset, readable Korean, Agent registration, and automation workflow tests.

- [ ] **Step 2: Run the new contract test and verify failure**

Run:

```powershell
go test ./internal/webui -run TestControlAppContainsExactlyThreeResearchViews -count=1
```

Expected: FAIL because the current HTML still exposes six views.

- [ ] **Step 3: Replace sidebar and view containers**

Use exactly:

```html
<nav class="primary-nav">
  <button class="nav-item is-active" type="button" data-view-target="agent-control">
    <i data-lucide="workflow" aria-hidden="true"></i><span>자동화 에이전트</span>
  </button>
  <button class="nav-item" type="button" data-view-target="agents">
    <i data-lucide="shield-check" aria-hidden="true"></i><span>Agent 및 정책</span>
  </button>
  <button class="nav-item" type="button" data-view-target="results">
    <i data-lucide="flask-conical" aria-hidden="true"></i><span>실험 결과</span>
  </button>
</nav>
```

Remove the Overview, Planner, Autonomy, Feedback, standalone Action Proposal,
and external Agent execution view markup. Retain the Agent registration dialog
because registration and deletion are part of the Registry prototype.

- [ ] **Step 4: Turn the core inputs into one execution form**

Wrap both textareas in:

```html
<form id="automation-flow-form">
  <section class="input-band">...</section>
  <section class="input-band">...</section>
  <div class="workflow-actions">
    <button class="button button-secondary" id="load-agent-control-sample" type="button">
      샘플 불러오기
    </button>
    <button class="button button-primary" id="run-automation-flow" type="submit">
      배포 판단 실행
    </button>
  </div>
</form>
```

Remove the separate `application-context-form` and
`resource-recommendation-form` submit buttons. Keep the six-stage evidence row
and the concise decision summary.

- [ ] **Step 5: Implement one joined-input submit function**

Replace both submit handlers with:

```javascript
async function submitAutomationFlow(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setBusy(form, true, "판단 중...");
  try {
    const context = parseProtocolMessage("application-context-json", "Application Context");
    const recommendation = parseProtocolMessage(
      "resource-recommendation-json",
      "Resource Recommendation",
    );
    if (context.correlation_id !== recommendation.correlation_id) {
      throw new Error("두 입력의 correlation_id가 같아야 합니다.");
    }
    if (
      context.data?.application_profile?.profile_id !==
      recommendation.data?.resource_recommendation?.profile_id
    ) {
      throw new Error("두 입력의 profile_id가 같아야 합니다.");
    }
    await apiRequest(API.applicationContexts, {
      method: "POST",
      body: JSON.stringify(context),
    });
    const flow = await apiRequest(API.resourceRecommendations, {
      method: "POST",
      body: JSON.stringify(recommendation),
    });
    renderAgentControlFlow(flow);
    await loadAgentControlFlows(flow.correlation_id);
    showToast(`${text(flow.decision?.action, flow.state)} 결정이 생성되었습니다.`, "success");
  } catch (error) {
    byID("agent-control-result-json").textContent =
      pretty(error.payload || { message: error.message });
    showToast(error.message, "error");
  } finally {
    setBusy(form, false);
  }
}
```

Bind only `automation-flow-form` for core execution.

- [ ] **Step 6: Make Registry a policy evidence screen**

Keep:

- Agent table
- capabilities
- bounded actions
- enabled status
- runtime Agent registration
- runtime Agent deletion

Add a compact policy band showing:

```text
Required Agent: AIApplicationAutomationAgent
Capability: ai_application_automation
Bounded Action: generate_deployment_decision
Guard: automatic
```

Remove execute buttons, Action Proposal form, standalone Action validation form,
Guard decision panel, and execution dialog from HTML and JavaScript.

- [ ] **Step 7: Build the Experiment Results screen**

Add these stable elements:

```html
<section class="view" data-view="results" hidden>
  <div class="results-layout">
    <section class="panel">
      <div id="experiment-flow-list"></div>
      <button id="clear-experiment-flows" type="button">전체 기록 삭제</button>
    </section>
    <section class="panel">
      <div id="experiment-decision-summary"></div>
      <div id="experiment-scaling-summary"></div>
      <pre id="experiment-flow-json"></pre>
    </section>
  </div>
  <details id="experiment-reasoning">...</details>
  <details id="experiment-feedback">...</details>
</section>
```

Move the existing reasoning-comparison and status/metrics Feedback forms into
the two collapsed disclosures. Their IDs remain unchanged so the existing API
functions can be reused.

Add `renderExperimentFlows(flows)` and
`selectExperimentFlow(correlationID)`. Selection must call
`renderAgentControlFlow(flow)`, render the complete Flow JSON, and display:

```javascript
const scaling = flow.scaling_decision || {};
byID("experiment-scaling-summary").textContent = scaling.action
  ? `${scaling.action} · ${scaling.current_replicas} → ${scaling.desired_replicas} · ${scaling.reason}`
  : "배포 상태와 성능 Feedback을 기다리고 있습니다.";
```

Add delete-one buttons using
`DELETE ${API.agentControlFlows}/${correlationID}` and clear-all using
`DELETE ${API.agentControlFlows}`.

- [ ] **Step 8: Remove legacy JavaScript state and API calls**

Remove browser-only references to:

- `controlRuns`
- `planner`
- `actionProposals`
- `feedback`
- all `autonomy*` API constants
- `APP_VERSION_KEY`
- `ACTIVE_RUN_KEY`
- ControlRun rendering and submission
- Action Proposal generation and validation
- external executor Feedback rendering
- Autonomous Loop polling and controls
- Overview metrics and Guide rendering

Keep the backend routes untouched.

Change labels to:

```javascript
const VIEW_LABELS = Object.freeze({
  "agent-control": ["KHU AUTOMATION AGENT", "AI 응용 자동화 에이전트"],
  agents: ["AGENT REGISTRY POLICY", "Agent 및 정책"],
  results: ["DECISION EVIDENCE", "실험 결과"],
});
```

`refreshDashboard` loads only health, Agents, and Agent Control Flows.

- [ ] **Step 9: Prune obsolete CSS and stabilize responsive layout**

Delete selectors used only by removed Overview, Planner, Autonomy, Action
Proposal, and external Feedback views. Keep cards at 8px radius or less.

Use:

```css
.automation-input-grid,
.results-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.agent-control-stage-flow {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

@media (max-width: 900px) {
  .automation-input-grid,
  .results-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .agent-control-stage-flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
```

Textareas use a bounded minimum height, all grid children use `min-width: 0`,
and the page must have no horizontal overflow at 390px.

- [ ] **Step 10: Run static and web tests**

Run:

```powershell
go test ./internal/webui -count=1
node --check internal/webui/static/app.js
rg -n 'data-view="(overview|planner|autonomy|feedback)"|id="(planner-form|action-form|autonomy-form|feedback-form)"' internal/webui/static
```

Expected:

- Go tests PASS.
- Node syntax check exits 0.
- `rg` returns no matches.

- [ ] **Step 11: Commit**

Run:

```powershell
git add -- internal/webui/webui_test.go internal/webui/static/index.html internal/webui/static/app.js internal/webui/static/app.css
git diff --cached --check
git commit -m "feat: simplify geon web to three research screens"
```

---

### Task 5: Update Contracts, Documentation, and Final Verification

**Files:**
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`
- Modify: `docs/evidence/agent_control_mvp_20260729.md`

**Interfaces:**
- Documents the existing Agent Control Flow response plus
  `scaling_decision`, Flow deletion, and the three-screen experiment.

- [ ] **Step 1: Write failing OpenAPI contract assertions**

Extend the contract test with:

```go
for _, expected := range []string{
	"delete:",
	"ScalingDecision:",
	"scaling_decision:",
	"NO_ACTION",
	"SCALE_OUT",
	"SCALE_IN",
} {
	if !strings.Contains(document, expected) {
		t.Fatalf("submission OpenAPI missing %q", expected)
	}
}
```

Scope the `delete:` assertions to the two Agent Control Flow paths so an
unrelated delete route cannot satisfy the test.

- [ ] **Step 2: Run the OpenAPI test and verify failure**

Run:

```powershell
go test ./internal/api -run TestSubmissionOpenAPIIncludesAgentControlInputJoinWorkflow -count=1
```

Expected: FAIL until the new schema and delete operations are documented.

- [ ] **Step 3: Update the submission OpenAPI contract**

Add:

```yaml
ScalingDecision:
  type: object
  required: [action, reason, current_replicas, desired_replicas, created_at]
  properties:
    action:
      type: string
      enum: [NO_ACTION, SCALE_OUT, SCALE_IN]
    reason:
      type: string
    current_replicas:
      type: integer
      minimum: 1
    desired_replicas:
      type: integer
      minimum: 1
    evidence:
      type: array
      items:
        type: string
    created_at:
      type: string
      format: date-time
```

Reference it from the Agent Control Flow schema and document both Flow delete
operations with 200 and 404 responses.

- [ ] **Step 4: Rewrite the top-level usage around the three screens**

Document:

```text
1. 자동화 에이전트
   ApplicationProfile + ResourceRecommendation
   → Registry authorization
   → DEPLOY | REJECT | RETRY
   → Go Guard
   → DesiredDeploymentSpec

2. Agent 및 정책
   Registered Agent capability and bounded action evidence

3. 실험 결과
   Flow evidence + reasoning comparison + status/metrics Feedback
   → NO_ACTION | SCALE_OUT | SCALE_IN
```

Explicitly state that AppDeploy and a VM are not required for the core local
experiment and that actual deployment is external.

- [ ] **Step 5: Run complete Go and static verification**

From `go/service-control-api` run:

```powershell
gofmt -w internal/agentcontrol/models.go internal/agentcontrol/scaling.go internal/agentcontrol/scaling_test.go internal/agentcontrol/service.go internal/agentcontrol/service_test.go internal/api/agent_control_api.go internal/api/agent_control_api_test.go internal/api/server.go
go test ./... -count=1
go vet ./...
node --check internal/webui/static/app.js
git diff --check
```

Expected: every command exits 0. CRLF conversion warnings are acceptable;
actual whitespace errors are not.

- [ ] **Step 6: Start the local service**

Run:

```powershell
$env:AIOPS_REPO_ROOT = (git rev-parse --show-toplevel)
$env:AIOPS_LLM_CANDIDATES_PATH = "config/ops_llm_eval_candidates.local_ollama.json"
$env:AIOPS_BIND_ADDRESS = "127.0.0.1"
$env:PORT = "18080"
go run ./cmd/service-control-api
```

Expected:

```text
http://127.0.0.1:18080/
```

serves the simplified web and `/healthz` returns HTTP 200.

- [ ] **Step 7: Perform browser verification**

Use Playwright with installed Chrome at:

- Desktop: 1440x900
- Mobile: 390x844

Verify:

1. Exactly three sidebar destinations are visible.
2. Automation Agent is the initial screen.
3. Loading samples and pressing `배포 판단 실행` returns
   `DEPLOY_APPROVED`, authorization `승인`, Guard `APPROVED`, and a desired
   deployment specification.
4. Agent and Policy shows the required capability and bounded action.
5. Experiment Results shows the new Flow.
6. Submitting an SLO-violating Feedback sample shows `SCALE_OUT`.
7. Deleting the selected Flow removes it from the results list.
8. No console errors, failed unexpected requests, overlap, or horizontal
   overflow occur.

- [ ] **Step 8: Commit final contracts and evidence**

Run:

```powershell
git add -- ../../README.md README.md ../../docs/submission/openapi_service_control.yaml ../../docs/evidence/agent_control_mvp_20260729.md internal/api/openapi_contract_test.go
git diff --cached --check
git commit -m "docs: document simplified KHU automation Agent PoC"
```

- [ ] **Step 9: Confirm user-owned files and branch state**

Run:

```powershell
git status --short --branch
git log -5 --oneline --decorate
```

Expected:

- branch is `geon`
- `config/inference_optimization.json` remains untracked and untouched
- implementation commits are ahead of `origin/geon`
- no AppDeploy files were modified
