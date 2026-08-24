# Guard-first Trusted Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect LLM_Op's first natural-language Safeguard to geon's existing automation Flow and approved INITIAL Revision without exposing caller-authored continuation data or changing AppDeploy.

**Architecture:** Merge the complete reviewed `LLM_Op` branch so its isolated `internal/llmop` and `internal/llmopbridge` packages remain intact. Add a small trusted in-process orchestrator that owns the call order `Review -> RunAnalysisRequest -> ProjectApprovedInitialFlow`, stops on every non-allow outcome, and returns auditable stage evidence. Keep AppDeploy submission and post-deployment optimization outside this first independently testable phase.

**Tech Stack:** Go 1.25+, Echo service-control API, existing `agentcontrol`, `llmop`, `llmopbridge`, table-driven Go tests.

**Spec:** `docs/superpowers/specs/2026-08-24-llm-op-geon-appdeploy-orchestration-design.md`

## Global Constraints

- Work on the existing `geon` branch; do not create or modify the teammate-owned `AppDeployer` branch.
- Never expose `SafeguardStageResult.Continuation` or caller-authored `PlanningConstraints` through a resume HTTP API.
- Only `allow_request` with complete continuation evidence may invoke geon.
- Clarification, rejection, deterministic Guard failure, and model failure stop fail-closed with no legacy planner or mock fallback.
- geon remains the only owner of canonical Flow, DesiredDeploymentSpec, and Manifest Revision 1/2.
- This phase stops at approved-flow projection; it does not POST to AppDeploy and does not claim a VM deployment.
- Preserve unrelated untracked files and existing user changes.

---

### Task 1: Integrate the reviewed LLM_Op packages

**Files:**
- Merge: `origin/LLM_Op`
- Preserve: `docs/superpowers/specs/2026-08-24-llm-op-geon-appdeploy-orchestration-design.md`
- Verify: `go/service-control-api/internal/llmop/*`
- Verify: `go/service-control-api/internal/llmopbridge/*`

**Interfaces:**
- Consumes: `llmop.ReviewWithConfig`, `llmop.SafeguardStageResult`, `llmopbridge.ProjectApprovedInitialFlow`.
- Produces: buildable `geon` tree containing the complete Guard-first implementation and tests.

- [ ] **Step 1: Merge the complete branch**

Run: `git merge --no-ff origin/LLM_Op`

Expected: isolated LLM_Op packages and their tests are added while the local architecture spec remains present.

- [ ] **Step 2: Run package tests**

Run: `go test ./internal/llmop ./internal/llmopbridge -count=1`

Expected: PASS.

- [ ] **Step 3: Inspect the merge**

Run: `git status --short --branch && git diff --check HEAD^..HEAD`

Expected: only pre-existing untracked files remain outside the merge.

### Task 2: Preserve explicit GPU 0 as CPU-only intent

**Files:**
- Modify: `go/service-control-api/internal/agentcontrol/requirement_analyzer_test.go`
- Modify: `go/service-control-api/internal/agentcontrol/requirement_analyzer.go`

**Interfaces:**
- Consumes: natural-language `AutomationRunInput`.
- Produces: an `ApplicationProfile` whose accelerator is not required when the caller explicitly requests `GPU 0`, without adding a GPU-memory default assumption.

- [ ] **Step 1: Write the failing regression test**

Use `GPU 0, CPU 4 cores, memory 8GiB, storage 20GiB, replicas 1` and assert `Accelerator.Required == false`, `CountMin == 0`, and no GPU default assumption is recorded.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/agentcontrol -run TestLocalRequirementAnalyzerTreatsExplicitGPUZeroAsCPUOnly -count=1`

Expected: FAIL because the existing analyzer treats any `gpu` token as requiring an accelerator.

- [ ] **Step 3: Implement the minimal semantic fix**

Parse the GPU count before deciding `gpuRequired`. An explicit zero disables the accelerator; an absent count with a GPU keyword retains the existing default of one.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/agentcontrol -run TestLocalRequirementAnalyzerTreatsExplicitGPUZeroAsCPUOnly -count=1`

Expected: PASS.

### Task 3: Define the trusted orchestration boundary with failing tests

**Files:**
- Create: `go/service-control-api/internal/trustedorchestration/orchestrator_test.go`
- Create: `go/service-control-api/internal/trustedorchestration/orchestrator.go`

**Interfaces:**
- Consumes:
  - `Reviewer.Review(context.Context, llmop.Request) (llmop.SafeguardStageResult, error)`
  - `AutomationRunner.RunAnalysisRequest(context.Context, agentcontrol.ApplicationAnalysisRequestEnvelope) (agentcontrol.AutomationRun, bool, error)`
  - `llmopbridge.TrustedAppVersionResolver`
- Produces:
  - `Orchestrator.Run(context.Context, Input) (Result, error)`
  - `Result{Status, Safeguard, AutomationRun, ApprovedProjection}`

- [ ] **Step 1: Write the allow-path failing test**

