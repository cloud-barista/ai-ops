# geon ControlRun Manifest Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect Agent Registry, Request Guard, Qwen Manifest generation, Manifest Guard, optional AppDeploy submission, Autonomous Loop, and Feedback through one backend-owned ControlRun while preserving geon's DeploymentManifest responsibility.

**Architecture:** Add a small concurrency-safe `internal/controlrun` state package. API services orchestrate the existing `plannerguard`, Registry, `deploymentplanner.Generator`, and AppDeploy client while recording each stage. Manifest generation is independent from AppDeploy; the existing plan-and-deploy endpoint becomes a compatibility composition of generate and submit.

**Tech Stack:** Go 1.25, Echo v4, existing Qwen/Ollama `llmclient`, existing AppDeploy REST client, embedded HTML/CSS/JavaScript, Swagger via swaggo.

## Global Constraints

- Do not modify the AppDeploy repository or AppDeploy-owned resources.
- `DeploymentManifest` and operational `Action Proposal` remain separate contracts.
- External Agent endpoints are validated but are not invoked.
- Autonomous Loop remains optional and never runs as a Manifest-generation stage.
- Existing `/api/v1/planner/deployments` clients remain compatible.
- Use a process-memory store with mutex protection; no database dependency.
- Preserve unrelated `config/inference_optimization.json`.
- Every behavior change follows a failing-test-first cycle.

---

### Task 1: ControlRun Model and Store

**Files:**
- Create: `go/service-control-api/internal/controlrun/models.go`
- Create: `go/service-control-api/internal/controlrun/store.go`
- Create: `go/service-control-api/internal/controlrun/store_test.go`

**Interfaces:**
- Produces: `controlrun.Run`, `controlrun.Stage`, `controlrun.Status`, `controlrun.Store`
- Consumes: existing `appdeploy`, `deploymentplanner`, and `plannerguard` response types

- [ ] **Step 1: Write failing store lifecycle tests**

Test these behaviors with real store operations:

```go
func TestStoreLifecycleReturnsIsolatedCopies(t *testing.T) {
    store := NewStore()
    created := store.Create(CreateInput{RunID: "run-001"})
    updated, err := store.Update(created.RunID, func(run *Run) error {
        run.Status = StatusManifestApproved
        run.CorrelationIDs = append(run.CorrelationIDs, "corr-001")
        return nil
    })
    if err != nil {
        t.Fatal(err)
    }
    updated.CorrelationIDs[0] = "mutated"
    loaded, ok := store.Get(created.RunID)
    if !ok || loaded.CorrelationIDs[0] != "corr-001" {
        t.Fatalf("store leaked mutable state: %#v", loaded)
    }
}
```

