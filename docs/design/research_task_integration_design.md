# Research Task Integration Design

## Target Scope

The integrated research task is implemented as a Go-based prototype for an AI
service-control and management automation framework. It focuses on the 1st-year
deliverables assigned to the service-control layer.

| Research item | Prototype implementation |
| --- | --- |
| Ops analysis and optimal LLM selection | `go/service-control-api` LLM selection logic |
| AI LLM operation-management structure | Service-operations readiness pipeline |
| AI agent registration management | `config/agent_registry.json` plus Go API/CLI validation |
| CPU/GPU VM AI application deployment/control | CPU/GPU placement and Kubernetes deployment-plan generation |
| Safety boundary | `go/aiops-guard` standalone bounded-action validator |

## System Flow

```text
Ops policy/config
-> Go LLM selection
-> Agent registry and bounded-action validation
-> CPU/GPU VM placement recommendation
-> Kubernetes deployment/control plan
-> manifest dry-run and guard-readiness check
-> service-operations readiness report
```

## AI-Infra Boundary

CB-Tumblebug or other AI-Infra components are treated as external VM
provisioning and management infrastructure. This project does not replace those
systems. It consumes CPU/GPU VM resource assumptions from configuration and
produces AI application placement and deployment-control decisions on top of
that infrastructure boundary.

## Development Boundary

The submission/demo path does not include non-core experiment runners, external
agent orchestration frameworks, provider-specific monitoring adapters, or local
cluster helper scripts. This keeps the implementation aligned with the Go
development-language requirement and the assigned research scope.
