# Research Task Integration Design

## Target Scope

The integrated research task is a Go-based AI service-control and management
automation framework.

| Research item | Prototype implementation |
| --- | --- |
| Ops analysis and optimal LLM selection | `go/service-control-api` LLM selection logic |
| AI LLM operation-management structure | service-operations readiness pipeline |
| AI agent registration management | `config/agent_registry.json` plus Go API/CLI validation |
| CPU/GPU VM AI application deployment/control | CPU/GPU placement and deployment-plan generation |

## System Flow

```text
Ops policy/config
-> Go LLM selection
-> Agent registry and bounded-action validation
-> CPU/GPU VM placement
-> Kubernetes deployment plan and manifest dry-run
-> service operations readiness report
```

## Development Boundary

The submission/demo path does not include non-core experiment runners, external
orchestration-framework integrations, or post-processing tooling. These were
removed so the implementation message remains aligned with the Go development
language requirement.
