# Service-Control Framework Mapping

## Mapping To Research Scope

| Research scope | Go implementation |
| --- | --- |
| AI LLM operation-management design | Ops LLM policy ranking and runtime candidate selection |
| AI agent registration management | Agent registry plus bounded-action validation |
| AI application automation agent design | Application, infrastructure, and cost review outputs |
| CPU/GPU VM-based AI application deployment/control | Placement recommendation and deployment-plan generation |
| Safety validation | Standalone `aiops-guard` plus service-control guard-readiness response |

## Pipeline

```text
config/ops_llm_benchmark.json
-> select-ops-llm
-> config/agent_registry.json
-> validate-agent-action
-> config/inference_optimization.json
-> recommend-inference-placement
-> plan-inference-deployment
-> run-service-operations
```

## Safety Boundary

The Go layer validates the selected action and deployment plan before a service
operation is considered ready. The default team validation uses `mock` mode and
does not require cluster credentials.

`aiops-guard` remains a standalone bounded-action validator. Full runtime
wiring between `service-control-api` and `aiops-guard` is a planned next step.
