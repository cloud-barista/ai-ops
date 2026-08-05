# Simplified Agent Control Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dense geon Overview with a compact Manifest experiment entry screen while preserving every underlying Agent, Guard, ControlRun, AppDeploy, Autonomy, and Feedback function.

**Architecture:** Keep the existing single-page view router and embedded static assets. Restructure only the Overview HTML, move the ControlRun timeline into the Planner view, remove JavaScript references to deleted Overview-only elements, and place the existing five-step guide inside a native collapsed `<details>` disclosure.

**Tech Stack:** Go 1.22+, Echo embedded web assets, HTML5, CSS, vanilla JavaScript, Go `testing`

## Global Constraints

- Work only in `C:\Users\geonhae\Documents\Kyunghee-aiops-go` on the existing `geon` branch.
- Do not modify the AppDeploy repository or AppDeploy execution responsibilities.
- Keep `DeploymentManifest` separate from operational `Action Proposal`.
- Preserve `config/inference_optimization.json` unchanged.
- Do not add a frontend dependency or a new API route.
- The disclosure must be collapsed by default.

---

### Task 1: Simplify The Overview Contract

**Files:**
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`

**Interfaces:**
- Consumes: existing `data-view-target` click routing and ControlRun rendering functions.
- Produces: `#overview-agent-action`, `#overview-manifest-action`, `#experiment-guide-disclosure`, and the existing `#control-run-list`.

- [ ] **Step 1: Write the failing simplified Overview test**

Add `TestControlAppContainsSimplifiedOverview` to isolate the Overview section and assert:

```go
for _, expected := range []string{
    `id="overview-agent-action"`,
    `id="overview-manifest-action"`,
    `id="experiment-guide-disclosure"`,
    `<summary`,
    `id="control-run-list"`,
    `id="metric-api"`,
    `id="metric-agents"`,
    `Qwen3.5`,
} {
    if !strings.Contains(overview, expected) {
        t.Fatalf("expected simplified Overview contract %q", expected)
    }
}

for _, removed := range []string{
    `id="workflow-title"`,
    `id="overview-agent-list"`,
    `id="control-run-timeline"`,
    `id="metric-guard"`,
} {
    if strings.Contains(overview, removed) {
        t.Fatalf("expected duplicate Overview element %q to be removed", removed)
    }
}
```

Also assert that the disclosure opening tag does not contain the `open` attribute.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
go test ./internal/webui -run SimplifiedOverview -count=1
```

Expected: FAIL because `overview-agent-action` and the collapsed disclosure do not exist.

- [ ] **Step 3: Replace the dense Overview markup**

In `index.html`:

- Render only three status metrics: geon API, registered Agent count, and Qwen3.5.
- Add a compact `Manifest 실험 시작` panel with two buttons:

```html
<button id="overview-agent-action" data-view-target="agents">Agent 확인</button>
<button id="overview-manifest-action" data-view-target="planner">Manifest 생성</button>
```

- Keep the recent `#control-run-list` panel at full width.
- Wrap the existing five clickable guide steps in:

```html
<details class="panel experiment-guide-disclosure" id="experiment-guide-disclosure">
  <summary>전체 실험 순서 보기</summary>
  <div class="experiment-guide-track">...</div>
</details>
```

- Remove the duplicate workflow panel and Agent Snapshot.
- Move `#control-run-timeline` and `#selected-run-id` from the Overview into the Planner view below its split layout.

- [ ] **Step 4: Remove stale JavaScript references**

In `app.js`:

- Remove `state.lastGuard`.
- Remove the `overview-agent-list` lookup and snapshot rendering from `renderAgents`.
- Remove `metric-guard` and `metric-guard-detail` updates from `renderAction`.
- Keep `renderControlRunTimeline` unchanged because its DOM now belongs to the Planner view.

- [ ] **Step 5: Add compact and responsive styles**

In `app.css`:

- Add a three-column `.overview-metrics` grid.
- Add a restrained `.overview-start-panel` and two command buttons.
- Style `.experiment-guide-disclosure > summary` as a compact disclosure header.
- Preserve the existing five-step guide styling only when the disclosure is open.
- Stack metrics and commands at `580px` without horizontal overflow.
- Remove unused workflow and Overview grid styles.

- [ ] **Step 6: Run focused web UI tests and verify GREEN**

Run:

```powershell
go test ./internal/webui -count=1
```

Expected: PASS.

- [ ] **Step 7: Commit the simplified Overview**

```powershell
git add -- go/service-control-api/internal/webui/webui_test.go go/service-control-api/internal/webui/static/index.html go/service-control-api/internal/webui/static/app.js go/service-control-api/internal/webui/static/app.css
git commit -m "feat: simplify agent control overview"
```

---

### Task 2: Align Documentation And Verify The Running UI

**Files:**
- Modify: `go/service-control-api/README.md`

**Interfaces:**
- Consumes: the simplified Overview labels from Task 1.
- Produces: documented Overview behavior matching the running UI.

- [ ] **Step 1: Update the README Overview description**

Document that the default Overview contains service state, `Agent 확인`, `Manifest 생성`, and recent ControlRuns. State that `전체 실험 순서 보기` is collapsed by default and that AppDeploy, Autonomy, and Feedback remain optional post-Manifest steps.

- [ ] **Step 2: Run complete verification**

Run:

```powershell
go test ./...
go vet ./...
git diff --check
```

Expected: all commands succeed.

- [ ] **Step 3: Verify desktop and mobile behavior**

At `http://127.0.0.1:18080/` verify:

- The disclosure is closed by default.
- Opening it reveals all five clickable steps.
- `Agent 확인` opens **Agents & Guard**.
- `Manifest 생성` opens **Deployment Planner**.
- A 390-by-844 viewport has no horizontal overflow.
- Browser console contains no warnings or errors.

- [ ] **Step 4: Commit the documentation**

```powershell
git add -- go/service-control-api/README.md
git commit -m "docs: explain simplified agent control overview"
```

