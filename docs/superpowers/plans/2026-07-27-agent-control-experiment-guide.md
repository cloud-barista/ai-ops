# Agent Control Experiment Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-app experiment guide and reorder Agent Control navigation to match the Registry-to-Manifest-to-post-deployment research workflow.

**Architecture:** Extend the embedded Overview HTML with an ordered guide that uses the existing `data-view-target` navigation contract. Keep all behavior client-side and presentation-only; no API, ControlRun, Planner, Guard, Autonomy, or Feedback contract changes are required.

**Tech Stack:** Go embedded assets, HTML5, CSS, vanilla JavaScript, Echo web UI tests, in-app browser Playwright verification.

## Global Constraints

- Modify only the geon repository and existing `geon` branch.
- Do not modify AppDeploy or any AppDeploy-owned resource.
- Keep `DeploymentManifest` separate from operational `Action Proposal`.
- Mark AppDeploy submission and post-deployment autonomy as optional.
- Do not make Autonomous Loop a Manifest-generation stage.
- Do not modify or stage `config/inference_optimization.json`.
- Follow the existing visual language and responsive breakpoints.
- Every behavior change follows a failing-test-first cycle.

---

### Task 1: Navigation Order and Experiment Guide

**Files:**
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.css`

**Interfaces:**
- Consumes: existing `data-view-target` click binding in `static/app.js`
- Produces: `id="experiment-guide"`, `.experiment-guide-track`, and ordered navigation controls

- [ ] **Step 1: Write the failing embedded UI contract test**

Add a test that reads `/` through the real embedded web handler and verifies:

```go
func TestControlAppContainsOrderedExperimentGuide(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	required := []string{
		`id="experiment-guide"`,
		`실험 순서`,
		`핵심 Manifest 실험`,
		`선택적 외부 배포`,
		`선택적 배포 후 실험`,
		`data-guide-step="registry"`,
		`data-guide-step="manifest"`,
		`data-guide-step="deploy"`,
		`data-guide-step="operate"`,
		`data-guide-step="feedback"`,
		`MANIFEST_APPROVED`,
		`DeploymentManifest`,
	}
	for _, expected := range required {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected experiment guide contract %q", expected)
		}
	}

	agents := strings.Index(html, `data-view-target="agents"`)
	planner := strings.Index(html, `data-view-target="planner"`)
	autonomy := strings.Index(html, `data-view-target="autonomy"`)
	feedback := strings.Index(html, `data-view-target="feedback"`)
	if !(agents < planner && planner < autonomy && autonomy < feedback) {
		t.Fatalf("navigation is not ordered by the experiment flow")
	}
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
go test ./internal/webui -run OrderedExperimentGuide
```

Expected: FAIL because `experiment-guide` and the ordered controls do not exist.

- [ ] **Step 3: Reorder the navigation**

In `static/index.html`, keep Overview first and order the remaining buttons:

```text
Overview
Agents & Guard
Deployment Planner
Post-deployment Autonomy
Feedback
```

Change only the visible Autonomy label and accessible name; keep
`data-view-target="autonomy"` so existing JavaScript behavior remains intact.

- [ ] **Step 4: Add the Overview experiment guide**

Insert the full-width guide after the Overview heading and before the metric
grid. Each ordered item is a real button with:

```html
<button type="button"
        class="experiment-guide-step"
        data-guide-step="registry"
        data-view-target="agents">
  <span class="experiment-step-number">1</span>
  <span class="experiment-step-copy">
    <strong>Agent Registry 확인</strong>
    <small>활성 Agent · capability · bounded action</small>
    <em>핵심 Manifest 실험</em>
  </span>
</button>
```

Use the following ordered outcomes:

```text
1 Registry: authorized Planner Agent
2 Manifest: MANIFEST_APPROVED + DeploymentManifest
3 Deploy: deployment_id + status/logs (optional)
4 Operate: guarded Action Proposal + Autonomy Event (optional)
5 Feedback: run_id-linked execution feedback
```

- [ ] **Step 5: Add responsive guide styling**

Add stable layout rules:

```css
.experiment-guide-track {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

@media (max-width: 1120px) {
  .experiment-guide-track {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 580px) {
  .experiment-guide-track {
    grid-template-columns: 1fr;
  }
}
```

Guide buttons must wrap text, keep stable minimum height, use existing color
tokens, and avoid horizontal overflow.

- [ ] **Step 6: Run focused and package tests**

```powershell
go test ./internal/webui
go test ./internal/api -run 'Embedded|RoutesInclude'
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add go/service-control-api/internal/webui
git commit -m "feat: add ordered agent control experiment guide"
```

### Task 2: Documentation and Browser Verification

**Files:**
- Modify: `go/service-control-api/README.md`
- Modify only if browser verification reveals a defect:
  - `go/service-control-api/internal/webui/static/index.html`
  - `go/service-control-api/internal/webui/static/app.css`
  - `go/service-control-api/internal/webui/webui_test.go`

**Interfaces:**
- Consumes: local Agent Control at `http://127.0.0.1:18080/`
- Produces: documented in-app order and verified desktop/mobile workflow

- [ ] **Step 1: Add README guide order**

In the Agent Control usage section, document:

```text
Overview
→ Agents & Guard
→ Deployment Planner
→ optional AppDeploy submission
→ optional Post-deployment Autonomy
→ Feedback
```

State that steps through `MANIFEST_APPROVED` form the core Manifest experiment.

- [ ] **Step 2: Run documentation checks**

```powershell
git diff --check
go test ./internal/webui ./internal/api
```

Expected: PASS.

- [ ] **Step 3: Start or restart the local server**

Run with Ollama and Qwen configured. AppDeploy may remain stopped because the
guide itself is presentation-only and Manifest generation remains independent.

- [ ] **Step 4: Verify desktop behavior**

At the default desktop viewport verify:

- sidebar order matches the guide;
- every guide step is visible and clickable;
- Registry and Planner buttons switch to the correct views;
- required and optional labels are visually distinct;
- no text overlap or clipping;
- browser console has no warnings or errors.

- [ ] **Step 5: Verify mobile behavior**

At `390x844` verify:

- navigation icons follow the same order;
- guide steps collapse to one column;
- `scrollWidth == clientWidth`;
- labels and outcomes wrap without truncation.

- [ ] **Step 6: Run final verification**

```powershell
go test ./...
go vet ./...
git diff --check
git status --short --branch
```

Expected: all Go tests and vet pass; only the pre-existing untracked
`config/inference_optimization.json` remains outside commits.

- [ ] **Step 7: Commit documentation or verification fixes**

```powershell
git add go/service-control-api/README.md go/service-control-api/internal/webui
git commit -m "docs: explain agent control experiment order"
```
