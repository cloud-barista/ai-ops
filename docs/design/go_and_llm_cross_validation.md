# Go And LLM Cross Validation

## Goal

The system separates LLM reasoning from deterministic service-control checks.
LLM selection and high-level readiness decisions are represented in config and
API responses, while Go validates bounded actions, placement constraints, and
deployment-plan structure.

## Control Boundary

```text
LLM/model policy
-> selected runtime model
-> registered agent roles
-> bounded action validation
-> CPU/GPU placement constraints
-> deployment manifest dry-run
```

## Go Responsibilities

| Layer | Go responsibility |
| --- | --- |
| LLM selection | rank candidate models under configured policy |
| Agent registry | list agents and validate bounded actions |
| Placement | score CPU/GPU VM candidates against SLO/cost/capacity |
| Deployment plan | generate Kubernetes deployment/control plan |
| Readiness | combine LLM, placement, manifest, and agent review results |

## Safety Principle

The runtime model can recommend or justify an operation, but the Go service
control layer owns deterministic validation before an action becomes executable.
