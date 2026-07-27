# Agent Dispatcher and Runtime Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `AIApplicationAutomationAgent` use a common Agent Dispatcher to produce a guarded `DeploymentManifest`, and allow a registered runtime Agent endpoint to be invoked through the same guarded execution contract.

**Architecture:** Keep Agent profiles and API orchestration in `internal/api`. Add a small executor interface, a source-based Dispatcher, an internal Qwen Manifest Executor, and a bounded HTTP Runtime Executor. Extend `ControlRun` with generic Agent execution evidence while preserving the existing Manifest and optional AppDeploy submission workflow.

**Tech Stack:** Go 1.25, Echo v4, `net/http`, existing Qwen `llmclient`, existing `deploymentplanner`, existing Go Guards, embedded HTML/CSS/JavaScript.

## Global Constraints

- Do not modify the AppDeploy repository or transfer final Target selection into geon.
- Do not implement Job Scheduling, a Job Scheduler API, or a `JobSchedulingAgent`.
- Do not build multi-Agent DAGs, delegation, retry orchestration, or a workflow engine.
- Do not convert failed Agent or LLM calls into successful mock results.
- Keep Manifest generation usable without AppDeploy.
- Keep runtime Agent registration in process memory.
- Do not store Authorization values, tokens, private keys, or credential-shaped input in Registry or ControlRun records.
- Preserve existing API paths and existing UI workflows.
- Use TDD for each behavior change.

---

### Task 1: Define the Agent execution contract and result Guard

**Files:**
- Create: `go/service-control-api/internal/api/agent_execution_models.go`
- Create: `go/service-control-api/internal/api/agent_execution_guard.go`
- Create: `go/service-control-api/internal/api/agent_execution_guard_test.go`

**Interfaces:**
- Produces: `AgentExecutionRequest`, `AgentDispatchRequest`, `AgentProposal`, `AgentExecutionResult`, `AgentExecutionResponse`
- Produces: `validateAgentExecutionRequest(AgentProfile, AgentExecutionRequest) GuardDecision`
- Produces: `validateAgentExecutionResult(AgentProfile, AgentDispatchRequest, AgentExecutionResult) GuardDecision`

- [ ] **Step 1: Write failing contract and Guard tests**

Add tests that prove:

```go
func TestValidateAgentExecutionRequestRejectsUnregisteredCapabilityAndAction(t *testing.T)
func TestValidateAgentExecutionRequestRejectsCredentialFields(t *testing.T)
func TestValidateAgentExecutionResultRejectsMismatchedRunAgentAndAction(t *testing.T)
func TestValidateAgentExecutionResultApprovesBoundedCompletedResult(t *testing.T)
```

Use a profile with capability `deployment_review` and action `review_deployment_plan`. Assert that `token`, `password`, `private_key`, `secret`, and `credential` keys are rejected recursively.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
cd go/service-control-api
go test ./internal/api -run 'TestValidateAgentExecution' -count=1
```

Expected: FAIL because execution contract types and Guard functions do not exist.

- [ ] **Step 3: Add the request and result types**

Add the following shapes to `agent_execution_models.go`:

```go
type AgentExecutionRequest struct {
    Capability string         `json:"capability" validate:"required"`
    Action     string         `json:"action" validate:"required"`
    Input      map[string]any `json:"input,omitempty"`
    Context    map[string]any `json:"context,omitempty"`
}

type AgentDispatchRequest struct {
    RunID      string
    Agent      string
    Capability string
    Action     string
    Input      map[string]any
    Context    map[string]any
}

type AgentProposal struct {
    Action     string         `json:"action"`
    Parameters map[string]any `json:"parameters,omitempty"`
}

type AgentExecutionResult struct {
    RunID      string         `json:"run_id"`
    Agent      string         `json:"agent"`
    Status     string         `json:"status"`
    Proposal   AgentProposal  `json:"proposal"`
    Result     map[string]any `json:"result,omitempty"`
    Evidence   map[string]any `json:"evidence,omitempty"`
    Message    string         `json:"message,omitempty"`
    LatencyMS  int64          `json:"latency_ms"`
    Manifest   *appdeploy.DeploymentManifest     `json:"manifest,omitempty"`
    Generation *deploymentplanner.GenerateResult `json:"generation,omitempty"`
    DomainValidation string   `json:"domain_validation"`
}

