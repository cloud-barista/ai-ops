# ETRI Delivery Package Manifest

This package is scoped to the Go-based AI service-control and management
automation prototype.

## Included

| Path | Purpose |
| --- | --- |
| `go/service-control-api/` | Go Echo API and Go CLI for LLM selection, agent registry, CPU/GPU placement, deployment-plan generation, and service-operations readiness |
| `go/aiops-guard/` | Standalone Go bounded-action validator for service-control actions |
| `config/agent_registry.json` | Agent registry and bounded-action metadata |
| `config/ops_llm_benchmark.json` | Prototype LLM candidate policy values and selection weights |
| `config/inference_optimization.json` | CPU/GPU VM resource profiles and workload requirements |
| `docs/design/` | Go-centered design notes |
| `docs/submission/` | Submission-facing guides, API contract, requirement mapping, and evaluation summary |

## Excluded

| Excluded item | Reason |
| --- | --- |
| Non-core legacy code and tests | Removed so the submission/demo package is Go-centered |
| External benchmark/orchestration integrations | Experimental paths that blur the assigned deliverable scope |
| Provider-specific monitoring adapters | Removed in favor of provider-neutral Ops input/config |
| Local cluster experiment manifests and helper tooling | Environment-specific checks, not core deliverables |
| `runs/` | Local execution artifacts and validation outputs |
| virtualenv/cache/build artifacts | Local generated artifacts |
| `.env`, kubeconfig, API keys | Sensitive local credentials |

## Submission Scope

```text
AI LLM operation-management structure design and prototype
AI agent registration-management prototype
CPU/GPU VM-based AI application deployment/control inference optimization strategy
Go-based API/CLI implementation and functional validation
```
