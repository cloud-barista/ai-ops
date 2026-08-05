# Automatic Three-Stage Agent Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one natural-language request or structured App Spec automatically run requirement analysis, mock infrastructure recommendation, and the existing guarded AI application automation Agent.

**Architecture:** Add focused analyzer, recommender, and orchestration units under `internal/agentcontrol`. The orchestrator generates Common JSON v1.0 inputs and delegates authorization, `DEPLOY/REJECT/RETRY`, and Guard checks to the existing `agentcontrol.Service`. A single Echo endpoint and one primary web command expose the flow while legacy raw JSON endpoints remain available.

**Tech Stack:** Go 1.22+, Echo, existing `llmclient`, vanilla HTML/CSS/JavaScript, Swagger/OpenAPI, Go tests, Node syntax checks.

## Global Constraints

- Keep the implementation on the existing `geon` branch.
- Do not modify the AppDeployer checkout or invoke AppDeploy.
- Do not provision, delete, or control a real VM.
- Keep `DesiredDeploymentSpec` free of credentials, actual VM IDs, AppDeploy Target IDs, and provider-specific commands.
- Preserve existing Common JSON v1.0 Agent Control endpoints.
- Record whether requirement analysis used `structured`, `qwen`, or `local_rule`.
- Never report local-rule fallback as Qwen execution.
- Use Go + Echo, `context.Context`, stable error responses, Swagger comments, generated OpenAPI, `gofmt`, `make test`, and `make vet`.

---

### Task 1: Explicit Desired Deployment Spec

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/models.go`
- Modify: `go/service-control-api/internal/agentcontrol/service.go`
- Modify: `go/service-control-api/internal/agentcontrol/service_test.go`

**Interfaces:**
- Produces: `agentcontrol.DesiredDeploymentSpec`
- Produces: `Flow.DesiredDeploymentSpec *DesiredDeploymentSpec`
- Preserves: `Flow.DeploymentRequest` for Common JSON compatibility

- [ ] **Step 1: Write failing tests**

Add assertions that an approved flow contains `DesiredDeploymentSpec`, that it
does not expose an actual VM ID, and that the compatibility
`DeploymentManifest` is derived from the same resource and inference fields.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
cd go/service-control-api
go test ./internal/agentcontrol -run 'Test.*DesiredDeploymentSpec' -count=1
```

Expected: compile or assertion failure because the explicit model does not
exist.

- [ ] **Step 3: Implement the explicit model**

Create:

```go
type DesiredDeploymentSpec struct {
    SpecVersion            string                 `json:"spec_version"`
    DecisionID             string                 `json:"decision_id"`
    Application            ManifestApplication    `json:"application"`
    TargetRuntime          string                 `json:"target_runtime"`
    DesiredInfrastructure  DesiredInfrastructure  `json:"desired_infrastructure"`
    InferenceConfiguration InferenceConfiguration `json:"inference_configuration"`
    Runtime                RuntimeConfiguration   `json:"runtime,omitempty"`
    PolicyHints            []string               `json:"policy_hints,omitempty"`
    Metadata               ManifestMetadata       `json:"metadata"`
}
```

Populate it before the compatibility deployment request, then build the
compatibility Manifest from the spec.

- [ ] **Step 4: Run focused tests**

```bash
go test ./internal/agentcontrol -count=1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/service-control-api/internal/agentcontrol
git commit -m "feat: define platform-neutral desired deployment spec"
```

### Task 2: Requirement Analyzer

**Files:**
- Create: `go/service-control-api/internal/agentcontrol/requirement_analyzer.go`
- Create: `go/service-control-api/internal/agentcontrol/requirement_analyzer_test.go`
- Modify: `go/service-control-api/internal/agentcontrol/models.go`

**Interfaces:**
- Produces: `RequirementAnalyzer.Analyze(context.Context, AutomationRunInput) (RequirementAnalysisResult, error)`
- Produces: `ApplicationProfile`, `AnalysisMode`, assumptions, and source request
- Consumes later: Task 4 `AutomationRunner`

- [ ] **Step 1: Write table-driven failing tests**

Cover:

```text
natural Korean GPU request -> CPU 4, memory 16384 MiB, GPU 1, storage 20 GiB
natural English CPU request -> accelerator not required
structured App Spec -> exact deterministic mapping
empty natural request -> validation error
unsupported input_type -> validation error
missing optional values -> documented conservative assumptions
```

- [ ] **Step 2: Verify failure**

```bash
go test ./internal/agentcontrol -run 'TestLocalRequirementAnalyzer' -count=1
```

Expected: compile failure because the analyzer is not defined.

- [ ] **Step 3: Implement the bounded local analyzer**

Implement:

```go
type RequirementAnalyzer interface {
    Analyze(context.Context, AutomationRunInput) (RequirementAnalysisResult, error)
}

type LocalRequirementAnalyzer struct{}
```

Natural-language parsing accepts explicit Korean and English CPU, memory, GPU,
storage, and replica expressions. Missing values use conservative defaults and
record each default in `assumptions`. It must reject secret-like fields and
must not create VM IDs or commands.

- [ ] **Step 4: Run focused tests**

```bash
go test ./internal/agentcontrol -run 'TestLocalRequirementAnalyzer' -count=1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/service-control-api/internal/agentcontrol
git commit -m "feat: analyze one-shot application requirements"
```

### Task 3: Mock Resource Recommender

**Files:**
- Create: `config/mock_resource_catalog.json`
- Create: `go/service-control-api/internal/agentcontrol/resource_recommender.go`
- Create: `go/service-control-api/internal/agentcontrol/resource_recommender_test.go`
- Modify: `go/service-control-api/internal/api/config.go`

**Interfaces:**
- Produces: `ResourceRecommender.Recommend(context.Context, ApplicationProfile) (RecommendationResult, error)`
- Configuration: `ServerConfig.ResourceCatalogPath`
- Environment: `AIOPS_RESOURCE_CATALOG_PATH`

- [ ] **Step 1: Write failing tests**

Cover:

```text
GPU profile -> feasible L4 mock candidate
CPU profile -> lower-cost CPU mock candidate
GPU memory above every candidate -> closest candidate selected with feasible=false
invalid catalog -> stable error
rank ordering -> total score descending
```

- [ ] **Step 2: Verify failure**

```bash
go test ./internal/agentcontrol -run 'TestCatalogResourceRecommender' -count=1
```

Expected: compile failure because the recommender is not defined.

- [ ] **Step 3: Implement catalog loading, filtering, and ranking**

Define a versioned catalog containing platform-neutral mock candidates such as
`mock-cpu-balanced`, `mock-gpu-l4`, and `mock-gpu-high-memory`. Filter by
declared requirements, calculate bounded scores from 0 to 1, sort by total
score. When no candidate is feasible, select the closest candidate with
`feasible=false` and explicit rejection reasons so the existing Agent produces
a traceable `RETRY` correction request.

- [ ] **Step 4: Run focused tests**

```bash
go test ./internal/agentcontrol -run 'TestCatalogResourceRecommender' -count=1
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/mock_resource_catalog.json go/service-control-api/internal/agentcontrol go/service-control-api/internal/api/config.go
git commit -m "feat: recommend mock infrastructure from application profiles"
```

### Task 4: One-Shot Automation Runner and API

**Files:**
- Create: `go/service-control-api/internal/agentcontrol/automation_runner.go`
- Create: `go/service-control-api/internal/agentcontrol/automation_runner_test.go`
- Modify: `go/service-control-api/internal/api/service.go`
- Modify: `go/service-control-api/internal/api/agent_control_api.go`
- Modify: `go/service-control-api/internal/api/agent_control_api_test.go`
- Modify: `go/service-control-api/internal/api/server.go`

**Interfaces:**
- Produces: `POST /api/v1/agent-control/automation-runs`
- Produces: `GET /api/v1/agent-control/automation-runs/:run_id`
- Produces: `AutomationRun` with per-stage evidence and final Flow
- Consumes: `RequirementAnalyzer`, `ResourceRecommender`, existing `agentcontrol.Service`

- [ ] **Step 1: Write failing runner and HTTP tests**

Assert:

```text
one natural request completes all stages and returns DEPLOY
one structured input completes all stages
invalid profile returns REJECT evidence
no feasible candidate returns RETRY
all generated envelopes share correlation_id and trace_id
Agent Registry denial prevents the Agent decision
GET returns the stored run
```