type AgentExecutionResponse struct {
    RequestID    string               `json:"request_id"`
    RunID        string               `json:"run_id"`
    SelectedAgent AgentProfile        `json:"selected_agent"`
    RequestGuard GuardDecision        `json:"request_guard"`
    Execution    AgentExecutionResult `json:"execution"`
    ResultGuard  GuardDecision        `json:"result_guard"`
}
```

- [ ] **Step 4: Implement deterministic request and result validation**

Implement recursive input key inspection, allowed status values `completed`, `rejected`, `failed`, run and Agent identity matching, and bounded proposal action validation. Return existing `GuardDecision` values with explicit reasons.

Reject `endpoint`, `invocation_path`, `target_url`, and `authorization` in input or context so an execution request cannot override the Registry endpoint. Set `domain_validation=manifest_guard` for a Manifest result and `domain_validation=not_registered` for other Agent result types.

- [ ] **Step 5: Run focused tests**

Run:

```bash
go test ./internal/api -run 'TestValidateAgentExecution' -count=1
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add go/service-control-api/internal/api/agent_execution_models.go \
  go/service-control-api/internal/api/agent_execution_guard.go \
  go/service-control-api/internal/api/agent_execution_guard_test.go
git commit -m "feat: define guarded agent execution contract"
```

---

### Task 2: Implement the bounded HTTP Runtime Agent Executor

**Files:**
- Modify: `go/service-control-api/internal/api/config.go`
- Modify: `go/service-control-api/internal/api/models.go`
- Modify: `go/service-control-api/internal/api/agent_registry_runtime.go`
- Create: `go/service-control-api/internal/api/agent_http_executor.go`
- Create: `go/service-control-api/internal/api/agent_http_executor_test.go`
- Modify: `go/service-control-api/internal/api/agent_registry_test.go`

**Interfaces:**
- Consumes: `AgentDispatchRequest`, `AgentExecutionResult`
- Produces: `type agentExecutor interface { Execute(context.Context, AgentProfile, AgentDispatchRequest) (AgentExecutionResult, error) }`
- Produces: `newHTTPAgentExecutor(httpDoer, time.Duration) agentExecutor`

- [ ] **Step 1: Write failing HTTP Executor tests**

Use `httptest.Server` to add:

```go
func TestHTTPAgentExecutorPostsContractAndReturnsValidatedEnvelope(t *testing.T)
func TestHTTPAgentExecutorRejectsRedirectAndNon2xx(t *testing.T)
func TestHTTPAgentExecutorRejectsOversizedAndInvalidJSONResponses(t *testing.T)
func TestHTTPAgentExecutorTimesOut(t *testing.T)
```

The successful server must assert method `POST`, `Content-Type: application/json`, received `run_id`, Agent, capability, action, input, and context. The HTTP Executor receives `AgentProfile` separately and builds the URL only from `profile.Endpoint + profile.InvocationPath`.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
go test ./internal/api -run 'TestHTTPAgentExecutor' -count=1
```

Expected: FAIL because the Executor does not exist.

- [ ] **Step 3: Add timeout configuration**

Add `AgentExecutionTimeout time.Duration` to `ServerConfig`. Read `AIOPS_AGENT_EXECUTION_TIMEOUT_SECONDS`, default to 30 seconds, and clamp values above 120 seconds to 120 seconds.

- [ ] **Step 4: Add an optional token environment reference**

Add `AuthTokenEnv` to `ExternalAgentRegistrationRequest` and `AgentProfile` as `auth_token_env,omitempty`. Validate it with:

```go
var environmentNamePattern = regexp.MustCompile(`^[A-Z_][A-Z0-9_]{1,127}$`)
```

Store only the environment variable name. Read its value only immediately before the HTTP call and set `Authorization: Bearer <value>`. Never put the value in an error, response, Registry record, or ControlRun.

- [ ] **Step 5: Implement the HTTP Executor**

Implement:

