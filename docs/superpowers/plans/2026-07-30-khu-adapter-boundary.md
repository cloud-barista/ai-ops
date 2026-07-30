# KHU Adapter Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate KHU decision logic from Mock deployment and external handoff evidence without adding real platform execution.

**Architecture:** `AutomationRunner` keeps requirement analysis, recommendation, Agent authorization, decision, and Go Guard authoritative. An injected `DeploymentAdapter` receives only an approved Common JSON `deployment.create.request` and records either deterministic Mock evidence or external-handoff-ready evidence on the same `AutomationRun`.

**Tech Stack:** Go 1.22+, Echo, Viper, embedded HTML/CSS/JavaScript, Go tests, Node browser tests.

## Global Constraints

- Work on the existing `geon` branch.
- Do not modify the AppDeployer branch.
- Do not touch `config/inference_optimization.json` or `tmp/`.
- Do not add network calls, credentials, VM execution, or provider-specific fields.
- Keep `DesiredDeploymentSpec` platform-neutral.
- Use test-driven development for every behavior change.
- Preserve Common JSON v1.0 identifiers and request idempotency.

---

### Task 1: Add typed deployment Adapter contracts

**Files:**
- Create: `go/service-control-api/internal/agentcontrol/deployment_adapter.go`
- Create: `go/service-control-api/internal/agentcontrol/deployment_adapter_test.go`

**Interfaces:**
- Consumes: `agentcontrol.DeploymentCreateRequestEnvelope`
- Produces: `agentcontrol.DeploymentAdapter`, `agentcontrol.DeploymentSubmission`

- [ ] **Step 1: Write failing tests for Mock and handoff evidence**

```go
func TestMockDeploymentAdapterMarksSubmissionSimulated(t *testing.T) {
    adapter := MockDeploymentAdapter{Now: fixedTime}
    result, err := adapter.Submit(context.Background(), approvedDeploymentRequest())
    if err != nil {
        t.Fatal(err)
    }
    if result.Adapter != DeploymentAdapterMock ||
        result.Status != DeploymentSubmissionSimulated ||
        !result.Simulated {
        t.Fatalf("submission = %#v", result)
    }
}

func TestHandoffDeploymentAdapterMarksRequestReady(t *testing.T) {
    adapter := HandoffDeploymentAdapter{Now: fixedTime}
    result, err := adapter.Submit(context.Background(), approvedDeploymentRequest())
    if err != nil {
        t.Fatal(err)
    }
    if result.Adapter != DeploymentAdapterHandoff ||
        result.Status != DeploymentSubmissionReady ||
        result.Simulated {
        t.Fatalf("submission = %#v", result)
    }
}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
go test ./internal/agentcontrol -run 'Test(Mock|Handoff)DeploymentAdapter' -count=1
```

Expected: build failure because Adapter types do not exist.

- [ ] **Step 3: Implement minimal Adapter types**

```go
type DeploymentAdapter interface {
    Submit(context.Context, DeploymentCreateRequestEnvelope) (DeploymentSubmission, error)
}

type DeploymentSubmission struct {
    Adapter     string `json:"adapter"`
    Status      string `json:"status"`
    Simulated   bool   `json:"simulated"`
    RequestID   string `json:"request_id"`
    SubmittedAt string `json:"submitted_at"`
    ErrorCode   string `json:"error_code,omitempty"`
    ErrorMessage string `json:"error_message,omitempty"`
}
```

Implement deterministic `MockDeploymentAdapter` and
`HandoffDeploymentAdapter`. Both preserve the input request ID and perform no
network or execution side effects.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
gofmt -w internal/agentcontrol/deployment_adapter.go internal/agentcontrol/deployment_adapter_test.go
go test ./internal/agentcontrol -run 'Test(Mock|Handoff)DeploymentAdapter' -count=1
```

- [ ] **Step 5: Commit**

```bash
git add go/service-control-api/internal/agentcontrol/deployment_adapter.go \
        go/service-control-api/internal/agentcontrol/deployment_adapter_test.go
