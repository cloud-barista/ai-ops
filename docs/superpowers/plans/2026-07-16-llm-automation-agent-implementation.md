# LLM Automation Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect an actual OpenAI-compatible LLM response to the AI application automation agent, then validate the proposed Action with actual VM evidence, Agent Registry capabilities, and Go Guard rules before producing a non-executing external-agent handoff.

**Architecture:** A shared `llmclient` package performs provider-neutral chat completion calls. An `automation` package builds a bounded service-control prompt and parses a strict Action proposal. The existing API service remains the policy authority: VM suitability is evaluated before the LLM call, and target VM identity, allowed Action, capability, registered executor, and guard decision are verified in Go after the LLM response.

**Tech Stack:** Go 1.25, Echo v4, `net/http`, `encoding/json`, zerolog, viper, go-playground/validator, Swagger/OpenAPI.

## Global Constraints

- Preserve the Go + Echo implementation and existing repository structure.
- Do not hard-code AppDeployer or another external execution framework name.
- Do not expose credentials, API keys, VM access secrets, or raw provider errors in API responses.
- Do not add Kubernetes or container deployment behavior.
- Do not execute an external deployment inside this service-control prototype.
- An LLM failure must not produce a deterministic success fallback.
- VM facts and bounded Action authorization remain deterministic Go checks.
- Apply `gofmt`; finish with `make test`, `make vet`, and `make lint`.

---

### Task 1: Shared OpenAI-Compatible LLM Client

**Files:**
- Create: `go/service-control-api/internal/llmclient/client.go`
- Create: `go/service-control-api/internal/llmclient/client_test.go`
- Modify: `go/service-control-api/internal/benchmark/benchmark.go`
- Test: `go/service-control-api/internal/benchmark/benchmark_test.go`

**Interfaces:**
- Produces: `llmclient.Candidate`, `llmclient.CandidateConfig`, `llmclient.Completion`, `llmclient.Client.Complete(ctx, candidate, systemPrompt, userPrompt)`.
- Produces: `llmclient.LoadCandidateConfig(path)` and `llmclient.FindEnabledCandidate(config, candidateID)`.
- Consumes later: automation planner and benchmark runner.

- [ ] **Step 1: Write failing client tests**

```go
func TestClientCompleteCallsOpenAICompatibleEndpoint(t *testing.T) {
    server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        w.Header().Set("content-type", "application/json")
        _, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"action\":\"observe_status\"}"}}]}`))
    }))
    defer server.Close()

    result, err := NewClient(nil).Complete(context.Background(), Candidate{
        CandidateID: "test", ActualModel: "test-model", Endpoint: server.URL, Enabled: true,
    }, "system", "user")
    if err != nil || result.Content != `{"action":"observe_status"}` || result.Status != "executed" {
        t.Fatalf("unexpected completion: %#v, %v", result, err)
    }
}

func TestClientCompleteDoesNotFallbackWhenEndpointFails(t *testing.T) {
    result, err := NewClient(nil).Complete(context.Background(), Candidate{
        CandidateID: "test", ActualModel: "test-model", Endpoint: "http://127.0.0.1:1", Enabled: true,
        TimeoutSeconds: 1,
    }, "system", "user")
    if err == nil || result.Status != "not_executed" {
        t.Fatalf("expected provider failure without fallback: %#v, %v", result, err)
    }
}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd go/service-control-api
go test ./internal/llmclient -count=1
```

Expected: FAIL because the `llmclient` package and symbols do not exist.

- [ ] **Step 3: Implement the shared client**

Implement these public types and signatures:

```go
type Candidate struct {
    CandidateID string `json:"candidate_id"`
    RoleLabel string `json:"role_label"`
    Provider string `json:"provider"`
    ActualModel string `json:"actual_model"`
    APIKeyEnv string `json:"api_key_env"`
    Endpoint string `json:"endpoint"`
    Enabled bool `json:"enabled"`
    TimeoutSeconds int `json:"timeout_seconds"`
}

type CandidateConfig struct {
    Version string `json:"version"`
    BenchmarkStatus string `json:"benchmark_status"`
    Candidates []Candidate `json:"candidates"`
}

type Completion struct {
    Status string
    Content string
    LatencyMS int64
    Provider string
    ActualModel string
    CandidateID string
}

type Client struct { HTTPClient *http.Client }

func NewClient(httpClient *http.Client) Client
func (client Client) Complete(ctx context.Context, candidate Candidate, systemPrompt string, userPrompt string) (Completion, error)
func LoadCandidateConfig(path string) (CandidateConfig, error)
func FindEnabledCandidate(config CandidateConfig, candidateID string) (Candidate, error)
```

Use `http.NewRequestWithContext`, temperature `0`, environment-variable API key lookup, bounded timeout, 2xx status validation, and OpenAI-compatible `choices[0].message.content` extraction.

- [ ] **Step 4: Reuse the client from the benchmark runner**

Replace the benchmark-local provider HTTP call with `llmclient.Client.Complete`. Keep benchmark output fields and status semantics unchanged.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
go test ./internal/llmclient ./internal/benchmark -count=1
```