- JSON request serialization
- context timeout
- redirect refusal through a configured `http.Client.CheckRedirect`
- response body limit `1 << 20`
- 2xx requirement
- JSON decoding with unknown fields allowed
- latency measurement
- response body close on every path

- [ ] **Step 6: Run focused tests**

```bash
go test ./internal/api -run 'TestHTTPAgentExecutor|TestRegisterExternalAgent' -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add go/service-control-api/internal/api/config.go \
  go/service-control-api/internal/api/models.go \
  go/service-control-api/internal/api/agent_registry_runtime.go \
  go/service-control-api/internal/api/agent_registry_test.go \
  go/service-control-api/internal/api/agent_http_executor.go \
  go/service-control-api/internal/api/agent_http_executor_test.go
git commit -m "feat: execute bounded runtime agent endpoints"
```

---

### Task 3: Add the source-based Dispatcher and internal Manifest Executor

**Files:**
- Create: `go/service-control-api/internal/api/agent_dispatcher.go`
- Create: `go/service-control-api/internal/api/agent_dispatcher_test.go`
- Create: `go/service-control-api/internal/api/manifest_agent_executor.go`
- Create: `go/service-control-api/internal/api/manifest_agent_executor_test.go`
- Modify: `go/service-control-api/internal/api/planner_agent_resolver_test.go`

**Interfaces:**
- Consumes: `AgentProfile`, `AgentDispatchRequest`, `agentExecutor`
- Produces: `newAgentDispatcher(map[string]agentExecutor, agentExecutor) *agentDispatcher`
- Produces: `Dispatch(context.Context, AgentProfile, AgentDispatchRequest) (AgentExecutionResult, error)`
- Produces: `newManifestAgentExecutor(candidatesPath string, generator controlRunManifestGenerator) agentExecutor`

- [ ] **Step 1: Write failing Dispatcher tests**

Add:

```go
func TestAgentDispatcherUsesInternalExecutorForConfigurationAgent(t *testing.T)
func TestAgentDispatcherUsesHTTPExecutorForRuntimeAgent(t *testing.T)
func TestAgentDispatcherRejectsUnknownInternalAgent(t *testing.T)
func TestAgentDispatcherRejectsUnsupportedSource(t *testing.T)
func TestResolvePlannerAgentRejectsAmbiguousEligibleAgents(t *testing.T)
```

Assert exactly one Executor is called.

- [ ] **Step 2: Write failing Manifest Executor test**

Provide an input map containing:

```json
{
  "natural_language_request": "Deploy a GPU inference app.",
  "app_version_id": "appver-001",
  "candidate_id": "qwen3.5-ops-planner",
  "requested_by": "ai-ops-geon-planner"
}
```

Assert the fake Generator is called and the result envelope contains `proposal.action=generate_deployment_manifest`, `status=completed`, and a typed Manifest result available to the ControlRun service.

- [ ] **Step 3: Run focused tests and verify failure**

```bash
go test ./internal/api -run 'TestAgentDispatcher|TestManifestAgentExecutor' -count=1
```

Expected: FAIL because Dispatcher and internal Executor do not exist.

- [ ] **Step 4: Implement Dispatcher routing**

Use:

```go
switch agent.Source {
case agentSourceConfiguration:
    executor, ok := dispatcher.internal[agent.Name]
case agentSourceRuntime:
    executor = dispatcher.runtime
default:
    return result, fmt.Errorf("unsupported agent source: %s", agent.Source)
}
```

Call `executor.Execute(ctx, agent, request)`. Do not select an Executor by user-supplied URL or type.

- [ ] **Step 5: Implement the internal Manifest Executor**

Decode the input map into `CreateControlRunRequest`, load the configured Qwen candidate, call the supplied `controlRunManifestGenerator`, and return the generation metadata plus Manifest. Keep Manifest Guard in the ControlRun service so Guard evidence remains a distinct stage.

- [ ] **Step 6: Run focused tests**

