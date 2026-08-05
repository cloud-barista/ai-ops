# geon Autonomous Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded closed-loop controller to geon Agent Control that monitors AppDeploy metrics, evaluates SLOs, asks Qwen for a bounded recovery Action, validates it with Go policy, optionally executes it through AppDeploy, and re-evaluates the result.

**Architecture:** Extend the existing AppDeploy client with monitoring and control contracts, then add an `internal/autonomy` package containing pure SLO evaluation and a concurrency-safe manager. The API layer supplies Qwen and Agent Registry adapters, while the embedded web UI exposes mode, policy, loop controls, current observations, and the event timeline.

**Tech Stack:** Go 1.25+, Echo v4, existing `llmclient` and `automation.Planner`, embedded HTML/CSS/JavaScript, AppDeploy REST API, Go `testing` and `httptest`.

## Global Constraints

- Do not modify the AppDeploy source tree.
- Default mode is `monitor_only`; a process restart must never resume `guarded_auto` automatically.
- AppDeploy state-changing calls require fresh evidence, confirmed consecutive violations, Agent Registry approval, Go Guard approval, cooldown clearance, and remaining action budget.
- Never accept, store, log, or forward credentials or secret-like fields.
- Scale-out means an additional VM-only Deployment on an explicitly configured standby Target; it must report `traffic_handoff_required=true`.
- Rollback requires an explicitly configured previous App Version ID.
- Preserve all existing dirty workspace changes and do not stage unrelated `config/inference_optimization.json`.

---

### Task 1: AppDeploy monitoring and control client

**Files:**
- Modify: `go/service-control-api/internal/appdeploy/models.go`
- Modify: `go/service-control-api/internal/appdeploy/client.go`
- Modify: `go/service-control-api/internal/appdeploy/client_test.go`

**Interfaces:**
- Produces: `ListDeployments`, `ListDeploymentMetrics`, `GetMonitoringSummary`, `StopDeployment` methods on `appdeploy.Client`.
- Produces: `DeploymentListResponse`, `InferenceMetricRecord`, `DeploymentMetricsResponse`, `MonitoringSummaryResponse` and nested monitoring models.

- [ ] **Step 1: Write failing HTTP contract tests**

Add cases to the existing `httptest.Server` verifying these requests and response normalizations:

```go
GET  /api/v1/deployments
GET  /api/v1/deployments/dep-1/metrics
GET  /api/v1/monitoring/summary
POST /api/v1/deployments/dep-1/stop
```

Assert that `items` and compatibility aliases such as `deployments` or `metrics` normalize to `Items`, and that stop rejects an empty deployment ID.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/appdeploy -run 'TestClientMonitoringAndControl|TestClientRejectsEmptyStopID' -count=1`

Expected: compile failure because the methods and models do not exist.

- [ ] **Step 3: Add minimal models and client methods**

Use `Client.doJSON` and URL escaping exactly like existing deployment methods. Do not add retry loops in the client.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/appdeploy -count=1`

Expected: PASS.

### Task 2: Pure SLO evaluation and safety policy

**Files:**
- Create: `go/service-control-api/internal/autonomy/models.go`
- Create: `go/service-control-api/internal/autonomy/evaluator.go`
- Create: `go/service-control-api/internal/autonomy/evaluator_test.go`
- Create: `go/service-control-api/internal/autonomy/guard.go`
- Create: `go/service-control-api/internal/autonomy/guard_test.go`

**Interfaces:**
- Produces: `Config.Validate() error`, `Evaluate(EvaluationInput) Evaluation`, and `ValidateExecution(GuardInput) GuardDecision`.
- Consumes: `appdeploy.InferenceMetricRecord`, deployment status, monitoring health, and current deployment runtime state.

- [ ] **Step 1: Write failing evaluator tests**

Cover exact boundary behavior:

```go
latency == max                 => healthy
latency > max                  => latency violation
throughput == min              => healthy
throughput < min               => throughput violation
error_count/request_count > max => error-rate violation
zero request_count             => no divide-by-zero violation
metric older than max age      => insufficient_evidence
failed deployment without metric => confirmed failure evidence
healthy evaluation             => reset consecutive count
```

- [ ] **Step 2: Verify evaluator RED**

Run: `go test ./internal/autonomy -run TestEvaluate -count=1`

Expected: compile failure because the package API is missing.

- [ ] **Step 3: Implement models, config validation, and evaluator**

Use typed constants:

```go
type Mode string
const (
    ModeMonitorOnly Mode = "monitor_only"
    ModeGuardedAuto Mode = "guarded_auto"
)
```