git commit -m "feat: add deployment adapter contracts"
```

### Task 2: Invoke the Adapter exactly once from AutomationRunner

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/automation_runner.go`
- Modify: `go/service-control-api/internal/agentcontrol/automation_runner_test.go`

**Interfaces:**
- Consumes: `DeploymentAdapter.Submit`
- Produces: `AutomationRun.DeploymentSubmission`

- [ ] **Step 1: Add a counting Adapter test double and failing tests**

Add tests proving:

```go
func TestAutomationRunnerSubmitsApprovedRequestOnce(t *testing.T)
func TestAutomationRunnerDoesNotSubmitRejectOrRetry(t *testing.T)
func TestAutomationRunnerDoesNotResubmitIdempotentReplay(t *testing.T)
func TestAutomationRunnerRecordsSanitizedAdapterFailure(t *testing.T)
```

The counting Adapter increments on `Submit`. The approved run must record one
submission. `REJECT`, `RETRY`, and replay paths must leave the count unchanged.
The failure test must expose `DEPLOYMENT_ADAPTER_FAILED` without copying the
underlying error text into `DeploymentSubmission.ErrorMessage`.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./internal/agentcontrol -run 'TestAutomationRunner.*Submit|TestAutomationRunner.*Adapter' -count=1
```

Expected: compile or assertion failure because the Runner has no Adapter.

- [ ] **Step 3: Inject and invoke the Adapter**

Add a backward-compatible constructor:

```go
func NewAutomationRunnerWithAdapter(
    analyzer RequirementAnalyzer,
    recommender ResourceRecommender,
    agentControl *Service,
    deploymentAdapter DeploymentAdapter,
) *AutomationRunner
```

Keep `NewAutomationRunner` and make it select `MockDeploymentAdapter` by
default. After the Agent flow returns an approved deployment request, call the
Adapter and store its evidence. Never call it without both
`DesiredDeploymentSpec` and `Flow.DeploymentRequest`.

On Adapter error:

- keep decision and Guard evidence;
- set run status to `FAILED`;
- set error code `DEPLOYMENT_ADAPTER_FAILED`;
- record a sanitized user message; and
- store the run before returning the underlying error to the internal caller.

- [ ] **Step 4: Run focused and package tests**

```bash
gofmt -w internal/agentcontrol/automation_runner.go internal/agentcontrol/automation_runner_test.go
go test ./internal/agentcontrol -count=1
```

- [ ] **Step 5: Commit**

```bash
git add go/service-control-api/internal/agentcontrol/automation_runner.go \
        go/service-control-api/internal/agentcontrol/automation_runner_test.go
git commit -m "feat: record deployment adapter evidence"
```

### Task 3: Select Mock or handoff mode through configuration

**Files:**
- Create: `go/service-control-api/internal/api/config_test.go`
- Modify: `go/service-control-api/internal/api/config.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/cmd/service-control-api/main.go`

**Interfaces:**
- Consumes: `AIOPS_DEPLOYMENT_ADAPTER`
- Produces: `ServerConfig.DeploymentAdapterMode`

- [ ] **Step 1: Write failing configuration tests**

```go
func TestNewServerConfigDefaultsDeploymentAdapterToMock(t *testing.T)
func TestNewServerConfigReadsHandoffDeploymentAdapter(t *testing.T)
func TestValidateServerConfigRejectsUnknownDeploymentAdapter(t *testing.T)
```

Use `t.Setenv("AIOPS_DEPLOYMENT_ADAPTER", ...)` and assert `mock`, `handoff`,
and a sanitized validation error respectively.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./internal/api -run 'Test(NewServerConfig|ValidateServerConfig).*DeploymentAdapter' -count=1
```

- [ ] **Step 3: Implement configuration and service wiring**

Add `DeploymentAdapterMode string` to `ServerConfig`, default it to `mock`,
and validate only `mock` or `handoff`.

Add a factory in `agentcontrol`:

```go
func NewDeploymentAdapter(mode string) (DeploymentAdapter, error)
```