```bash
go test ./internal/api -run 'TestAgentDispatcher|TestManifestAgentExecutor|TestResolvePlannerAgent' -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add go/service-control-api/internal/api/agent_dispatcher.go \
  go/service-control-api/internal/api/agent_dispatcher_test.go \
  go/service-control-api/internal/api/manifest_agent_executor.go \
  go/service-control-api/internal/api/manifest_agent_executor_test.go \
  go/service-control-api/internal/api/planner_agent_resolver_test.go
git commit -m "feat: dispatch internal and runtime agents"
```

---

### Task 4: Connect Dispatcher execution to ControlRun and the REST API

**Files:**
- Modify: `go/service-control-api/internal/controlrun/models.go`
- Modify: `go/service-control-api/internal/controlrun/store.go`
- Modify: `go/service-control-api/internal/controlrun/store_test.go`
- Create: `go/service-control-api/internal/api/agent_execution_service.go`
- Create: `go/service-control-api/internal/api/agent_execution_service_test.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces: `Service.ExecuteAgent(context.Context, string, AgentExecutionRequest) (AgentExecutionResponse, error)`
- Produces: `Service.ExecuteAgentWithDispatcher(context.Context, string, AgentExecutionRequest, *agentDispatcher) (AgentExecutionResponse, error)`
- Produces: `POST /api/v1/agents/{name}/execute`

- [ ] **Step 1: Write failing ControlRun clone tests**

Extend `controlrun.Run` with an Agent execution record and verify Store `Create`, `Update`, `Get`, and `List` return deep copies of result, evidence, and proposal parameter maps.

- [ ] **Step 2: Write failing service tests**

Add:

```go
func TestExecuteRuntimeAgentRecordsApprovedControlRun(t *testing.T)
func TestExecuteRuntimeAgentStopsBeforeDispatchWhenGuardRejects(t *testing.T)
func TestExecuteRuntimeAgentRecordsEndpointFailure(t *testing.T)
func TestExecuteInternalManifestAgentReturnsApprovedManifestRun(t *testing.T)
```

Assert stages:

```text
agent_registry
agent_request_guard
agent_dispatch
agent_result_guard
```

For the internal Manifest Agent, assert the existing Manifest stages continue after dispatch and final status is `MANIFEST_APPROVED`.

- [ ] **Step 3: Write failing REST API test**

Register an `ExternalResearchAgent` backed by `httptest.Server`, call:

```text
POST /api/v1/agents/ExternalResearchAgent/execute
```

Assert HTTP 200, `execution.status=completed`, `result_guard.status=approved`, and a non-empty `run_id`. Add 403, 404, 422, 502, and 504 mapping tests.

- [ ] **Step 4: Run focused tests and verify failure**

```bash
go test ./internal/controlrun ./internal/api -run 'TestExecute|TestControlRun' -count=1
```

Expected: FAIL because execution storage, service, and route do not exist.

- [ ] **Step 5: Extend ControlRun**

Add:

```go
const (
    StatusAgentDispatching Status = "AGENT_DISPATCHING"
    StatusAgentCompleted   Status = "AGENT_COMPLETED"
    StatusAgentFailed      Status = "AGENT_FAILED"
    StatusResultRejected   Status = "RESULT_REJECTED"
)

type AgentExecution struct {
    Status      string         `json:"status"`
    LatencyMS   int64          `json:"latency_ms"`
    Proposal    map[string]any `json:"proposal,omitempty"`
    Result      map[string]any `json:"result,omitempty"`
    Evidence    map[string]any `json:"evidence,omitempty"`
    GuardStatus string         `json:"guard_status,omitempty"`
    Message     string         `json:"message,omitempty"`
}
```

Add `Execution *AgentExecution` to `Run` and deep clone every map.

- [ ] **Step 6: Implement Service execution orchestration**

The service must:

1. generate `run_id`
2. create ControlRun
3. show the named Agent
4. validate enabled, capability, and action
5. record `SelectedAgent`
6. dispatch
7. validate result
8. record latency and result
9. return an explicit failed or rejected state without a fake success

For `AIApplicationAutomationAgent`, route the request through the existing Manifest ControlRun function after converting input into `CreateControlRunRequest`. That function is refactored in Task 5 to use Dispatcher internally.

- [ ] **Step 7: Add REST handler and error mapping**

Register:

```go
server.POST(
    pathAgents+"/:name/execute",
    handler.requireAutonomyAdmin(handler.RestPostAgentExecute),
)
```

Keep loopback use available without a token through the existing admin middleware behavior.

- [ ] **Step 8: Run focused tests**

```bash
go test ./internal/controlrun ./internal/api -run 'TestExecute|TestControlRun|TestExternalAgent' -count=1
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add go/service-control-api/internal/controlrun \
  go/service-control-api/internal/api/agent_execution_service.go \
  go/service-control-api/internal/api/agent_execution_service_test.go \
  go/service-control-api/internal/api/service.go \
  go/service-control-api/internal/api/server.go \
  go/service-control-api/internal/api/server_test.go