Config validation must enforce all bounds from the design spec and reject secret-like JSON keys through API decoding before configuration reaches the manager.

- [ ] **Step 4: Write failing guard tests**

Cover monitor-only `would_execute`, stale evidence, consecutive threshold, cooldown, max action count, missing standby Target, missing rollback version, and one-action-per-cycle.

- [ ] **Step 5: Verify guard RED**

Run: `go test ./internal/autonomy -run TestValidateExecution -count=1`

Expected: compile failure for the missing guard implementation.

- [ ] **Step 6: Implement guard and verify GREEN**

Run: `go test ./internal/autonomy -count=1`

Expected: PASS.

### Task 3: Concurrency-safe autonomy manager and executor

**Files:**
- Create: `go/service-control-api/internal/autonomy/manager.go`
- Create: `go/service-control-api/internal/autonomy/manager_test.go`
- Create: `go/service-control-api/internal/autonomy/executor.go`
- Create: `go/service-control-api/internal/autonomy/executor_test.go`

**Interfaces:**
- Consumes:

```go
type AppDeployControl interface {
    GetDeployment(context.Context, string) (appdeploy.DeploymentResponse, error)
    ListDeploymentMetrics(context.Context, string) (appdeploy.DeploymentMetricsResponse, error)
    GetMonitoringSummary(context.Context) (appdeploy.MonitoringSummaryResponse, error)
    CreateDeployment(context.Context, appdeploy.DeploymentManifest) (appdeploy.DeploymentResponse, error)
    StopDeployment(context.Context, string) (appdeploy.DeploymentResponse, error)
}

type DecisionPlanner interface {
    Plan(context.Context, DecisionInput) (Decision, error)
}

type ActionAuthorizer interface {
    Validate(context.Context, string) (bool, string, error)
}
```

- Produces: `NewManager`, `Configure`, `Start`, `Stop`, `EmergencyStop`, `RunCycle`, `Status`, and `Events`.

- [ ] **Step 1: Write failing executor tests**

Use a recording fake AppDeploy control and verify:

- observe performs only GET operations,
- stop performs one stop,
- restart performs stop then create with the same manifest,
- rollback changes only `app_version_id`,
- scale-out changes only `target_profile_id` and sets `traffic_handoff_required`,
- stop success followed by create failure returns `partial_failure`.

- [ ] **Step 2: Verify executor RED**

Run: `go test ./internal/autonomy -run TestExecutor -count=1`

Expected: compile failure for the missing executor.

- [ ] **Step 3: Implement executor and verify GREEN**

Run the same command and expect PASS.

- [ ] **Step 4: Write failing manager tests**

Verify:

- first violation records `observing_violation`,
- second violation calls planner and authorizer,
- monitor-only records `would_execute` and performs no state change,
- guarded auto executes one Action,
- duplicate `Start` creates one loop,
- `Stop` cancels the loop,
- `EmergencyStop` returns mode to monitor-only,
- cooldown prevents duplicate execution,
- partial failure locks execution and returns monitor-only,
- event store keeps the newest 200 events.

- [ ] **Step 5: Verify manager RED**

Run: `go test ./internal/autonomy -run TestManager -count=1`

Expected: compile failure for the missing manager.

- [ ] **Step 6: Implement manager and verify GREEN**

Use separate `mu` and `cycleMu` mutexes. Never hold `mu` across AppDeploy or Qwen network calls. Use a cancellable context for the ticker loop.

Run: `go test ./internal/autonomy -count=1`

Expected: PASS, including `go test -race ./internal/autonomy` when supported.

### Task 4: Qwen and Agent Registry adapters

**Files:**
- Create: `go/service-control-api/internal/api/autonomy_adapters.go`
- Create: `go/service-control-api/internal/api/autonomy_adapters_test.go`
- Modify: `config/agent_registry.json`
- Modify: `config/vm_workload_requirements.json`

**Interfaces:**
- Produces: API-layer implementations of `autonomy.DecisionPlanner` and `autonomy.ActionAuthorizer`.
- Consumes: existing `automation.Planner`, `llmclient`, `AIApplicationAutomationAgent`, and candidate `qwen3.5-ops-planner`.

- [ ] **Step 1: Write failing adapter tests**

Use a fake completion client through a narrow constructor seam and assert that observations include SLO values, violations, Deployment ID, Target Profile ID, allowed actions, and no credentials. Verify unknown and unregistered Actions are rejected.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/api -run 'TestAutonomyDecisionPlanner|TestAutonomyActionAuthorizer' -count=1`

Expected: compile failure because adapters do not exist.

- [ ] **Step 3: Implement adapters and extend bounded actions**

Add `scale_out_application` and `rollback_application` to the automation Agent and workload allowlists. Reuse strict JSON parsing from `automation.Planner`; do not parse free-form text.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/api ./internal/automation -count=1`

