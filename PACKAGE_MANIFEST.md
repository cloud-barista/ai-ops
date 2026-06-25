# ETRI Delivery Package Manifest

This package is scoped to the Go-based AI service-control and management
automation prototype.

## Included

| Path | Purpose |
| --- | --- |
| `go/service-control-api/` | Go Echo API and Go CLI for LLM selection, agent registry, CPU/GPU placement, and service-operations readiness |
| `go/aiops-guard/` | Go bounded-action guard used by service-control execution paths |
| `config/agent_registry.json` | Agent registry and bounded-action metadata |
| `config/ops_llm_benchmark.json` | Ops LLM candidate metrics and selection policy weights |
| `config/inference_optimization.json` | CPU/GPU VM resource profiles and workload requirements |
| `docs/design/` | Go-centered design notes |
| `docs/submission/` | Submission-facing guides, API contract, and requirement mapping |

## Excluded

| Excluded item | Reason |
| --- | --- |
| Non-core legacy code and tests | Removed so the submission/demo package is Go-centered |
| External benchmark/orchestration integrations | Experimental paths that blur the ETRI deliverable scope |
| Provider-specific monitoring adapters | Removed in favor of provider-neutral Ops input/config |
| Local cluster experiment manifests and helper tooling | Environment-specific checks, not core deliverables |
| `runs/` | Local execution artifacts and validation outputs |
| virtualenv/cache/build artifacts | Local generated artifacts |
| `.env`, kubeconfig, API keys | Sensitive local credentials |

## Submission Scope

```text
AI LLM 운영관리 구조 설계 및 프로토타입
AI 에이전트 등록관리 프로토타입
CPU/GPU VM 기반 AI 응용 배포/제어 추론 최적화 전략
Go 기반 API/CLI 구현 및 검증
```