git commit -m "feat: track agent execution in control runs"
```

---

### Task 5: Route the existing Manifest ControlRun through the Dispatcher

**Files:**
- Modify: `go/service-control-api/internal/api/control_run_service.go`
- Modify: `go/service-control-api/internal/api/control_run_service_test.go`
- Modify: `go/service-control-api/internal/api/planner_agent_resolver.go`
- Modify: `go/service-control-api/internal/controlrun/models.go`

**Interfaces:**
- Consumes: `agentDispatcher`, `manifestAgentExecutor`
- Preserves: `CreateControlRun`, `CreateControlRunWithDependencies`, `SubmitControlRun`

- [ ] **Step 1: Change the approved Manifest test to require Dispatcher evidence**

Update `TestCreateControlRunReturnsApprovedManifestWithoutAppDeploy` to require stages:

```text
request_guard
agent_registry
agent_dispatch
qwen_planner
manifest_guard
```

Assert:

```go
run.SelectedAgent.Name == "AIApplicationAutomationAgent"
run.SelectedAgent.Source == agentSourceConfiguration
run.Execution.Status == "completed"
run.Manifest.Spec.AppVersionID == "appver-001"
```

- [ ] **Step 2: Add failure tests**

Add tests proving:

- disabled Agent stops before Dispatcher
- missing internal Executor stops before Qwen
- Qwen error becomes `MANIFEST_REJECTED`
- Manifest Guard rejection remains separate from Dispatcher completion

- [ ] **Step 3: Run focused tests and verify failure**

```bash
go test ./internal/api -run 'TestCreateControlRun' -count=1
```

Expected: FAIL because the existing path directly calls the Generator.

- [ ] **Step 4: Replace the direct Generator call**

Construct a Manifest Executor from the supplied Generator and candidate path, then call Dispatcher with the Registry-selected Agent. Record `agent_dispatch` before preserving the existing `qwen_planner` generation evidence and `manifest_guard` result.

- [ ] **Step 5: Preserve AppDeploy submission**

Run existing submit tests to ensure `MANIFEST_APPROVED -> SUBMITTING -> DEPLOYED` behavior and final Target ownership remain unchanged.

- [ ] **Step 6: Run focused tests**

```bash
go test ./internal/api -run 'TestCreateControlRun|TestSubmitControlRun|TestRunAppDeployPlanner' -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add go/service-control-api/internal/api/control_run_service.go \
  go/service-control-api/internal/api/control_run_service_test.go \
  go/service-control-api/internal/api/planner_agent_resolver.go \
  go/service-control-api/internal/controlrun/models.go
