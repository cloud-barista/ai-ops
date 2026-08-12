# Final Important-Finding Fix Report

**Base:** `37b03c0` on `geon`
**Review:** `.superpowers/sdd/2026-08-05-two-agent-optimization/final-code-review.md`
**Design:** `docs/superpowers/specs/2026-08-05-two-agent-optimization-design.md`
**Plan:** `docs/superpowers/plans/2026-08-05-two-agent-optimization.md`

## Finding 1: Complete Scaling Result Contract

- Added deterministic Scaling Guard checks for:
  - non-empty `reason`;
  - a maximum of 8,000 Unicode characters, matching the repository's existing bounded natural-language policy;
  - non-empty, parseable RFC3339 `created_at`.
- Applied the checks in `validateOperationOptimizationResult`, so Internal, Runtime, and injected operation runtime results use the same domain contract.
- Kept Internal rule outputs valid: `ProposeRuleBasedScalingDecision` already supplies a bounded reason and RFC3339Nano timestamp.
- Invalid otherwise-approved Runtime results retain explicit failed `decision_reason` or `decision_created_at` Scaling Guard evidence and never populate `flow.scaling_decision`.
- Documented the same reason bounds in submission OpenAPI.

## Finding 2: Auditable Operation Execution Identity

- Added required `operation_agent_execution.run_id` evidence.
- Operation execution IDs use `run-operation-<24 hex chars>` and are deterministically derived from the Flow `correlation_id` and optimization Feedback `message_id`.
- The operation ID is distinct from deployment `automation_run_id`, remains stable for idempotent replay, and is persisted for successful and terminal-safe failed execution evidence.
- Protocol-created Flows may retain an empty `automation_run_id`; their operation dispatch still receives a non-empty operation ID.
- Generic Agent result Guard now rejects empty request or result `run_id` before checking equality. Mismatched IDs remain rejected.
- Registry operation runtime rejects an empty request ID before invoking Internal or Runtime executors. Internal and Runtime results must echo the dispatched ID.
- Strengthening the generic Guard exposed the existing protocol decision dispatch's empty ID. That path now derives a separate stable `run-decision-*` ID without changing the Flow's compatibility `automation_run_id` field.
- Submission OpenAPI and generated Swagger JSON/YAML expose the operation result `run_id`.

## Strict TDD Evidence

### Finding 1 RED

`go test ./internal/agentcontrol ./internal/api -run 'TestValidateOperationOptimizationResult|TestOptimizationFeedbackDoesNotPersistInvalidRuntimeDecisionContract|TestSubmissionOpenAPIIncludesAgentControlDeletionAndScalingDecision' -count=1`

- Failed because missing/over-limit reasons and missing/invalid timestamps were still `APPROVED`.
- Corrected the integrated fixture to include matching SLO evidence, then confirmed missing reason and missing/invalid `created_at` were persisted as `ScalingDecision` values.
- OpenAPI failed because `ScalingDecision.reason` had no `minLength`/`maxLength`.

### Finding 2 RED

- Initial focused build failed because `OperationOptimizationResult` had no `RunID` field.
- After adding only that field, the behavioral RED run showed:
  - protocol operation request `RunID:""`;
  - Internal and Runtime operation evidence dropping the request ID;
  - missing execution ID approved by Scaling Guard;
  - empty direct operation request invoking the executor;
  - OpenAPI omitting operation result `run_id`.
- `go test ./internal/api -run 'TestValidateAgentExecutionResultRejectsEmptyRequestOrResultRunID' -count=1` failed because one empty ID was reported only as a mismatch and two empty IDs were approved.
- Generated Swagger contract RED failed because `agentcontrol.OperationOptimizationResult` omitted `run_id`.

### GREEN

`go test ./internal/agentcontrol ./internal/api -run 'TestValidateOperationOptimizationResult|TestOptimizationFeedbackDoesNotPersistInvalidRuntimeDecisionContract|TestProtocolFlowUsesStableOperationExecutionRunIDAcrossReplay|TestValidateAgentExecutionResult|TestOperationOptimizationRuntime|TestSubmissionOpenAPIIncludesAgentControlDeletionAndScalingDecision|TestGeneratedSwaggerDocumentsCanonicalOperationScalingAction' -count=1`

Result: PASS for `internal/agentcontrol` and `internal/api`.

The first full service run then exposed five protocol API failures caused by the stronger generic Guard rejecting the initial decision Agent's empty ID. After deriving `run-decision-*` for that compatibility path, the exact five failed API tests passed and the complete service suite passed.

## Final Verification

From `go/service-control-api`:

- PASS: `go test ./... -count=1`
- PASS: `go vet ./...`
- PASS: `go build ./cmd/service-control-api`
- PASS: `go build ./cmd/aiops-service-control`
- PASS: pinned Swagger generation with `go run github.com/swaggo/swag/cmd/swag@v1.16.6 ...`

From `go/aiops-guard`:

- PASS: `go test ./... -count=1`
- PASS: `go vet ./...`
- PASS: `go build ./...`

Browser suites were not rerun because no HTML, CSS, JavaScript, selector, rendering, or frontend workflow behavior changed. The full service suite still passed `internal/webui`; the only UI-visible contract addition is the auditable `run_id` in existing raw Flow JSON.

## Preserved Safety And Scope

- Deployment status/Feedback ordering, replay short-circuiting, stale-result suppression, and approved-only scaling persistence remain intact.
- Runtime Agent failures still do not fall back to an Internal Agent.
- Terminal Runtime errors remain sanitized; the new run ID is deterministic audit metadata and contains no endpoint, credential, or Runtime message content.
- Scaling remains a recommendation only. Mock `SIMULATED`/`READY` evidence is not real VM, Kubernetes, AppDeploy, or scaling execution evidence.
- Common JSON status and optimization Feedback request bodies are unchanged. The API response contract only adds required operation execution `run_id` evidence and tightens documented scaling reason bounds.
- No AppDeploy implementation or frontend asset was changed.
- Pre-existing untracked `config/inference_optimization.json` and `tmp/` are excluded from the commit.
