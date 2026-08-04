# Registry-Selected Deployment Decision Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect eligible Internal and Runtime Agents from Agent Registry to the one-shot automation workflow and expose the selected Agent, Guard evidence, decision, DesiredDeploymentSpec, Adapter evidence, and experiment record under one run.

**Architecture:** `agentcontrol` owns platform-neutral request analysis, recommendation joining, deployment decision validation, DesiredDeploymentSpec generation, and flow records. `api` owns Registry resolution, default-Agent policy, Dispatcher routing, HTTP execution, and generic request/result Guards. A narrow `DecisionAgentRuntime` interface prevents `agentcontrol` from importing API-layer types.

**Tech Stack:** Go 1.22+, Echo, embedded HTML/CSS/JavaScript, Go unit/API tests, Node browser tests, Playwright.

## Global Constraints

- Work only on the existing `geon` branch; do not create a new branch or worktree.
- Do not modify AppDeploy code or its API contract.
- Keep `DeploymentManifest` separate from operational Action Proposal behavior.
- Eligible decision Agents require `enabled=true`, capability `ai_application_automation`, and bounded action `generate_deployment_decision`.
- `AIApplicationAutomationAgent` is the Registry default Internal Agent, not a Go string selected by the workflow.
- Runtime Agent failure must not silently fall back to the Internal Agent.
- Go Guard remains outside every selected Agent and must approve the decision before DesiredDeploymentSpec creation.
- `mock` Adapter remains `SIMULATED`; `handoff` remains `READY`; neither is evidence of an external deployment.
- Runtime Agents remain process-memory registrations and disappear after server restart.
- Do not modify untracked `config/inference_optimization.json` or `tmp/`.

---

### Task 1: Registry Default and Eligible Decision-Agent Resolution

**Files:**
- Modify: `config/agent_registry.json`
- Modify: `go/service-control-api/internal/api/models.go`
- Create: `go/service-control-api/internal/api/decision_agent_resolver.go`
- Create: `go/service-control-api/internal/api/decision_agent_resolver_test.go`
- Modify: `go/service-control-api/internal/api/service.go`

**Interfaces:**
- Consumes: configured `AgentRegistry` and process-memory `runtimeAgentStore`.
- Produces: `resolveDecisionAgent(registry AgentRegistry, runtime []AgentProfile, requestedName string) (AgentProfile, error)` and `eligibleDecisionAgents(...) []AgentProfile`.

- [ ] **Step 1: Write failing resolver tests**

Add tests proving that an omitted name selects `registry.Defaults["ai_application_automation"]`, an explicitly selected eligible Runtime Agent resolves, and disabled or under-authorized Agents are rejected.

```go
registry := AgentRegistry{
    Defaults: map[string]string{
        agentcontrol.AutomationCapability: "AIApplicationAutomationAgent",
    },
    Agents: []AgentProfile{eligibleDecisionAgent("AIApplicationAutomationAgent", agentSourceConfiguration)},
}
selected, err := resolveDecisionAgent(registry, nil, "")
if err != nil || selected.Name != "AIApplicationAutomationAgent" {
    t.Fatalf("selected = %#v, err = %v", selected, err)
}
```

- [ ] **Step 2: Verify the resolver tests fail**

Run: `go test ./internal/api -run 'TestResolveDecisionAgent|TestEligibleDecisionAgents' -count=1`

Expected: FAIL because `Defaults`, `resolveDecisionAgent`, and `eligibleDecisionAgents` are not defined.

- [ ] **Step 3: Implement Registry defaults and eligibility filtering**

Extend `AgentRegistry` with:

```go
Defaults map[string]string `json:"defaults,omitempty"`
```

Resolve configuration and Runtime Agents through the same eligibility predicate. Preserve explicit selection errors and reject ambiguous or missing defaults.

- [ ] **Step 4: Return default and eligibility evidence from the Agents API**