git commit -m "refactor: dispatch manifest automation agent"
```

---

### Task 6: Add Agent execution controls to the web UI

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Consumes: `POST /api/v1/agents/{name}/execute`
- Produces: Agent source badges, execute command, execution dialog, result rendering, ControlRun link

- [ ] **Step 1: Write failing embedded UI contract test**

Require:

```text
id="agent-execution-dialog"
id="agent-execution-form"
id="agent-execution-result"
data-execute-agent
/execute
executeAgent
```

Also require source labels and keep existing delete controls.

- [ ] **Step 2: Run focused test and verify failure**

```bash
go test ./internal/webui -run 'TestControlAppContainsAgentExecution' -count=1
```

Expected: FAIL because the execution UI does not exist.

- [ ] **Step 3: Add source and execute controls**

Render `internal` or `external` next to each Agent. Add a play icon command to enabled Agent rows. Preserve the trash command only for runtime Agents.

- [ ] **Step 4: Add a focused execution dialog**

The dialog contains:

- read-only Agent name and source
- capability select
- bounded action select
- JSON input textarea
- execute command
- status badge
- latency
- result JSON
- resulting `run_id`

For `AIApplicationAutomationAgent`, default input contains the current saved App Version ID and a sample natural-language VM deployment request. For runtime Agents, default input is `{}`. Do not include Job Scheduling examples.

Add an optional `Auth Token Environment Variable` field to the existing Runtime Agent registration dialog. Display and submit only the environment variable name, never its value.

- [ ] **Step 5: Implement browser execution behavior**

Parse JSON before the request, disable submit during execution, POST the selected capability/action/input, render approved/rejected/failed status, refresh ControlRuns, and preserve the response for copy.

- [ ] **Step 6: Add responsive styles**

Keep the existing 8px-or-less radius, existing color system, stable icon command dimensions, and mobile layout without horizontal overflow.

- [ ] **Step 7: Run UI tests**

```bash
go test ./internal/webui -count=1
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add go/service-control-api/internal/webui
git commit -m "feat: add guarded agent execution UI"
```

---

### Task 7: Update API contracts, guides, and verification

**Files:**
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`
- Modify: `docs/deliverables/02_agent_registration_management_prototype.md`
- Modify: `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md`

**Interfaces:**
- Documents: `POST /api/v1/agents/{name}/execute`
- Documents: internal Manifest Agent versus external Runtime Agent result boundaries

- [ ] **Step 1: Write failing contract tests**

Require `ExecuteAgent` and `/api/v1/agents/{name}/execute` in the submission OpenAPI and generated Swagger files.

- [ ] **Step 2: Run contract tests and verify failure**

```bash
go test ./internal/api -run 'TestSubmissionOpenAPI|TestGeneratedSwagger' -count=1
```

Expected: FAIL because the new operation is not documented.

- [ ] **Step 3: Add Swagger annotations and regenerate**

Add the handler annotation and run:

```bash
make swag
```

Then update the manually maintained submission OpenAPI with request, response, and error schemas.

- [ ] **Step 4: Update user and deliverable documentation**

Document:

```text
Registry -> Request Guard -> Dispatcher
  -> AIApplicationAutomationAgent -> Qwen -> DeploymentManifest -> Manifest Guard
  -> Runtime Agent HTTP endpoint -> Result Guard
```

State explicitly that:

- only `AIApplicationAutomationAgent` is the built-in functional Agent
- no Job Scheduling Agent is included
- runtime Agent execution is one bounded HTTP call
- AppDeploy remains the actual deployment executor

- [ ] **Step 5: Run formatting and full Go verification**

```bash
gofmt -w go/service-control-api/internal/api go/service-control-api/internal/controlrun
make test
make vet
```

Expected: all commands exit 0.

- [ ] **Step 6: Run the local browser verification**

Start Ollama only when validating the real internal Qwen path. Start geon at `127.0.0.1:18080`, register an `ExternalResearchAgent` backed by a local test endpoint, and verify:

- Agent row shows `external`
- execute command makes one HTTP request
- approved result and latency appear
- ControlRun timeline appears
- `AIApplicationAutomationAgent` produces `MANIFEST_APPROVED`
- Manifest generation does not require AppDeploy
- no console errors
- no horizontal overflow at desktop and 390x844 mobile

- [ ] **Step 7: Commit**

```bash
git add README.md \
  go/service-control-api/README.md \
  docs/submission/openapi_service_control.yaml \
  docs/deliverables/02_agent_registration_management_prototype.md \
  docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md \
  go/service-control-api/docs/swagger \
  go/service-control-api/internal/api/openapi_contract_test.go
git commit -m "docs: explain executable agent registry"
```

- [ ] **Step 8: Final repository audit**

Run:

```bash
git status --short --branch
git log --oneline -8
```

Confirm that the existing untracked `config/inference_optimization.json` was not added, changed, or deleted by this implementation.