Also cover newest-first listing, missing Run update, individual delete, clear, and concurrent updates under `go test -race`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
go test ./internal/controlrun
```

Expected: package or symbols do not exist.

- [ ] **Step 3: Implement the model and mutex-protected store**

Provide these public operations:

```go
func NewStore() *Store
func (store *Store) Create(input CreateInput) Run
func (store *Store) Get(runID string) (Run, bool)
func (store *Store) List() []Run
func (store *Store) Update(runID string, mutate func(*Run) error) (Run, error)
func (store *Store) Delete(runID string) (Run, bool)
func (store *Store) Clear() int
```

Deep-copy slices, maps, pointers, Manifest parameters, stages, and correlation IDs on input and output.

- [ ] **Step 4: Run focused tests and race detector**

```powershell
go test ./internal/controlrun
go test -race ./internal/controlrun
```

Expected: PASS with no race reports.

- [ ] **Step 5: Commit**

```powershell
git add go/service-control-api/internal/controlrun
git commit -m "feat: add control run state store"
```

### Task 2: Registry-Driven Planner Agent Resolution

**Files:**
- Create: `go/service-control-api/internal/api/planner_agent_resolver.go`
- Create: `go/service-control-api/internal/api/planner_agent_resolver_test.go`
- Modify: `go/service-control-api/internal/api/models.go`

**Interfaces:**
- Consumes: `AgentRegistry`, `AgentProfile`
- Produces: `AgentSelection`, `resolvePlannerAgent(registry AgentRegistry, requestedName string, action string) (AgentSelection, error)`

- [ ] **Step 1: Write failing Agent resolution tests**

Cover:

```go
func TestResolvePlannerAgentSelectsEnabledAuthorizedConfigurationAgent(t *testing.T)
func TestResolvePlannerAgentRejectsDisabledAgent(t *testing.T)
func TestResolvePlannerAgentRejectsMissingCapability(t *testing.T)
func TestResolvePlannerAgentRejectsUnboundedAction(t *testing.T)
func TestResolvePlannerAgentDoesNotSelectRuntimeEndpointAgent(t *testing.T)
func TestResolvePlannerAgentHonorsExplicitAgentName(t *testing.T)
```

The resolver requires:

```text
capability = deployment_manifest_planning
action     = generate_deployment_manifest or submit_deployment_manifest
source     = configuration
enabled    = true
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/api -run ResolvePlannerAgent
```

Expected: resolver and selection type are undefined.

- [ ] **Step 3: Implement deterministic resolution**

Add:

```go
type AgentSelection struct {
    Name       string `json:"name"`
    Capability string `json:"capability"`
    Action     string `json:"action"`
    Source     string `json:"source"`
    Reason     string `json:"reason"`
}
```

Explicit Agent names fail closed. Automatic selection uses Registry declaration order and never falls back to a runtime endpoint Agent.

- [ ] **Step 4: Run focused tests**

```powershell
go test ./internal/api -run ResolvePlannerAgent
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add go/service-control-api/internal/api/planner_agent_resolver.go go/service-control-api/internal/api/planner_agent_resolver_test.go go/service-control-api/internal/api/models.go
git commit -m "feat: resolve manifest planner through agent registry"
```

### Task 3: Manifest-Only ControlRun Service

**Files:**
- Create: `go/service-control-api/internal/api/control_run_models.go`
- Create: `go/service-control-api/internal/api/control_run_service.go`
- Create: `go/service-control-api/internal/api/control_run_service_test.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/deploymentplanner/generator.go`

**Interfaces:**
- Consumes: `controlrun.Store`, `plannerguard.ValidateRequest`, Agent resolver, `deploymentplanner.Generator`
- Produces:

```go
func (service Service) CreateControlRun(context.Context, CreateControlRunRequest) (controlrun.Run, error)
func (service Service) CreateControlRunWithDependencies(
    context.Context,
    CreateControlRunRequest,
    string,
    string,
    controlRunManifestGenerator,
) (controlrun.Run, error)
```

- [ ] **Step 1: Write failing service pipeline tests**

Use a fake `controlRunManifestGenerator` that records calls and returns a real `GenerateResult`.

Cover:

```go
func TestCreateControlRunReturnsApprovedManifestWithoutAppDeploy(t *testing.T)
func TestCreateControlRunRejectsRequestBeforeRegistryAndQwen(t *testing.T)
func TestCreateControlRunRejectsUnauthorizedPlannerBeforeQwen(t *testing.T)
func TestCreateControlRunRecordsManifestGuardRejection(t *testing.T)
func TestCreateControlRunRecordsOrderedStages(t *testing.T)
```

The successful stage names are exactly:

```text
request_guard
agent_registry
qwen_planner
manifest_guard
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/api -run CreateControlRun
```

Expected: ControlRun service methods do not exist.

- [ ] **Step 3: Add the store to Service**

Initialize exactly one store per Service:

```go
type Service struct {
    config             ServerConfig
    runtimeAgents      *runtimeAgentStore
    automationFeedback *automationFeedbackStore
    autonomyManager    *autonomy.Manager
    controlRuns        *controlrun.Store
}
```

- [ ] **Step 4: Implement stage-recording orchestration**

The service must:

1. Generate an opaque `run-` ID.
2. Create `RECEIVED`.
3. Validate Request Guard.
4. Load Registry and resolve the internal Planner Agent.
5. Load the configured Qwen candidate.
6. Record `PLANNING`.
7. Call the generator once.
8. Record Manifest Guard outcome.
9. Return `MANIFEST_APPROVED` without reading AppDeploy configuration.

Do not store rejected sensitive parameters.

- [ ] **Step 5: Run focused and package tests**

```powershell
go test ./internal/api -run CreateControlRun
go test ./internal/api ./internal/deploymentplanner
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add go/service-control-api/internal/api/control_run_models.go go/service-control-api/internal/api/control_run_service.go go/service-control-api/internal/api/control_run_service_test.go go/service-control-api/internal/api/service.go go/service-control-api/internal/deploymentplanner/generator.go
git commit -m "feat: generate guarded manifests as control runs"
```

### Task 4: ControlRun REST API and Deletion

**Files:**
- Create: `go/service-control-api/internal/api/control_run_api.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`

**Interfaces:**
- Produces:

```text
POST   /api/v1/control-runs
GET    /api/v1/control-runs
GET    /api/v1/control-runs/:run_id
DELETE /api/v1/control-runs/:run_id
DELETE /api/v1/control-runs
```

- [ ] **Step 1: Write failing route and HTTP contract tests**

Test:

- route registration
- `201` Manifest approval
- `400` Request Guard rejection with persisted Run payload
- `403` Agent rejection
- `422` Manifest rejection
- newest-first list
- `404` unknown Run
- individual and clear deletion
- delete routes use existing `requireAutonomyAdmin`

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/api -run 'ControlRun|RoutesInclude'
```