Expected: PASS.

### Task 2: LLM Action Planner and Strict Proposal Parser

**Files:**
- Create: `go/service-control-api/internal/automation/planner.go`
- Create: `go/service-control-api/internal/automation/planner_test.go`

**Interfaces:**
- Consumes: `llmclient.Client`, `llmclient.Candidate`.
- Produces: `automation.DecisionContext`, `automation.ActionProposal`, `automation.DecisionResult`, `automation.Planner.Plan`.

- [ ] **Step 1: Write failing planner tests**

Cover these behaviors with an `httptest.Server`:

```go
func TestPlannerReturnsExecutedStructuredProposal(t *testing.T)
func TestPlannerRejectsMalformedJSON(t *testing.T)
func TestPlannerRejectsConfidenceOutsideZeroToOne(t *testing.T)
func TestPlannerRejectsTargetVMSubstitution(t *testing.T)
```

The valid response must contain:

```json
{
  "action": "observe_status",
  "reason": "Performance evidence is not measured.",
  "confidence": 0.82,
  "required_capability": "ai_application_deployment_control",
  "target_vm_id": "recorded-vm-id",
  "parameters": {"next_check":"measure_workload_performance"}
}
```

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./internal/automation -count=1
```

Expected: FAIL because planner types do not exist.

- [ ] **Step 3: Implement planner types and validation**

```go
type DecisionContext struct {
    Workload string `json:"workload"`
    ServiceName string `json:"service_name"`
    TargetVMID string `json:"target_vm_id"`
    CompatibilityStatus string `json:"compatibility_status"`
    Checks []map[string]string `json:"checks"`
    Observations map[string]any `json:"observations,omitempty"`
    AllowedActions []string `json:"allowed_actions"`
    RequiredCapability string `json:"required_capability"`
}

type ActionProposal struct {
    Action string `json:"action"`
    Reason string `json:"reason"`
    Confidence float64 `json:"confidence"`
    RequiredCapability string `json:"required_capability"`
    TargetVMID string `json:"target_vm_id"`
    Parameters map[string]any `json:"parameters,omitempty"`
}

type DecisionResult struct {
    ExecutionStatus string `json:"decision_execution_status"`
    CandidateID string `json:"candidate_id"`
    Provider string `json:"provider"`
    ActualModel string `json:"actual_model"`
    LatencyMS int64 `json:"latency_ms"`
    Proposal ActionProposal `json:"proposal"`
}
```

`Planner.Plan` must generate a JSON-only prompt, call the shared LLM client, strip optional markdown fences, unmarshal exactly one object, reject missing required fields, reject invalid confidence, and reject a changed target VM ID.

- [ ] **Step 4: Run tests and verify GREEN**

```bash
go test ./internal/automation -count=1
```

Expected: PASS.

### Task 3: Go Policy Validation and Capability-Based Handoff

**Files:**
- Modify: `go/service-control-api/internal/api/models.go`
- Modify: `go/service-control-api/internal/api/config.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/api/service_test.go`

**Interfaces:**
- Consumes: `automation.Planner.Plan`, existing VM suitability and runtime Agent Registry.
- Produces: `Service.PlanLLMAutomationActionFromPaths` and public response models.

- [ ] **Step 1: Write failing service tests**

Add tests for:

```go
func TestPlanLLMAutomationActionApprovesAllowedAction(t *testing.T)
func TestPlanLLMAutomationActionRejectsUnboundedAction(t *testing.T)
func TestPlanLLMAutomationActionSkipsLLMForIncompatibleVM(t *testing.T)
func TestPlanLLMAutomationActionReturnsPendingWhenExecutorMissing(t *testing.T)
func TestPlanLLMAutomationActionDoesNotFallbackAfterProviderFailure(t *testing.T)
```

Use temporary workload and candidate configs plus an `httptest.Server`; do not require a real network provider in unit tests.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./internal/api -run PlanLLMAutomationAction -count=1
```

Expected: FAIL because the service method and models do not exist.

- [ ] **Step 3: Add API models**