Add `defaults` and an `eligible_decision_agents` array to `ListAgents` without changing the existing `agents` array.

- [ ] **Step 5: Run focused tests and commit**

Run: `go test ./internal/api -run 'TestResolveDecisionAgent|TestEligibleDecisionAgents|TestListAgents' -count=1`

Commit: `feat: resolve registry decision agents`

---

### Task 2: Common Decision Runtime Contract and Domain Guard

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/models.go`
- Create: `go/service-control-api/internal/agentcontrol/decision_runtime.go`
- Create: `go/service-control-api/internal/agentcontrol/decision_runtime_test.go`
- Modify: `go/service-control-api/internal/agentcontrol/service.go`
- Modify: `go/service-control-api/internal/agentcontrol/service_test.go`

**Interfaces:**
- Consumes: `ApplicationProfile`, `ResourceRecommendation`, requested Agent name, correlation and trace identifiers.
- Produces: `DecisionAgentRuntime.Decide(context.Context, DecisionAgentRequest) (DecisionAgentResult, error)` and a guarded `Flow`.

- [ ] **Step 1: Write failing decision-domain tests**

Cover allowed decisions, missing candidate for DEPLOY, unknown candidate, under-sized candidate, invalid confidence, Runtime failure, and preservation of selected-Agent evidence.

```go
result := DecisionAgentResult{
    AgentName: "RuntimeDeploymentAgent",
    Source: "runtime",
    Status: "completed",
    Decision: AutomationDecision{
        Action: ActionDeploy,
        SelectedCandidateID: "missing-candidate",
        Confidence: 0.9,
    },
}
guarded := validateDecisionAgentResult(profile, recommendation, result)
if guarded.Guard.Status != GuardRetryRequired {
    t.Fatalf("guard = %#v", guarded.Guard)
}
```

- [ ] **Step 2: Verify the decision tests fail**

Run: `go test ./internal/agentcontrol -run 'TestDecisionAgent|TestServiceUsesSelectedDecisionAgent' -count=1`

Expected: FAIL because the runtime contract and decision validation do not exist.

- [ ] **Step 3: Add shared request, result, and evidence types**

Add:

```go
type DecisionAgentRuntime interface {
    Decide(context.Context, DecisionAgentRequest) (DecisionAgentResult, error)
}

type DecisionAgentRequest struct {
    RunID string
    RequestedAgent string
    CorrelationID string
    TraceID string
    ApplicationProfile ApplicationProfile
    ResourceRecommendation ResourceRecommendation
}