Expected: missing routes or handlers.

- [ ] **Step 3: Implement handlers and status mapping**

Add `pathControlRuns = "/api/v1/control-runs"` and Swagger annotations. Error payloads return the Run when a Run was created, preserving Guard evidence.

- [ ] **Step 4: Run API tests**

```powershell
go test ./internal/api -run 'ControlRun|RoutesInclude'
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add go/service-control-api/internal/api/control_run_api.go go/service-control-api/internal/api/server.go go/service-control-api/internal/api/server_test.go go/service-control-api/internal/api/openapi_contract_test.go
git commit -m "feat: expose control run manifest API"
```

### Task 5: Optional AppDeploy Submission and Compatibility Endpoint

**Files:**
- Modify: `go/service-control-api/internal/deploymentplanner/planner.go`
- Modify: `go/service-control-api/internal/deploymentplanner/planner_test.go`
- Modify: `go/service-control-api/internal/api/control_run_service.go`
- Modify: `go/service-control-api/internal/api/control_run_service_test.go`
- Modify: `go/service-control-api/internal/api/control_run_api.go`
- Modify: `go/service-control-api/internal/api/planner_service.go`
- Modify: `go/service-control-api/internal/api/planner_models.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces:

```go
type DeployRequest struct {
    Manifest        appdeploy.DeploymentManifest
    PollInterval    time.Duration
    MaxPollAttempts int
}

func (planner Planner) DeployApprovedManifest(context.Context, DeployRequest) (Response, error)
func (service Service) SubmitControlRun(context.Context, string, SubmitControlRunRequest) (controlrun.Run, error)
```

and:

```text
POST /api/v1/control-runs/:run_id/submit
```

- [ ] **Step 1: Write failing deployment composition tests**

Verify `DeployApprovedManifest`:

- never invokes the Manifest generator
- submits the supplied approved Manifest
- retains polling and log behavior
- rejects missing deployer

Verify `SubmitControlRun`:

- only accepts `MANIFEST_APPROVED` and retryable `APPDEPLOY_FAILED`
- validates `submit_deployment_manifest` through Registry
- records `deployment_id`, polling, and logs
- preserves Manifest after AppDeploy failure
- returns conflict for invalid state

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/deploymentplanner ./internal/api -run 'DeployApprovedManifest|SubmitControlRun'
```

Expected: methods do not exist.

- [ ] **Step 3: Refactor PlanAndDeploy through DeployApprovedManifest**

`PlanAndDeploy` generates once, then delegates the approved Manifest. Preserve its existing response shape and terminal classification.