```go
type LLMAutomationActionRequest struct {
    Workload string `json:"workload" validate:"required"`
    TargetVM VMResourceSnapshot `json:"target_vm" validate:"required"`
    CandidateID string `json:"candidate_id" validate:"required"`
    Observations map[string]any `json:"observations,omitempty"`
}

type GuardDecision struct {
    Valid bool `json:"valid"`
    Status string `json:"status"`
    Reason string `json:"reason"`
}

type LLMAutomationActionResponse struct {
    Valid bool `json:"valid"`
    Status string `json:"status"`
    CorrelationID string `json:"correlation_id"`
    VMCompatibility VMCompatibilityResponse `json:"vm_compatibility"`
    Decision automation.DecisionResult `json:"decision"`
    Guard GuardDecision `json:"guard"`
    Handoff AgentInvocationPlan `json:"handoff"`
}
```

Define API-facing `LLMActionProposal` and `LLMDecisionResult` DTOs in `internal/api/models.go` and map explicitly from the `automation` package in `service.go`. Do not expose internal package types directly through generated Swagger schemas.

Extend `ServerConfig` with `LLMCandidatesPath`. Read `AIOPS_LLM_CANDIDATES_PATH` through viper and default to `config/ops_llm_eval_candidates.json`. The REST API accepts only `candidate_id`; it must not accept an arbitrary filesystem path from the request body.

- [ ] **Step 4: Implement the service flow**

Implement:

```go
func (service Service) PlanLLMAutomationAction(ctx context.Context, request LLMAutomationActionRequest) (LLMAutomationActionResponse, error)

func (service Service) PlanLLMAutomationActionFromPaths(
    ctx context.Context,
    requirementsPath string,
    candidatesPath string,
    request LLMAutomationActionRequest,
) (LLMAutomationActionResponse, error)
```

Required order:

1. Validate actual VM suitability.
2. Return `vm_incompatible` before any LLM call when resource checks fail.
3. Load workload allowed Actions and the explicitly requested enabled candidate.
4. Call the LLM planner.
5. Reject an Action outside the workload allowed list.
6. Reject a capability different from `ai_application_deployment_control`.
7. Select a registered executor by capability and Action.
8. Return `pending_executor` when none is registered.
9. Generate a correlation ID and register it in the process-memory automation store.
10. Produce an approved non-executing handoff only when every check passes.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
go test ./internal/api -run 'PlanLLMAutomationAction|ValidateVMSuitability' -count=1
```

Expected: PASS.

### Task 4: REST API and CLI Entry Points

**Files:**
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/main.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/main_test.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/api_integration_validation.go`

**Interfaces:**
- Produces REST: `POST /api/v1/automation/action-proposals`.
- Produces CLI: `plan-llm-automation-action`.

- [ ] **Step 1: Write failing endpoint and CLI tests**

Endpoint test must assert a 200 approved response from a local fake LLM server and a sanitized 400 response for provider failure. CLI test must assert saved JSON contains `decision_execution_status=executed` and the actual model name.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./cmd/aiops-service-control ./internal/api -run 'LLMAutomation|AutomationAction' -count=1
```

Expected: FAIL because route and command are absent.

- [ ] **Step 3: Add REST route and handler**

Register:

```go
server.POST("/api/v1/automation/action-proposals", handler.RestPostLLMAutomationAction)
```

Use `bindAndValidate`, do not return raw provider error text, and add complete Swagger annotations.

- [ ] **Step 4: Add CLI command**

```bash
go run ./cmd/aiops-service-control plan-llm-automation-action \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --candidate-id local-ollama-ops-llm \
  --workload llm-chat-inference
