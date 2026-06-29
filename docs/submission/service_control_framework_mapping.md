# Service-Control Framework Mapping

## Mapping To Research Scope

| Research scope | Official design deliverable | Go implementation |
| --- | --- |
| AI LLM operation-management design | `docs/deliverables/01_llm_operation_management_design.md` | Ops LLM policy ranking and runtime candidate selection |
| AI agent registration management | `docs/deliverables/02_agent_registration_management_prototype.md` | Agent registry plus bounded-action validation |
| AI application automation agent design | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | Application, infrastructure, and cost review outputs |
| CPU/GPU VM-based AI application deployment/control | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` | Placement recommendation and deployment-plan generation |
| Safety validation | `docs/submission/test_guide.md` | Standalone `aiops-guard` plus service-control guard-readiness response |

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

The mapping is a 1st-year functional prototype mapping. It does not claim
production readiness, final standardized LLM benchmark completion, or actual GPU
VM provisioning in the default validation path.