type DecisionAgentResult struct {
    AgentName string `json:"agent_name"`
    Source string `json:"source"`
    Status string `json:"status"`
    RequestGuard GuardResult `json:"request_guard"`
    ResultGuard GuardResult `json:"result_guard"`
    Decision AutomationDecision `json:"decision"`
    LatencyMS int64 `json:"latency_ms"`
}
```

Add `DecisionAgent string` to `AutomationRunInput` and `AgentExecution *DecisionAgentResult` to `Flow`.

- [ ] **Step 4: Extract the current rule decision and apply the domain Guard**

Move existing requirement validation and candidate selection into a reusable rule decision function. Validate any Internal or Runtime result before building `DeploymentPlan`, `DesiredDeploymentSpec`, and `deployment.create.request`.

- [ ] **Step 5: Allow selected-Agent evaluation without breaking protocol inputs**

Add `ReceiveResourceRecommendationForAgent(ctx, message, requestedAgent, runID)` and make the existing `ReceiveResourceRecommendation` delegate with empty Agent and run ID. When no `DecisionAgentRuntime` is injected, preserve the existing rule-based behavior for focused package tests.

- [ ] **Step 6: Run focused and package tests, then commit**

Run: `go test ./internal/agentcontrol -count=1`

Commit: `feat: add guarded decision agent runtime`

---

### Task 3: API Dispatcher Bridge for Internal and Runtime Decision Agents

**Files:**
- Create: `go/service-control-api/internal/api/decision_agent_runtime.go`
- Create: `go/service-control-api/internal/api/decision_agent_runtime_test.go`
- Create: `go/service-control-api/internal/api/internal_decision_agent_executor.go`
- Create: `go/service-control-api/internal/api/internal_decision_agent_executor_test.go`
- Modify: `go/service-control-api/internal/api/agent_execution_guard.go`
- Modify: `go/service-control-api/internal/api/agent_execution_guard_test.go`
- Modify: `go/service-control-api/internal/api/service.go`

**Interfaces:**
- Consumes: Task 1 resolver and Task 2 `DecisionAgentRuntime` contract.
- Produces: API bridge that resolves, guards, dispatches, validates, and maps the selected Agent result.

- [ ] **Step 1: Write failing bridge tests**

Test one Internal Agent call, one Runtime HTTP Agent call, unauthorized Agent rejection, timeout without fallback, mismatched candidate rejection, and `domain_validation="deployment_decision"` acceptance.

```go
result, err := runtime.Decide(context.Background(), agentcontrol.DecisionAgentRequest{
    RequestedAgent: "RuntimeDeploymentAgent",
    ApplicationProfile: profile,
    ResourceRecommendation: recommendation,
})
if err != nil || result.AgentName != "RuntimeDeploymentAgent" {
    t.Fatalf("result = %#v, err = %v", result, err)
}
```

- [ ] **Step 2: Verify bridge tests fail**

Run: `go test ./internal/api -run 'TestDecisionAgentRuntime|TestInternalDecisionAgentExecutor|TestAgentExecutionResultAllowsDecisionDomain' -count=1`

Expected: FAIL because the bridge and internal decision executor do not exist.

- [ ] **Step 3: Implement the Internal Decision Executor**

Decode `application_profile` and `resource_recommendation` from `AgentDispatchRequest.Input`, call the shared rule decision function, and return:

```json
{
  "status": "completed",
  "proposal": {
    "action": "generate_deployment_decision",
    "parameters": {
      "decision": "DEPLOY",
      "selected_candidate_id": "candidate-gpu-01",
      "reason": "...",
      "confidence": 0.9
    }
  },
  "domain_validation": "deployment_decision"
}
```

- [ ] **Step 4: Implement the Registry and Dispatcher bridge**

Resolve the requested/default Agent, run `validateAgentExecutionRequest`, call `agentDispatcher.Dispatch`, run `validateAgentExecutionResult`, and map proposal parameters into `agentcontrol.DecisionAgentResult`. Sanitize endpoint errors and preserve Agent source and latency.

- [ ] **Step 5: Extend generic Result Guard for deployment decisions**

Permit `domain_validation="deployment_decision"` only when the dispatched action is `generate_deployment_decision`; keep all existing Manifest Guard checks unchanged.

- [ ] **Step 6: Inject the bridge into the service**

Construct one shared Runtime Agent store, Dispatcher, decision runtime, `agentcontrol.Service`, and AutomationRunner in dependency order. Register the Internal Decision Executor under the Registry default Agent name while keeping the existing Manifest executor behavior available.

- [ ] **Step 7: Run focused and API tests, then commit**

Run: `go test ./internal/api ./internal/agentcontrol -count=1`

Commit: `feat: dispatch selected deployment decision agents`

---

### Task 4: One-Shot API, UI Selection, and Experiment Evidence

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/automation_runner.go`
- Modify: `go/service-control-api/internal/agentcontrol/automation_runner_test.go`
- Modify: `go/service-control-api/internal/api/agent_control_api_test.go`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/automation_run_browser_test.js`

**Interfaces:**
- Consumes: `AutomationRunInput.decision_agent`, `GET /api/v1/agents`, and selected-Agent evidence in `Flow`.
- Produces: dropdown-driven one-shot run and experiment result rendered from the same run and flow.

- [ ] **Step 1: Write failing runner and API tests**

Assert that `decision_agent` reaches `ReceiveResourceRecommendationForAgent`, appears in the final Flow evidence, and errors are stored as `AGENT_AUTHORIZATION_REJECTED`, `AGENT_EXECUTION_FAILED`, or `AGENT_RESULT_REJECTED` without Adapter submission.

- [ ] **Step 2: Verify runner and API tests fail**

Run: `go test ./internal/agentcontrol ./internal/api -run 'TestAutomationRunner.*DecisionAgent|TestAutomationRunAPI.*DecisionAgent' -count=1`

Expected: FAIL because the selected Agent is not passed through the one-shot runner.

- [ ] **Step 3: Connect AutomationRunner to the selected Agent**

Pass `run.RunID` and `input.DecisionAgent` into the selected-Agent resource-recommendation method. Preserve the same `run_id`, `correlation_id`, and `trace_id` in Agent execution evidence and stored AutomationRun.

- [ ] **Step 4: Write failing web source and browser tests**

Require a `decision-agent-select`, eligible-Agent filtering, `decision_agent` in the POST body, selected Agent and source in the result, and the same evidence in the experiment detail.

- [ ] **Step 5: Verify web tests fail**

Run: `go test ./internal/webui -count=1`

Run: `node ./internal/webui/automation_run_browser_test.js`

Expected: FAIL because the dropdown and selected-Agent evidence are absent.

- [ ] **Step 6: Implement the main-screen Agent selector**

Rename the main title to `AI 응용 배포 자동화`, add the `배포 판단 Agent` select, load only `eligible_decision_agents`, mark Internal/Runtime source in each option, and refresh it after Registry registration or deletion.

- [ ] **Step 7: Connect result and experiment rendering**

Show Agent name, source, dispatch status, latency, Request Guard, Result Guard, domain Guard, decision, DesiredDeploymentSpec, and Adapter evidence. Keep post-deployment Feedback and scaling evidence optional and attached to the same correlation ID.

- [ ] **Step 8: Run web and API tests, then commit**

Run: `go test ./internal/webui ./internal/api ./internal/agentcontrol -count=1`

Run: `node ./internal/webui/automation_run_browser_test.js`

Commit: `feat: connect decision agents to automation web`

---

### Task 5: Documentation, Full Verification, and Browser QA

**Files:**
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`

