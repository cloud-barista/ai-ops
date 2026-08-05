# Task 1 Implementation Report: Register the Operation Optimization Agent

## Scope

Implemented Task 1 on the existing `geon` branch. The change registers the bounded
`OperationOptimizationAgent`, exposes operation-agent eligibility from `ListAgents`,
and adds the capability-specific resolver. No AppDeploy files, inference optimization
configuration, temporary files, or design/plan documents were changed.

## TDD Evidence

### RED

Command:

```powershell
cd go/service-control-api
go test ./internal/api -run 'TestEligibleOperationAgents|TestService.*Agents' -count=1
```

Result: failed as expected with a package build failure because the new operation
symbols and resolver did not exist. The compiler reported undefined
`eligibleOperationAgents`, `resolveOperationAgent`,
`agentcontrol.OperationOptimizationCapability`,
`agentcontrol.OperationOptimizationDecisionAction`, and
`agentcontrol.OperationOptimizationAgentName`.

### GREEN

Command:

```powershell
cd go/service-control-api
go test ./internal/api -run 'TestEligibleOperationAgents|TestService.*Agents' -count=1
```

Result: passed.

```text
ok      kyunghee-aiops/service-control-api/internal/api  1.015s
```

Additional focused verification:

```powershell
go test ./internal/api -run 'TestEligibleOperationAgents|TestResolveOperationAgent|TestService.*Agents' -count=1
```

Result: passed.

```text
ok      kyunghee-aiops/service-control-api/internal/api  0.354s
```

`git diff --check` also passed.

Full-suite check:

```powershell
go test ./...
```

Result: failed only at `TestAutonomyActionAuthorizerUsesAgentRegistry`, which expects
`rollback_application` to remain authorized. Task 1 explicitly removes that actual
control action from `AIApplicationAutomationAgent`; the unrelated autonomy test was
not changed because Task 1 scope permits only the listed files.

## Files Changed

- `config/agent_registry.json`
- `go/service-control-api/internal/agentcontrol/models.go`
- `go/service-control-api/internal/api/operation_agent_resolver.go`
- `go/service-control-api/internal/api/operation_agent_resolver_test.go`
- `go/service-control-api/internal/api/service.go`
- `go/service-control-api/internal/api/service_test.go`
- `.superpowers/sdd/2026-08-05-two-agent-optimization/task-1-report.md`

## Implementation Notes

- Added the exact operation-optimization agent constants and registry default.
- Added `OperationOptimizationAgent` with the specified bounded scaling-decision
  capability and action.
- Removed actual deployment and control actions from
  `AIApplicationAutomationAgent`; retained its status observation actions used by the
  existing PoC and compatibility test.
- `resolveOperationAgent` follows the deterministic decision-agent resolution pattern:
  empty requests use the capability default, while disabled, missing-capability, and
  missing-action agents are rejected.
- `ListAgents` preserves existing fields and adds `eligible_operation_agents`.

## Self-Review

- The resolver filters configuration and runtime agents consistently, defaults a
  configuration source when absent, and returns a name-sorted eligible list.
- Tests exercise the resolver's eligibility/default/rejection behavior and the public
  `ListAgents` payload contract using the real registry configuration.
- The change is limited to Task 1 files. The pre-existing untracked
  `config/inference_optimization.json` and `tmp/` remain untouched and unstaged.

## Concerns

`go test ./...` remains blocked by the legacy autonomy authorization expectation for
the retired `rollback_application` action. The Task 1 focused tests pass. Runtime
execution of scaling decisions is intentionally outside this registration and discovery
task.
