# Go And LLM Cross Validation

## Goal

The system separates LLM reasoning from deterministic service-control checks.
LLM selection and high-level readiness decisions are represented in config and
API responses, while Go validates bounded actions, placement constraints,
deployment-plan structure, and guard readiness.

## Control Boundary

```text
LLM/model policy
-> selected runtime candidate
-> registered agent roles
-> bounded action validation
-> CPU/GPU placement constraints
-> deployment manifest dry-run
-> guard-readiness result
```

## Go Responsibilities

| Layer | Go responsibility |
| --- | --- |
| LLM selection | Rank candidate labels under configured prototype policy |
| Agent registry | List agents and validate bounded actions |
| Placement | Score CPU/GPU VM candidates against SLO, cost, and capacity |
| Deployment plan | Generate Kubernetes deployment/control plan |
| Readiness | Combine LLM, placement, manifest, agent review, and guard-readiness results |
| Guard boundary | Keep a standalone bounded-action validator in `go/aiops-guard` |

## Guard Relationship

`service-control-api` performs LLM selection, agent registry validation,
placement, deployment-plan generation, manifest dry-run, and readiness
reporting.

`aiops-guard` is a standalone bounded-action validator. The service-control
readiness response includes `guard_validation` to show that the Go guard backend
and recovery context are prepared. Full runtime wiring from `service-control-api`
to the standalone guard CLI is a planned next step, not a completed production
integration.

## Safety Principle

The runtime model can recommend or justify an operation, but the Go layer owns
deterministic validation before an action becomes executable.