**Interfaces:**
- Consumes: completed API and UI behavior.
- Produces: reproducible operator instructions and verified contract evidence.

- [ ] **Step 1: Write failing OpenAPI contract assertions**

Require `decision_agent` on `AutomationRunInput` and selected-Agent execution evidence on `Flow`.

- [ ] **Step 2: Verify OpenAPI test fails**

Run: `go test ./internal/api -run TestOpenAPI -count=1`

Expected: FAIL because the OpenAPI contract lacks the new fields.

- [ ] **Step 3: Update OpenAPI and Korean run instructions**

Document Internal versus Runtime Agent behavior, exact eligibility strings, Runtime endpoint response JSON, no-fallback behavior, Guard boundaries, process-memory persistence, and the Mock/Handoff Adapter evidence boundary.

- [ ] **Step 4: Run formatter, tests, vet, and build**

Run: `gofmt -w` on changed Go files.

Run: `go test ./... -count=1`

Run: `go vet ./...`

Run: `go build ./...`

- [ ] **Step 5: Run browser QA on port 18080**

Start the service with `PORT=18080`, `AIOPS_DEPLOYMENT_ADAPTER=mock`, and the existing local configuration. Verify at desktop and mobile widths that the Agent selector is populated, a default Internal run completes, a Runtime mock endpoint run is recorded, invalid Runtime output is rejected, and experiment evidence matches the same run.

- [ ] **Step 6: Commit documentation and verification fixes**

Commit: `docs: explain decision agent execution flow`