Expected: PASS.

### Task 5: Autonomy REST API and service wiring

**Files:**
- Modify: `go/service-control-api/internal/api/config.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Create: `go/service-control-api/internal/api/autonomy_api.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`
- Modify: `docs/submission/openapi_service_control.yaml`

**Interfaces:**
- Produces routes:

```text
GET  /api/v1/autonomy/status
PUT  /api/v1/autonomy/config
POST /api/v1/autonomy/start
POST /api/v1/autonomy/stop
POST /api/v1/autonomy/emergency-stop
POST /api/v1/autonomy/cycles
GET  /api/v1/autonomy/events
```

- [ ] **Step 1: Write failing API flow tests**

Test default status, invalid config 400, valid config, start idempotency, stop, emergency mode reset, one-shot cycle, event listing, and unknown secret-like fields rejected by strict decoding.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/api -run TestAutonomyAPI -count=1`

Expected: 404 or compile failure.

- [ ] **Step 3: Wire manager into Service and routes**

When `AIOPS_APPDEPLOY_BASE_URL` is absent, status remains available and cycles return a source-unavailable event instead of panicking. Do not start the loop in `NewServer`.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/api -count=1`

Expected: PASS.

### Task 6: Autonomous Loop web UI

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Consumes: all autonomy REST routes from Task 5.
- Produces: sidebar `Autonomous Loop` view, policy form, loop controls, observation facts, and Timeline.

- [ ] **Step 1: Write failing embedded UI contract test**

Assert the HTML contains `data-view="autonomy"`, `id="autonomy-form"`, mode control, start/stop/emergency/cycle buttons, observation IDs, Timeline container, and the JavaScript contains every autonomy route.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/webui -run TestControlAppContainsAutonomyView -count=1`

Expected: FAIL because the view is absent.

- [ ] **Step 3: Implement the operational UI**

Use the existing visual system. Add icons through Lucide, a segmented mode control, explicit confirmation only for Emergency Stop, stable dimensions, responsive single-column policy layout below 580px, and internal scrolling for long Timeline JSON.

JavaScript requirements:

- poll status/events only while the Autonomy view is active,
- stop polling when leaving the view,
- never claim an Action executed unless the API returns an execution result,
- distinguish `would_execute`, `blocked`, `approved`, `partial_failure`, and `recovered`,
- disable mutating controls while requests are pending,
- surface API errors through the existing toast system.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/webui -count=1`

Expected: PASS.

### Task 7: Documentation, full verification, and live demo

**Files:**
- Modify: `go/service-control-api/README.md`
- Modify: `docs/submission/openapi_service_control.yaml`
- Create runtime evidence only under an ignored `runs/autonomy/` directory if needed.

**Interfaces:**
- Documents: startup, AppDeploy metric injection, monitor-only demo, guarded-auto demo, emergency stop, and limitations of VM-only scale-out.

- [ ] **Step 1: Update README and OpenAPI contract**

Include a reproducible metric example:

```bash
curl -X POST http://127.0.0.1:8080/api/v1/deployments/DEPLOYMENT_ID/metrics \
  -H "Content-Type: application/json" \
  -d '{"latency_ms":900,"throughput_rps":0.5,"request_count":100,"error_count":8}'
```

- [ ] **Step 2: Run complete automated verification**

Run:

```bash
go test ./...
go test -race ./internal/autonomy
git diff --check
```

Expected: all PASS and no whitespace errors.

- [ ] **Step 3: Start local services and test Monitor Only**

Verify AppDeploy `8080`, Ollama `11434`, and geon `18080`. Inject two violating metrics or run two manual cycles. Confirm Timeline includes SLO, Qwen, Guard, and `would_execute`, with no new AppDeploy state-changing request.

- [ ] **Step 4: Test Guarded Auto with safe Mock resources**

Use a Mock deployment and configured standby/rollback IDs. Confirm exactly one bounded AppDeploy action, cooldown, and result re-evaluation. If resources are absent, verify `blocked` rather than creating infrastructure.

- [ ] **Step 5: Browser verification**

Using the in-app browser Playwright API, verify desktop `1440x900` and mobile `390x844`, no horizontal page overflow, working mode/config/start/stop/cycle controls, readable Timeline, and no console errors.

- [ ] **Step 6: Final workspace audit**

Confirm no AppDeploy source file changed, unrelated user changes remain untouched, and report exact tested behavior and any execution limitations.
