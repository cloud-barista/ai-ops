# Application Analysis Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the exact Common JSON v1.0 analysis request endpoint and replay-safe geon execution.

**Architecture:** Add protocol request models and a normalization boundary in
`internal/agentcontrol`. Extend `AutomationRunner` with an idempotent protocol
entry point while reusing its existing pipeline. Expose the entry point through
Echo and document it in both maintained OpenAPI documents.

**Tech Stack:** Go, Echo, validator, YAML OpenAPI, Go tests.

## Global Constraints

- Modify geon only; do not modify AppDeploy.
- Preserve existing automation and raw Common JSON endpoints.
- Use `message_id` for inbound protocol replay detection.
- Preserve `correlation_id` and `trace_id`.
- Keep downstream deployment execution ownership outside geon.

---

### Task 1: Protocol models and normalization

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/models.go`
- Test: `go/service-control-api/internal/agentcontrol/automation_runner_test.go`

**Interfaces:**
- Consumes: Common JSON v1.0 `application.analysis.request`
- Produces: `ApplicationAnalysisRequestEnvelope` and normalized `AutomationRunInput`

- [ ] Add a failing test that constructs the protocol payload and expects the
  analyzer output to preserve application identity, artifact, and declared RPS.
- [ ] Run the focused test and confirm the missing types or behavior fail.
- [ ] Add the protocol model and mapping fields.
- [ ] Run the focused test and confirm it passes.

### Task 2: Replay-safe runner

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/automation_runner.go`
- Test: `go/service-control-api/internal/agentcontrol/automation_runner_test.go`

**Interfaces:**
- Consumes: `ApplicationAnalysisRequestEnvelope`
- Produces: `RunAnalysisRequest(ctx, request) (AutomationRun, bool, error)`

- [ ] Add a failing test that submits the same `message_id` twice and expects
  one stable `run_id`, `correlation_id`, `trace_id`, causation chain, and
  deployment `request_id`.
- [ ] Run the focused test and confirm the method is missing.
- [ ] Implement request validation, normalization, preserved identifiers, and
  the request-to-run replay index.
- [ ] Run all `internal/agentcontrol` tests.

### Task 3: HTTP endpoint

**Files:**
- Modify: `go/service-control-api/internal/api/agent_control_api.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Test: `go/service-control-api/internal/api/agent_control_api_test.go`

**Interfaces:**
- Consumes: `POST /api/v1/agent-control/application-analysis-requests`
- Produces: `201` on first execution and `200` plus
  `Idempotent-Replayed: true` on replay

- [ ] Add failing API tests for first execution, replay, and malformed input.
- [ ] Register and implement the handler.
- [ ] Run focused API tests.

### Task 4: API documentation and verification

**Files:**
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: generated Swagger files under `go/service-control-api/docs/swagger`
- Test: `go/service-control-api/internal/api/openapi_contract_test.go`

**Interfaces:**
- Produces: documented request, response, replay semantics, and schemas

- [ ] Extend the OpenAPI contract test and confirm it fails.
- [ ] Update submission OpenAPI and regenerate Swagger.
- [ ] Run `go test ./...`, `go vet ./...`, and `go build ./...`.

