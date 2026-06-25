# Service-Control Framework Mapping

## Mapping To Research Scope

| Research scope | Go implementation |
| --- | --- |
| AI LLM 운영관리 설계 | Ops LLM policy ranking and runtime model selection |
| AI 에이전트 등록관리 | Agent registry plus bounded-action validation |
| AI 응용 자동화 에이전트 | Application, infrastructure, and cost review outputs |
| CPU/GPU VM 기반 AI 응용 배포/제어 | Placement and deployment-plan generation |

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
