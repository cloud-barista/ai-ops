# geon Agent Control App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing geon planner, Agent Registry, Qwen Action, Go Guard, AppDeploy integration, and feedback APIs through one operational web interface served by the service-control API.

**Architecture:** Embed a dependency-free HTML/CSS/JavaScript application in the Go service-control binary and register `/` plus `/assets/*` routes on the existing Echo server. The browser calls only same-origin geon APIs; the existing planner service remains the only component that calls AppDeploy, so the AppDeploy repository stays unchanged.

**Tech Stack:** Go 1.25, Echo v4, `embed`, semantic HTML, responsive CSS, vanilla JavaScript, existing geon REST APIs.

## Global Constraints

- Modify only `Kyunghee-aiops-go`; do not modify AppDeploy.
- Serve the control app from the same process and port as service-control API.
- Reuse existing API contracts without adding a second backend or frontend build toolchain.
- Present AppDeploy execution and Agent Action Guard as separate, accurately labelled workflows.
- Never claim a handoff was executed when the API reports `not_executed` or `pending_executor`.
- Preserve the current VM-only, non-container first-year scope.

---

### Task 1: Embedded Web Routes

**Files:**
- Create: `go/service-control-api/internal/webui/webui.go`
- Create: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/api/server.go`

**Interfaces:**
- Produces: `webui.Register(*echo.Echo)` registering `/`, `/assets/app.css`, and `/assets/app.js`.
- Consumes: existing Echo server from `api.NewServer`.

- [ ] Write route tests that expect the dashboard HTML and static assets.
- [ ] Run `go test ./internal/webui` and confirm the tests fail because the package is missing.
- [ ] Add the minimal embedded-file handler and register it from `api.NewServer`.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Agent Control Interface

**Files:**
- Create: `go/service-control-api/internal/webui/static/index.html`
- Create: `go/service-control-api/internal/webui/static/app.css`
- Create: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Consumes: `/healthz`, `/api/v1/agents`, `/api/v1/planner/deployments`, `/api/v1/automation/action-proposals`, and `/api/v1/automation/feedback`.
- Produces: a responsive operational UI with Overview, Deployment Planner, Agents and Guard, and Feedback views.

- [ ] Add failing content tests for the four views, required forms, and API route references.
- [ ] Implement accessible HTML with stable form and result element IDs.
- [ ] Implement responsive, restrained operational styling without a frontend dependency.
- [ ] Implement same-origin API calls, loading/error states, escaped JSON rendering, and persisted recent results.
- [ ] Run focused tests and confirm they pass.

### Task 3: Integration and Documentation

**Files:**
- Modify: `go/service-control-api/README.md`
- Test: `go/service-control-api/internal/api/server_test.go`

**Interfaces:**
- Produces: documented startup URL and an API server that serves both web and REST endpoints.

- [ ] Add a failing API integration assertion for `GET /`.
- [ ] Verify the embedded UI through `api.NewServer`.
- [ ] Document `http://127.0.0.1:<PORT>/` and the separation from AppDeploy on port 8080.
- [ ] Run `go test ./...` in `go/service-control-api`.

### Task 4: Browser Verification

**Files:**
- No production file changes expected.

**Interfaces:**
- Consumes: running Ollama, geon API, and AppDeploy.
- Produces: verified desktop and mobile rendering plus a working planner and Agent Registry interaction.

- [ ] Start geon on port 18080 with Qwen and AppDeploy environment variables.
- [ ] Open the dashboard at desktop and mobile viewport sizes.
- [ ] Verify navigation, Agent Registry loading, form layout, loading states, and non-overlapping content.
- [ ] Submit a safe planner request when current AppDeploy test data is available; otherwise verify the request reaches the API and reports the server error honestly.
- [ ] Record the final local URL and any external-service prerequisites.
