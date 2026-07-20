# AppDeploy Planner Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an LLM-based Planner that creates a contract-compatible AppDeploy DeploymentManifest, validates it with deterministic Go rules, submits it to AppDeploy, and tracks deployment status and retryability.

**Architecture:** Add an `internal/appdeploy` contract/client package and an `internal/deploymentplanner` orchestration package that reuse the existing provider-neutral `llmclient`. The Echo API and CLI load the selected LLM candidate, invoke the Planner, validate trusted fields in Go, call AppDeploy, poll status, and collect logs without selecting the target VM themselves.

**Tech Stack:** Go 1.25, Echo, viper, zerolog, standard-library HTTP/JSON, OpenAI-compatible LLM endpoint, AppDeploy OpenAPI v1.

## Global Constraints

- Preserve existing APIs for compatibility; add the AppDeploy Planner as the primary new flow.
- Do not add Kubernetes or container deployment logic.
- Never commit credentials, tokens, private keys, kubeconfig, or real endpoint secrets.
- Do not automatically resubmit a deployment without an explicit idempotency contract.
- Run gofmt, `make test`, `make vet`, `make lint`, and `make swag`.

---

### Task 1: AppDeploy Contract Models and Manifest Guard

**Files:**
- Create: `go/service-control-api/internal/appdeploy/models.go`
- Create: `go/service-control-api/internal/appdeploy/manifest.go`
- Test: `go/service-control-api/internal/appdeploy/manifest_test.go`
- Create: `contracts/appdeploy/deployment_manifest.schema.json`

**Interfaces:**
- Produces: `DeploymentManifest`, `DeploymentResponse`, `DeploymentLog`, `APIError`, `ValidateManifest(DeploymentManifest, ManifestConstraints) error`.

- [ ] Write table-driven failing tests for valid manifests, invalid constants, missing resources, target-hint substitution, accelerator/GPU mismatch, and secret-like parameters.
- [ ] Run `go test ./internal/appdeploy -run Manifest -count=1` and verify the package or functions are missing.
- [ ] Add typed contract models and minimal deterministic validation matching the pinned AppDeploy schema.
- [ ] Re-run the focused tests and verify they pass.

### Task 2: LLM Deployment Manifest Generator

**Files:**
- Create: `go/service-control-api/internal/deploymentplanner/generator.go`
- Test: `go/service-control-api/internal/deploymentplanner/generator_test.go`

**Interfaces:**
- Consumes: `llmclient.Candidate`, `llmclient.Client`, AppDeploy contract models.
- Produces: `Generator.Generate(context.Context, llmclient.Candidate, GenerateInput) (GenerateResult, error)`.

- [ ] Write failing tests for a valid provider response, strict unknown-field rejection, app version substitution rejection, and malformed JSON failure.
- [ ] Run `go test ./internal/deploymentplanner -run Generator -count=1` and verify the missing implementation failure.
- [ ] Implement a strict JSON prompt/parser and trusted-field overlay for app version, target hint, requester, and parameters.
- [ ] Re-run the focused tests and verify they pass.

### Task 3: AppDeploy HTTP Client

**Files:**
- Create: `go/service-control-api/internal/appdeploy/client.go`
- Test: `go/service-control-api/internal/appdeploy/client_test.go`

**Interfaces:**
- Produces: `Client.CreateDeployment`, `Client.GetDeployment`, `Client.GetDeploymentLogs`.

- [ ] Write failing `httptest.Server` tests that assert the exact POST body, status path, logs path, context cancellation, response limit, and `retryable` error decoding.
- [ ] Run `go test ./internal/appdeploy -run Client -count=1` and verify the missing client failure.
- [ ] Implement the minimal HTTP client using relative AppDeploy `/deployments` paths.
- [ ] Re-run the focused tests and verify they pass.

### Task 4: Planner Orchestration and Polling

**Files:**
- Create: `go/service-control-api/internal/deploymentplanner/planner.go`
- Test: `go/service-control-api/internal/deploymentplanner/planner_test.go`

**Interfaces:**
- Consumes: manifest generator and AppDeploy client interfaces.
- Produces: `Planner.PlanAndDeploy(context.Context, Request) (Response, error)`.

- [ ] Write failing tests for RUNNING completion, terminal failure with logs, poll exhaustion, missing AppDeploy base URL, and explicit retry recommendation.
- [ ] Run `go test ./internal/deploymentplanner -run Planner -count=1` and verify expected failures.
- [ ] Implement generation, guard validation, create request, bounded polling, log retrieval, and conservative retry recommendation.
- [ ] Re-run the focused tests and verify they pass.

### Task 5: Echo API and CLI Wiring

**Files:**
- Modify: `go/service-control-api/internal/api/config.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Test: `go/service-control-api/internal/api/server_test.go`
- Modify: `go/service-control-api/cmd/aiops-service-control/main.go`
- Test: `go/service-control-api/cmd/aiops-service-control/main_test.go`

**Interfaces:**
- Produces REST `POST /api/v1/planner/deployments` and CLI `run-appdeploy-planner`.

- [ ] Write failing endpoint and CLI tests using fake LLM and AppDeploy HTTP servers.
- [ ] Run focused API/CLI tests and verify route/command failures.
- [ ] Add viper configuration, service delegation, Swagger annotation, route, and command flags.
- [ ] Re-run focused API/CLI tests and verify success and user-facing errors.

### Task 6: Contracts, Examples, and Documentation

**Files:**
- Modify: `docs/design/main_llm_go_guard_control_flow.md`
- Modify: `docs/deliverables/02_agent_registration_management_prototype.md`
- Modify: `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md`
- Modify: `docs/submission/functional_api_guide.md`
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `README.md`
- Create: `examples/requests/run-appdeploy-planner.json`
- Create: `examples/responses/run-appdeploy-planner-success.json`

**Interfaces:**
- Documents the exact AppDeploy contract and distinguishes Planner from Target selection and execution.

- [ ] Update the A-flow document to use the real DeploymentManifest rather than a generic Action proposal.
- [ ] Add API and CLI examples with placeholders only.
- [ ] Update formal deliverables and README without claiming production deployment completion.
- [ ] Run `make swag` and validate generated JSON/YAML.

### Task 7: Full Verification

**Files:**
- Review all modified files.

- [ ] Run gofmt on all changed Go files.
- [ ] Run `make test` and require all modules to pass.
- [ ] Run `make vet` and require both modules to pass.
- [ ] Run `make lint` and require zero issues when the tool is available.
- [ ] Run `make swag` and parse generated OpenAPI JSON/YAML.
- [ ] Run a credential-pattern scan and confirm no sensitive values are present.
- [ ] Run `git diff --check` and review the final scope.