- [ ] **Step 4: Implement submit API and compatibility composition**

The old `POST /api/v1/planner/deployments` must:

1. Create a ControlRun.
2. Stop and return Guard evidence when Manifest is rejected.
3. Submit the approved Run.
4. Return its existing fields plus `run_id`.

- [ ] **Step 5: Run focused and regression tests**

```powershell
go test ./internal/deploymentplanner
go test ./internal/api -run 'AppDeployPlanner|SubmitControlRun'
```

Expected: PASS and existing Planner API tests remain green.

- [ ] **Step 6: Commit**

```powershell
git add go/service-control-api/internal/deploymentplanner go/service-control-api/internal/api
git commit -m "feat: submit approved control runs to appdeploy"
```

### Task 6: Link Action Proposal, Feedback, and Autonomy

**Files:**
- Modify: `go/service-control-api/internal/api/models.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/api/automation_feedback.go`
- Modify: `go/service-control-api/internal/api/automation_feedback_test.go`
- Modify: `go/service-control-api/internal/autonomy/models.go`
- Modify: `go/service-control-api/internal/autonomy/manager.go`
- Modify: `go/service-control-api/internal/autonomy/manager_test.go`
- Modify: `go/service-control-api/internal/api/autonomy_api.go`
- Modify: `go/service-control-api/internal/api/autonomy_adapters_test.go`

**Interfaces:**
- `LLMAutomationActionRequest.RunID string`
- `AutomationFeedbackRecord.RunID string`
- `autonomy.Config.RunID string`
- `autonomy.Event.RunID string`

- [ ] **Step 1: Write failing linkage tests**

Cover:

- unknown Action Proposal `run_id` rejected
- correlation ID appended to the referenced Run
- Feedback record inherits the Run ID from the approved correlation
- Autonomy configuration resolves the Run deployment ID
- Run without a deployment cannot configure Autonomy
- every Autonomy Event carries configured Run ID

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/api ./internal/autonomy -run 'RunID|ControlRunLink'
```

Expected: fields or linkage behavior do not exist.

- [ ] **Step 3: Implement optional linkage**

Requests without `run_id` retain existing behavior. A supplied `run_id` fails closed when missing or incompatible.

Autonomy does not auto-start when linked. It only copies the deployment identity and emits Run-tagged events.

- [ ] **Step 4: Run focused and regression tests**

```powershell
go test ./internal/api ./internal/autonomy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add go/service-control-api/internal/api go/service-control-api/internal/autonomy
git commit -m "feat: link post-deployment controls to control runs"
```

### Task 7: Agent Control Web Integration

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/styles.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Consumes: ControlRun REST API
- Produces: backend-owned Overview, staged Manifest Planner, explicit AppDeploy submit, Run-linked Autonomy and Feedback

- [ ] **Step 1: Write failing embedded UI contract tests**

Require these strings and controls:

```text
Generate Manifest
Submit to AppDeploy
Request Guard
Agent Registry
Qwen Planner
Manifest Guard
배포 후 자율 운영 실험
data-run-id
```

Also require JavaScript endpoint constants for ControlRun list, create, detail, and submit.

- [ ] **Step 2: Run tests and verify RED**

```powershell
go test ./internal/webui ./internal/api -run 'AgentControl|Embedded|ControlRun'
```

Expected: required UI contracts are absent.

- [ ] **Step 3: Replace local history with backend Run rendering**

Overview loads `GET /api/v1/control-runs`. Remove ControlRun authority from `localStorage`; retaining app-version convenience storage is allowed.

- [ ] **Step 4: Split Generate and Submit controls**

Planner creates a Run, renders its stages and Manifest, and enables Submit only for `MANIFEST_APPROVED`. The compatibility one-click flow may call create then submit.

- [ ] **Step 5: Add Run selection to post-deployment views**

- Agents & Guard shows the selected Manifest Planner Agent.
- Autonomous Loop accepts a deployed Run and fills its deployment ID without starting.
- Feedback includes the linked Run ID.

- [ ] **Step 6: Run embedded UI tests**

```powershell
go test ./internal/webui ./internal/api -run 'AgentControl|Embedded|ControlRun'
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add go/service-control-api/internal/webui go/service-control-api/internal/api/server_test.go
git commit -m "feat: connect agent control views through control runs"
```

### Task 8: Swagger and User Documentation

**Files:**
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`
- Modify: `go/service-control-api/docs/swagger/docs.go`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `docs/design/main_llm_go_guard_control_flow.md`

