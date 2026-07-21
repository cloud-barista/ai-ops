# geon Generated Record Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add individual and bulk deletion for all geon-owned generated records while leaving external deployment resources untouched.

**Architecture:** Add narrow mutex-protected deletion/list operations to the autonomy Manager and Feedback store, expose authenticated REST routes, and wire the embedded vanilla JavaScript UI to those routes. Browser-only history remains in `localStorage` and receives stable local IDs.

**Tech Stack:** Go 1.22+, Echo v4, embedded HTML/CSS/vanilla JavaScript, OpenAPI 3.0.3, Go tests

## Global Constraints

- Do not modify AppDeploy source or delete AppDeploy, CB-Tumblebug, or VM resources.
- Do not show deletion controls or protection labels for configuration-backed Agents.
- Protect every server DELETE route with the existing loopback/admin Bearer-token guard.
- Write and observe a focused failing test before each production behavior.

---

### Task 1: Individual Autonomous Event Deletion

**Files:**
- Modify: `go/service-control-api/internal/autonomy/manager.go`
- Modify: `go/service-control-api/internal/autonomy/manager_test.go`
- Modify: `go/service-control-api/internal/api/autonomy_api.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces: `func (manager *Manager) DeleteEvent(sequence uint64) (Event, bool)`
- Produces: `DELETE /api/v1/autonomy/events/:sequence`

- [ ] Add a failing Manager test for middle-event deletion, stable order, and missing sequence.
- [ ] Run `go test ./internal/autonomy -run TestManagerDeleteEvent -count=1` and confirm RED.
- [ ] Implement the locked slice removal and rerun the focused test.
- [ ] Add failing handler/auth tests, implement the protected route, and run `go test ./internal/api -run 'TestDeleteAutonomyEvent|TestAutonomyMutations' -count=1`.
- [ ] Commit the task.

### Task 2: Execution Feedback Lifecycle

**Files:**
- Modify: `go/service-control-api/internal/api/automation_feedback.go`
- Modify: `go/service-control-api/internal/api/automation_feedback_test.go`
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces: sorted Feedback listing, individual deletion by correlation ID, and clear-all
- Produces: `GET /api/v1/automation/feedback`
- Produces: `DELETE /api/v1/automation/feedback/:correlation_id`
- Produces: `DELETE /api/v1/automation/feedback`

- [ ] Add failing store/service tests for list, delete, clear, missing records, and valid re-recording.
- [ ] Run focused tests and confirm RED.
- [ ] Implement minimal mutex-protected store/service methods and rerun tests.
- [ ] Add failing route/auth tests, implement handlers, and rerun the API package.
- [ ] Commit the task.

### Task 3: Agent Control Deletion UI

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Consumes: event and Feedback REST deletion APIs
- Produces: individual and clear-all controls for Timeline, Feedback, and browser-local history

- [ ] Add failing embedded UI contract tests for every new control and handler.
- [ ] Run `go test ./internal/webui -count=1` and confirm RED.
- [ ] Add stable IDs to new and legacy local history records.
- [ ] Implement confirmed individual and bulk deletion flows with reload-after-success semantics.
- [ ] Run UI tests and Node JavaScript syntax validation.
- [ ] Commit the task.

### Task 4: Contracts, Documentation, and Verification

**Files:**
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.json`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`
- Modify: `go/service-control-api/README.md`

- [ ] Add failing OpenAPI contract assertions for all new routes.
- [ ] Update annotations/submission spec and regenerate Swagger artifacts.
- [ ] Document deletion ownership and operation in the README.
- [ ] Run `go test -count=1 ./...`, `go vet ./...`, `go build ./...`, JavaScript syntax validation, and `git diff --check`.
- [ ] Restart geon on `127.0.0.1:18081` and smoke-test APIs and desktop/mobile UI without deleting AppDeploy resources.
- [ ] Commit the task and review the final diff.