The test uses a fake reviewer returning a fully approved Safeguard stage, the real geon `AutomationRunner`, and a trusted app-version resolver. It asserts that the same `correlation_id`, `trace_id`, user request, approved Flow, and INITIAL Revision are preserved in `ApprovedProjection`.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/trustedorchestration -run TestOrchestratorRunsApprovedRequestThroughGeon -count=1`

Expected: FAIL because `Orchestrator` does not exist.

- [ ] **Step 3: Implement the minimal allow path**

Implement the interfaces and immutable result envelope. Validate request/analysis identity before calling the reviewer, call geon only after `Safeguard.Approved == true`, require a completed run with a Flow, and call `ProjectApprovedInitialFlow` with the trusted resolver.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/trustedorchestration -run TestOrchestratorRunsApprovedRequestThroughGeon -count=1`

Expected: PASS.

### Task 4: Enforce fail-closed outcomes and stage ordering

**Files:**
- Modify: `go/service-control-api/internal/trustedorchestration/orchestrator_test.go`
- Modify: `go/service-control-api/internal/trustedorchestration/orchestrator.go`

**Interfaces:**
- Consumes: Task 3 `Orchestrator`.
- Produces: stable terminal statuses `SAFEGUARD_STOPPED`, `GEON_REJECTED`, `APPROVED_FLOW_READY` and call-order evidence.

- [ ] **Step 1: Write failing rejection and clarification tests**

Add table cases for `REQUEST_REJECTED`, `CLARIFICATION_REQUIRED`, reviewer error, changed user request, and changed correlation/trace identity. Assert the automation runner and resolver are never called.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/trustedorchestration -run 'TestOrchestratorStops|TestOrchestratorRejectsIdentityDrift' -count=1`

Expected: FAIL on missing terminal handling.

- [ ] **Step 3: Implement fail-closed handling**

Return the Safeguard evidence for normal reject/clarify outcomes without invoking geon. Return an error for reviewer failure, incomplete approval, identity drift, geon failure, missing Flow, non-DEPLOY Flow, or projection failure. Never synthesize approval or switch to another planner.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/trustedorchestration -count=1`

Expected: PASS.

### Task 5: Add the production LLM_Op reviewer adapter

**Files:**
- Create: `go/service-control-api/internal/trustedorchestration/reviewer.go`
- Create: `go/service-control-api/internal/trustedorchestration/reviewer_test.go`

**Interfaces:**
- Consumes: `llmop.ReviewWithConfig`.
- Produces: `ConfigReviewer{CandidateConfigPath, GuardPolicyPath, AllowLiveCompletion, HTTPClient}` implementing `Reviewer`.

- [ ] **Step 1: Write failing configuration tests**

Assert empty candidate or Guard policy paths fail before execution, and default configuration keeps live completion disabled.

- [ ] **Step 2: Verify RED**

Run: `go test ./internal/trustedorchestration -run TestConfigReviewer -count=1`

Expected: FAIL because `ConfigReviewer` does not exist.

- [ ] **Step 3: Implement the adapter**

Validate required paths, forward the exact request to `llmop.ReviewWithConfig`, and set `ProviderOptions.AllowLiveCompletion` only from the explicit field. Do not add fallback behavior.

- [ ] **Step 4: Verify GREEN**

Run: `go test ./internal/trustedorchestration -count=1`

Expected: PASS.

### Task 6: Document the executable integration boundary

**Files:**
- Modify: `README.md`
- Modify: `go/service-control-api/README.md`

**Interfaces:**
- Consumes: trusted orchestration API from Tasks 3-5.
- Produces: concise operator documentation that separates Phase 1 evidence from later AppDeploy execution.

- [ ] **Step 1: Add the actual call order**

Document `ReviewWithConfig -> AutomationRunner -> ProjectApprovedInitialFlow`, the fail-closed states, and that no AppDeploy POST occurs in this phase.

- [ ] **Step 2: Add verification commands**

Document `go test ./internal/llmop ./internal/llmopbridge ./internal/trustedorchestration -count=1` and label `/llm-op-demo` as a contract demo rather than a live integrated deployment.

- [ ] **Step 3: Check documentation consistency**

Run: `rg -n "fallback|not_submitted|ProjectApprovedInitialFlow|AppDeploy" README.md go/service-control-api/README.md`

Expected: the responsibility boundary is explicit and no prepare-only result is described as a real deployment.

### Task 7: Verify the complete geon tree

**Files:**
- Verify only.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: tested Phase 1 implementation ready for review on `geon`.

- [ ] **Step 1: Run focused tests**

Run: `go test ./internal/llmop ./internal/llmopbridge ./internal/trustedorchestration -count=1`

Expected: PASS.

- [ ] **Step 2: Run the full test suite**

Run: `go test ./... -count=1`

Expected: PASS.

- [ ] **Step 3: Run static checks**

Run: `go vet ./...`

Expected: PASS.

- [ ] **Step 4: Check formatting and worktree scope**

Run: `gofmt -w internal/trustedorchestration/*.go && git diff --check && git status --short --branch`

Expected: no whitespace errors; unrelated untracked files remain unchanged.