**Interfaces:**
- Documents: Manifest-only path, optional AppDeploy submit, Run-linked post-deployment path

- [ ] **Step 1: Update OpenAPI contract expectations**

Require operation IDs for create, list, get, submit, delete, and clear ControlRuns.

- [ ] **Step 2: Run contract test and verify RED**

```powershell
go test ./internal/api -run GeneratedSwaggerIncludesDocumentedRoutes
```

Expected: generated Swagger lacks new operations.

- [ ] **Step 3: Regenerate Swagger**

From repository root:

```powershell
make swag
```

If `make` is unavailable on Windows, run the exact `go run github.com/swaggo/swag/cmd/swag@v1.16.6 init` command from the Makefile.

- [ ] **Step 4: Update README flows**

Document:

```text
Request -> Request Guard -> Registry -> Qwen -> Manifest Guard -> Manifest
Manifest -> optional AppDeploy -> optional Autonomous Loop -> Feedback
```

Explicitly state that external Agent execution and AppDeploy implementation are outside geon.

- [ ] **Step 5: Run docs and API tests**

```powershell
go test ./internal/api
git diff --check
```

Expected: PASS and no whitespace errors.

- [ ] **Step 6: Commit**

```powershell
git add README.md go/service-control-api/README.md go/service-control-api/docs/swagger docs/design/main_llm_go_guard_control_flow.md
git commit -m "docs: explain registry-driven control run workflow"
```

### Task 9: Full Verification and Browser Flow

**Files:**
- Modify only if a failing verification reveals an in-scope defect

**Interfaces:**
- Verifies the complete repository and visible Agent Control workflow

- [ ] **Step 1: Run formatting**

```powershell
gofmt -w go/service-control-api/internal/controlrun go/service-control-api/internal/api go/service-control-api/internal/autonomy go/service-control-api/internal/deploymentplanner
```

- [ ] **Step 2: Run full Go tests and vet**

```powershell
go test ./...
go vet ./...
```

Run from `go/service-control-api`, then run repository-level `make test` and `make vet` when available.

- [ ] **Step 3: Run race-sensitive packages**

```powershell
go test -race ./internal/controlrun ./internal/autonomy ./internal/api
```

- [ ] **Step 4: Start local dependencies**

Verify Ollama:

```powershell
curl.exe http://127.0.0.1:11434/api/tags
```

Run AppDeploy only for the optional submit test. Manifest generation must also be tested with AppDeploy stopped.

- [ ] **Step 5: Run browser verification**

At desktop and mobile viewports verify:

- Overview loads backend Runs.
- Generate Manifest shows all four stages.
- Approved Manifest is copyable.
- Submit stays disabled until approval.
- AppDeploy-offline generation succeeds.
- Deployed Run fills Autonomous Loop without auto-start.
- Feedback displays Run ID.
- No text overlap or horizontal clipping.

- [ ] **Step 6: Inspect repository scope**

```powershell
git status --short
git diff --check
git log --oneline -10
```

Confirm `config/inference_optimization.json` was not staged or modified and no AppDeploy path changed.

- [ ] **Step 7: Final integration commit if required**

Only when verification fixes produced changes:

```powershell
git add go/service-control-api/internal/controlrun go/service-control-api/internal/api go/service-control-api/internal/autonomy go/service-control-api/internal/deploymentplanner go/service-control-api/internal/webui go/service-control-api/docs/swagger README.md go/service-control-api/README.md docs/design/main_llm_go_guard_control_flow.md
git commit -m "fix: complete control run integration verification"
```