`NewService` uses the selected Adapter with
`NewAutomationRunnerWithAdapter`. The command entrypoint validates the config
before starting Echo. No network endpoint is added.

- [ ] **Step 4: Run focused tests**

```bash
gofmt -w internal/api/config.go internal/api/config_test.go \
  internal/api/service.go cmd/service-control-api/main.go
go test ./internal/api ./cmd/service-control-api -count=1
```

- [ ] **Step 5: Commit**

```bash
git add go/service-control-api/internal/api/config.go \
        go/service-control-api/internal/api/config_test.go \
        go/service-control-api/internal/api/service.go \
        go/service-control-api/cmd/service-control-api/main.go \
        go/service-control-api/internal/agentcontrol/deployment_adapter.go \
        go/service-control-api/internal/agentcontrol/deployment_adapter_test.go
git commit -m "feat: configure deployment adapter mode"
```

### Task 4: Show Adapter evidence in the web and documentation

**Files:**
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/automation_run_browser_test.js`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `README.md`

**Interfaces:**
- Consumes: `AutomationRun.deployment_submission`
- Produces: visible Adapter mode and status

- [ ] **Step 1: Write failing HTML and browser assertions**

Require:

```text
data-agent-control-stage="adapter"
id="agent-control-adapter"
id="agent-control-adapter-status"
Mock simulation
External handoff ready
```

Update the browser fixture with:

```json
"deployment_submission": {
  "adapter": "mock",
  "status": "SIMULATED",
  "simulated": true,
  "request_id": "deploy-request-demo"
}
```

Assert that the Adapter stage is complete and the summary displays
`mock / SIMULATED`.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./internal/webui -count=1
node internal/webui/automation_run_browser_test.js
```

- [ ] **Step 3: Implement focused web changes**

Add a fourth stage after Agent decision:

```text
4 Adapter handoff
Mock simulation or external handoff ready
```

Add two summary cells for Adapter and handoff status. Include
`deployment_submission` in the JSON result. Do not add new navigation or a
separate Adapter management screen.

Update README with:

- `AIOPS_DEPLOYMENT_ADAPTER=mock|handoff`;
- Mock means simulation, not real VM deployment;
- handoff means request-ready evidence, not confirmed external execution; and
- the KHU Core and external platform responsibility boundary.

- [ ] **Step 4: Run web tests**

```bash
go test ./internal/webui -count=1
node internal/webui/automation_run_browser_test.js
```

- [ ] **Step 5: Commit**

```bash
git add README.md \
        go/service-control-api/internal/webui/webui_test.go \
        go/service-control-api/internal/webui/automation_run_browser_test.js \
        go/service-control-api/internal/webui/static/index.html \
        go/service-control-api/internal/webui/static/app.js
git commit -m "feat: expose adapter evidence in agent web"
```

### Task 5: Verify the full geon prototype

**Files:**
- Modify generated OpenAPI only if the serialized public model requires it.

**Interfaces:**
- Consumes: all prior tasks
- Produces: verified geon branch

- [ ] **Step 1: Format and inspect**

```bash
gofmt -w internal/agentcontrol/*.go internal/api/*.go cmd/service-control-api/*.go
git diff --check
```

- [ ] **Step 2: Run complete Go validation**

```bash
go test ./... -count=1
go vet ./...
go build ./...
```

- [ ] **Step 3: Run web behavior tests**

```bash
node internal/webui/automation_run_browser_test.js
node internal/webui/manifest_stages_test.js
node internal/webui/control_run_browser_test.js
```

- [ ] **Step 4: Verify both modes locally**

Run the service once with each value:

```bash
AIOPS_DEPLOYMENT_ADAPTER=mock
AIOPS_DEPLOYMENT_ADAPTER=handoff
```

Submit the same structured request and verify `SIMULATED` versus `READY`
without external network or VM activity.

- [ ] **Step 5: Review and commit any final generated artifacts**

Stage only files related to this plan. Leave
`config/inference_optimization.json` and `tmp/` untouched.

