# geon Deletion Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe deletion controls for runtime Agents, autonomous-loop events, and browser-local history without modifying AppDeploy or configuration-backed core Agents.

**Architecture:** Extend the mutex-protected runtime Agent store and autonomy Manager with narrow deletion methods, then expose them through authenticated DELETE routes. The embedded Agent Control UI will render deletion controls only for resources that geon owns and will refresh server state after confirmed deletion.

**Tech Stack:** Go 1.22+, Echo v4, embedded HTML/CSS/vanilla JavaScript, OpenAPI 3.0.3, Go tests

## Global Constraints

- Only `source=runtime` Agents can be deleted.
- `config/agent_registry.json` and `AIApplicationAutomationAgent` must not be modified or deleted.
- AppDeploy App, Deployment, Runtime Profile, Target Profile, CB-Tumblebug Infra, and VM resources are out of scope.
- DELETE routes must use the existing loopback/admin Bearer-token mutation protection.
- Event clearing must preserve autonomy configuration, runtime state, cooldown, action budget, and monotonic event sequence.
- Production behavior must be implemented only after a focused failing test is observed.

---

### Task 1: Runtime Agent deletion domain behavior

**Files:**
- Modify: `go/service-control-api/internal/api/agent_registry_runtime.go`
- Modify: `go/service-control-api/internal/api/agent_registry_test.go`

**Interfaces:**
- Produces: `func (store *runtimeAgentStore) remove(name string) (AgentProfile, bool)`
- Produces: `func (service Service) DeleteExternalAgent(ctx context.Context, name string) (AgentProfile, error)`
- Produces: sentinel errors `errRuntimeAgentNotFound` and `errConfiguredAgentProtected`

- [ ] **Step 1: Write focused failing service tests**

Add tests that register `ResearchModelMonitorAgent`, delete it, and assert both `ShowAgent` and `BuildAgentInvocationPlan` fail afterward. Add separate assertions that deleting `AIApplicationAutomationAgent` returns `errConfiguredAgentProtected` and deleting `MissingRuntimeAgent` returns `errRuntimeAgentNotFound`.

```go
deleted, err := service.DeleteExternalAgent(ctx, registered.Name)
if err != nil || deleted.Name != registered.Name || deleted.Source != agentSourceRuntime {
	t.Fatalf("unexpected deletion: deleted=%#v err=%v", deleted, err)
}
if _, err := service.ShowAgent(ctx, registered.Name); err == nil {
	t.Fatal("deleted runtime agent is still visible")
}
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
go test ./internal/api -run 'TestDeleteExternalAgent' -count=1
```

Expected: build failure because `DeleteExternalAgent` and the sentinel errors do not exist.

- [ ] **Step 3: Implement atomic runtime deletion**

Add a store method that locks `store.mu`, retrieves the Agent, deletes the map entry, and returns a copy. Add service logic that checks the configuration registry first, rejects protected Agents, then removes only from the runtime store.

```go
func (store *runtimeAgentStore) remove(name string) (AgentProfile, bool) {
	store.mu.Lock()
	defer store.mu.Unlock()
	agent, ok := store.agents[name]
	if ok {
		delete(store.agents, name)
	}
	return agent, ok
}
```

- [ ] **Step 4: Run focused and race-independent package tests**

Run:

```powershell
go test ./internal/api -run 'Test(Register|Build|Delete).*Agent' -count=1
go test ./internal/api -count=1
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add go/service-control-api/internal/api/agent_registry_runtime.go go/service-control-api/internal/api/agent_registry_test.go
git commit -m "feat: delete runtime agents safely"
```

---

### Task 2: Autonomous event clearing

**Files:**
- Modify: `go/service-control-api/internal/autonomy/manager.go`
- Modify: `go/service-control-api/internal/autonomy/manager_test.go`

**Interfaces:**
- Produces: `func (manager *Manager) ClearEvents() int`

- [ ] **Step 1: Write a failing Manager test**

Record two events, capture the latest sequence and Manager status, clear events, and assert the returned count is two. Assert events are empty, config and runtime state are unchanged, then record a new event and assert its sequence is greater than the previous value.

```go
deleted := manager.ClearEvents()
if deleted != 2 || len(manager.Events()) != 0 {
	t.Fatalf("deleted=%d events=%d", deleted, len(manager.Events()))
}
manager.recordEvent(Event{Stage: "test", Status: "after-clear"})
if manager.Events()[0].Sequence <= previousSequence {
	t.Fatal("event sequence was reused after clear")
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
go test ./internal/autonomy -run TestManagerClearEvents -count=1
```

Expected: build failure because `ClearEvents` does not exist.

- [ ] **Step 3: Implement atomic event clearing**

Lock `manager.mu`, capture `len(manager.events)`, replace it with a zero-length slice with capacity 200, and return the count. Do not modify `sequence`, `config`, `state`, `running`, or cycle cancellation fields.

- [ ] **Step 4: Run autonomy tests**

Run:

```powershell
go test ./internal/autonomy -count=1
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```powershell
git add go/service-control-api/internal/autonomy/manager.go go/service-control-api/internal/autonomy/manager_test.go
git commit -m "feat: clear autonomy event history"
```

---

### Task 3: Authenticated DELETE REST APIs

**Files:**
- Modify: `go/service-control-api/internal/api/server.go`
- Modify: `go/service-control-api/internal/api/autonomy_api.go`
- Modify: `go/service-control-api/internal/api/server_test.go`
- Modify: `go/service-control-api/internal/api/openapi_contract_test.go`
- Modify: `docs/submission/openapi_service_control.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.yaml`
- Modify: `go/service-control-api/docs/swagger/swagger.json`

**Interfaces:**
- Produces: `DELETE /api/v1/agents/:name`
- Produces: `DELETE /api/v1/autonomy/events`
- Produces: `func (handler restHandler) RestDeleteAgent(echo.Context) error`
- Produces: `func (handler restHandler) RestDeleteAutonomyEvents(echo.Context) error`

- [ ] **Step 1: Write failing route tests**

Register a runtime Agent through POST, delete it through DELETE, assert `200` and `deleted=true`, then assert GET returns `404`. Assert configured Agent DELETE returns `403`, missing Agent DELETE returns `404`, and autonomy event DELETE returns the number removed while GET returns zero events.

Add both DELETE paths to remote mutation authentication tests and to the OpenAPI method assertions.

- [ ] **Step 2: Run API tests and verify RED**

Run:

```powershell
go test ./internal/api -run 'Test(Delete|AutonomyMutations|OpenAPI)' -count=1
```

Expected: route tests return `404 Method Not Allowed` or contract tests report missing DELETE operations.

- [ ] **Step 3: Add handlers and protected routes**

Register both routes through `handler.requireAutonomyAdmin`. Map protected configuration Agent errors to `403`, missing runtime Agent errors to `404`, and successful deletions to explicit JSON.

```go
return context.JSON(http.StatusOK, map[string]any{
	"deleted": true,
	"name": agent.Name,
	"source": agent.Source,
})
```

For event clearing return `deleted`, `deleted_count`, and an empty `events` array. A concurrently running loop may create newer events after the clear operation.

- [ ] **Step 4: Extend OpenAPI and generated Swagger artifacts**

Document success responses and `401`, `403`, and `404` failures. Update the Swagger annotations, then regenerate the two committed Swagger artifacts with the repository-pinned Swag version.

```powershell
go run github.com/swaggo/swag/cmd/swag@v1.16.6 init -d cmd/service-control-api,internal/api -g main.go -o docs/swagger --parseInternal --outputTypes json,yaml
```

- [ ] **Step 5: Run API and contract tests**

Run:

```powershell
go test ./internal/api -count=1
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```powershell
git add go/service-control-api/internal/api docs/submission/openapi_service_control.yaml go/service-control-api/docs/swagger
git commit -m "feat: expose protected geon deletion APIs"
```

---

### Task 4: Agent Control deletion UI and user documentation

**Files:**
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/README.md`

**Interfaces:**
- Consumes: `DELETE /api/v1/agents/{name}` and `DELETE /api/v1/autonomy/events`
- Produces: Runtime Agent trash controls, protected-Agent indicator, event clear control, confirmed local-history clear

- [ ] **Step 1: Write failing embedded UI contract tests**

Require the HTML and JavaScript assets to contain `delete-agent`, `clear-autonomy-events`, `DELETE`, `/api/v1/autonomy/events`, and a protected configuration Agent label. Keep the checks behavioral enough to ensure handlers and controls are wired, not just descriptive text.

- [ ] **Step 2: Run web UI tests and verify RED**

Run:

```powershell
go test ./internal/webui -count=1
```

Expected: FAIL because the new controls and handlers are absent.

- [ ] **Step 3: Implement Agent deletion controls**

Render a trash icon button only when `agent.source === "runtime"`. Use a lock icon and protected label for configuration Agents. Confirm with the Agent name, call DELETE, clear the selected Agent when needed, and reload the registry only after success.

- [ ] **Step 4: Implement event and local-history clearing controls**

Add an icon button next to the Timeline refresh button. Confirm that only event history is removed, call the server DELETE route, then reload status/events. Add a confirmation dialog before the existing localStorage clear operation.

- [ ] **Step 5: Add responsive control styling and operating guide**

Use existing button tokens and Lucide icons, keep controls within the panel header at mobile width, and document the three deletion scopes and protected resources in the service README.

- [ ] **Step 6: Run UI syntax and tests**

Run:

```powershell
go test ./internal/webui -count=1
& 'C:\Users\geonhae\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check internal/webui/static/app.js
```

Expected: PASS with no JavaScript syntax output.

- [ ] **Step 7: Commit Task 4**

```powershell
git add go/service-control-api/internal/webui go/service-control-api/README.md
git commit -m "feat: add geon deletion controls to agent control"
```

---

### Task 5: Full verification and local smoke test

**Files:**
- Verify only

**Interfaces:**
- Verifies all deletion controls without deleting AppDeploy resources.

- [ ] **Step 1: Run complete static verification**

```powershell
go test -count=1 ./...
go vet ./...
go build ./...
& 'C:\Users\geonhae\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check internal/webui/static/app.js
git diff --check
```

Expected: all commands succeed.

- [ ] **Step 2: Restart geon on loopback port 18081**

Start with `AIOPS_BIND_ADDRESS=127.0.0.1`, `PORT=18081`, the existing Qwen candidate config, planner guard policy, and AppDeploy base URL.

- [ ] **Step 3: Smoke-test only geon-owned deletion**

Register a disposable Runtime Agent, delete it, confirm it no longer appears, run a Monitor Only cycle to create an event, clear events, and verify AppDeploy deployment count and IDs are unchanged before and after.

- [ ] **Step 4: Verify desktop and mobile UI**

Confirm configuration Agents show protection, Runtime Agents show a trash control, Timeline clear works, confirmation dialogs appear, and there is no horizontal overflow at 1440x900 and 390x844.

- [ ] **Step 5: Final repository check**

```powershell
git status --short
git log --oneline -8
```

Expected: clean feature worktree with AppDeploy source untouched.