```

The command must fail when no actual provider call succeeds.

- [ ] **Step 5: Extend API integration validation and run GREEN tests**

Add the new endpoint to the local integration harness with an in-process fake provider.

```bash
go test ./cmd/aiops-service-control ./internal/api -count=1
```

Expected: PASS.

### Task 5: Generic External Execution Feedback

**Files:**
- Modify: `go/service-control-api/internal/api/models.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Create: `go/service-control-api/internal/api/automation_feedback.go`
- Create: `go/service-control-api/internal/api/automation_feedback_test.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces REST: `POST /api/v1/automation/feedback`.
- Produces: process-memory feedback lookup keyed by correlation ID.
- Consumes: correlation IDs registered by `PlanLLMAutomationActionFromPaths`.

- [ ] **Step 1: Write failing feedback tests**

```go
func TestRecordAutomationFeedbackRequiresKnownCorrelationID(t *testing.T)
func TestRecordAutomationFeedbackStoresExecutionStatusAndMetrics(t *testing.T)
func TestAutomationFeedbackRejectsCredentialLikeFields(t *testing.T)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./internal/api -run AutomationFeedback -count=1
```

Expected: FAIL because feedback storage and endpoint do not exist.

- [ ] **Step 3: Implement feedback DTO and process-memory store**

```go
type AutomationFeedbackRequest struct {
    CorrelationID string `json:"correlation_id" validate:"required"`
    Executor string `json:"executor" validate:"required"`
    Status string `json:"status" validate:"required"`
    ExternalExecutionID string `json:"external_execution_id,omitempty"`
    LatencyMS *float64 `json:"latency_ms,omitempty"`
    ThroughputRPS *float64 `json:"throughput_rps,omitempty"`
    Message string `json:"message,omitempty"`
}
```

Accept only `accepted`, `running`, `succeeded`, `failed`, and `stopped`. Store no secrets and no arbitrary parameter map.

- [ ] **Step 4: Add route and run GREEN tests**

Register `POST /api/v1/automation/feedback`, validate input, and return the normalized stored record.

```bash
go test ./internal/api -run AutomationFeedback -count=1
```

Expected: PASS.

### Task 6: Service Validation Integration and Evidence Status

**Files:**
- Modify: `go/service-control-api/internal/api/models.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/api/service_test.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/validate_system.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/main.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/main_test.go`

**Interfaces:**
- Extends `validate-system` with `--run-llm-decision` and `--llm-decision-candidate-id`.
- Adds explicit `decision_execution_status` to integrated service-control output.

- [ ] **Step 1: Write failing integration tests**

Tests must prove:

- the LLM decision step is `skipped` unless explicitly enabled;
- enabled mode requires an actual endpoint response;
- `decision_execution_status=executed` appears only after a provider call;
- an endpoint failure makes the step invalid rather than falling back;
- local and VM targets use the same request contract.

- [ ] **Step 2: Run tests and verify RED**

```bash
go test ./cmd/aiops-service-control -run 'SystemValidation.*LLMDecision' -count=1
```

Expected: FAIL because flags and system-validation step are absent.

- [ ] **Step 3: Add optional system-validation step**

Add flags:

```text
--run-llm-decision
--llm-decision-candidates <path>
--llm-decision-candidate-id <id>
```

Save `07_llm_automation_action.json` for local target and after VM snapshot collection for VM target. Never label dry-run or skipped output as executed.

- [ ] **Step 4: Run GREEN tests**

```bash
go test ./cmd/aiops-service-control -count=1
```

Expected: PASS.

### Task 7: Contracts, Documentation, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `PACKAGE_MANIFEST.md`
- Modify: `config/agent_registry.json`
- Modify: `config/vm_workload_requirements.json`
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `docs/deliverables/02_agent_registration_management_prototype.md`
- Modify: `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md`
- Modify: `docs/design/agent_action_reward_policy.md`
- Modify: `docs/design/integration_boundary.md`
- Modify: `docs/submission/install_and_run_guide.md`
- Modify: `docs/submission/test_guide.md`
- Modify: `go/service-control-api/README.md`
- Create: `examples/requests/plan-llm-automation-action.json`
- Create: `examples/responses/plan-llm-automation-action-approved.json`
- Regenerate: `go/service-control-api/docs/swagger/swagger.json`
- Regenerate: `go/service-control-api/docs/swagger/swagger.yaml`
- Regenerate only if Markdown content changed: affected deliverable DOCX files.

**Interfaces:**
- Documents the actual LLM decision path separately from benchmark selection.
- Defines `AIApplicationAutomationAgent` as the internal LLM planning agent.

- [ ] **Step 1: Align registry and declared Actions**

Replace stale VM selection and scale-oriented internal Action claims with the first-year VM-only set:

```json
[
  "deploy_application",
  "observe_status",
  "restart_application",
  "stop_application"
]
```

Keep specialized HA, infrastructure, and cost agents outside the core automation path unless they are explicitly registered as optional reviewers.

- [ ] **Step 2: Update OpenAPI and examples**

Document action-proposal and feedback routes, request/response schemas, statuses, actual provider metadata, guard result, and handoff boundary.

- [ ] **Step 3: Update formal documents**

State explicitly:

- the current automation agent calls an actual LLM endpoint;
- the LLM proposes but does not bypass Go validation;
- external execution agents are dynamically registered;
- actual deployment completion requires feedback;
- `dry_run`, `benchmark executed`, and `decision executed` are different statuses.

- [ ] **Step 4: Regenerate Swagger and affected DOCX files**

```bash
make swag
```

Use Pandoc only for Markdown sources that changed and structurally inspect generated DOCX files. Do not create PDF artifacts.

- [ ] **Step 5: Run complete verification**

```bash
make test
make vet
make lint
git diff --check
```

Run a credential-pattern scan and verify no fixed external executor name appears in core code.

- [ ] **Step 6: Run local executed-decision validation**

With an actual OpenAI-compatible endpoint running:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-llm-decision \
  --llm-decision-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --llm-decision-candidate-id local-ollama-ops-llm \
  --output-dir ../../runs/full-validation-local-llm-agent
```

Expected: system summary is valid and the Action evidence records `decision_execution_status=executed`. If no endpoint is running, report the failed real-call attempt; do not generate a replacement success artifact.