- [ ] **Step 2: Verify failure**

```bash
go test ./internal/agentcontrol ./internal/api -run 'Test.*AutomationRun' -count=1
```

Expected: compile or route-not-found failure.

- [ ] **Step 3: Implement orchestration**

`AutomationRunner.Run` shall:

1. validate the single input
2. create one `run_id`, `correlation_id`, and `trace_id`
3. analyze requirements
4. recommend resources
5. create Common JSON envelopes
6. call `ReceiveApplicationContext`
7. call `ReceiveResourceRecommendation`
8. store the final run
9. return all stage evidence

Use an in-memory mutex-protected store consistent with the existing PoC.

- [ ] **Step 4: Add Echo handlers and Swagger comments**

Return HTTP 201 for a created run, HTTP 400 for malformed input, and HTTP 404
for an unknown run. Domain `REJECT` and `RETRY` outcomes remain successful run
responses rather than HTTP errors.

- [ ] **Step 5: Run focused and full Go tests**

```bash
go test ./internal/agentcontrol ./internal/api -count=1
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add go/service-control-api/internal/agentcontrol go/service-control-api/internal/api
git commit -m "feat: run three-stage automation from one request"
```

### Task 5: User-Facing One-Command Web Flow

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Create: `go/service-control-api/internal/webui/automation_run_browser_test.js`

**Interfaces:**
- Consumes: `POST /api/v1/agent-control/automation-runs`
- Preserves: advanced Common JSON input controls
- Produces: natural-language and structured input modes

- [ ] **Step 1: Write failing HTML and JavaScript contract tests**

Require:

```text
natural-language mode is the default
structured mode is selectable
one submit button calls only automation-runs
three stage labels exist in order
generated ApplicationProfile and ResourceRecommendation are advanced evidence
raw Common JSON editors are in an advanced section
```

- [ ] **Step 2: Verify failure**

```bash
go test ./internal/webui -count=1
node --test internal/webui/automation_run_browser_test.js
```

Expected: missing controls and client method failures.

- [ ] **Step 3: Implement the primary form and result rendering**

Use a compact segmented control for input mode, one request textarea or
structured form, one `자동 분석 및 판단` button, three stable stage indicators,
and the existing decision summary. Show analyzer mode, selected mock candidate,
Registry authorization, Guard status, and Desired Deployment Spec.

- [ ] **Step 4: Run UI tests and syntax checks**

```bash
go test ./internal/webui -count=1
node --check internal/webui/static/app.js
node --test internal/webui/automation_run_browser_test.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/service-control-api/internal/webui
git commit -m "feat: automate three-stage flow from the web"
```

### Task 6: Contracts, Documentation, and End-to-End Verification

**Files:**
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `go/service-control-api/docs/swagger/docs.go`
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`
- Modify: `docs/evidence/agent_control_mvp_20260729.md`

**Interfaces:**
- Documents: one-shot input and response contracts
- Verifies: API, web, compatibility, desktop, and mobile behavior

- [ ] **Step 1: Regenerate Swagger and update contract tests**

```bash
make swag
```

Ensure both automation-run routes and their schemas are present.

- [ ] **Step 2: Update run guides and evidence**

Document the one-command flow, local mock catalog boundary, analyzer mode
labels, explicit non-execution boundary, and curl examples for natural and
structured requests.

- [ ] **Step 3: Run repository verification**

```bash
make test
make vet
```

Expected: PASS.

- [ ] **Step 4: Browser verification**

Verify at desktop and 390 px mobile widths:

```text
no horizontal document overflow
one natural-language submission completes all three stages
one structured submission completes all three stages
DEPLOY, REJECT, and RETRY evidence render correctly
advanced JSON controls remain available
no browser console errors
```

- [ ] **Step 5: Verify repository hygiene**

```bash
git diff --check
git status --short --branch
```

Do not stage or modify the existing untracked
`config/inference_optimization.json`.

- [ ] **Step 6: Commit**

```bash
git add README.md go/service-control-api/README.md docs/submission/openapi_service_control.yaml go/service-control-api/docs/swagger docs/evidence/agent_control_mvp_20260729.md
git commit -m "docs: document automatic three-stage agent flow"
```
