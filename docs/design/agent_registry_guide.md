# 에이전트 Registry 가이드

## 목적

Agent registry는 service-control prototype에서 사용하는 AI agent를 정의합니다. 각 agent의 role, responsibility boundary, allowed action, reward signal을 포함합니다. Go API/CLI가 결정론적으로 조회하고 검증할 수 있도록 JSON으로 표현합니다.

설정 파일:

```text
config/agent_registry.json
```

## 등록 에이전트

| Agent | 역할 |
| --- | --- |
| `AIServiceHASupportAgent` | service health, availability, recovery need 탐지 |
| `AIApplicationManagementAgent` | AI application deployment/control action 제안 |
| `AISemiconductorInfraOpsAgent` | CPU/GPU VM 및 infrastructure constraint 검증 |
| `CostOptimizationAgent` | cost와 resource-efficiency implication 검토 |

## Bounded Action

각 agent에는 명시적인 `bounded_actions` list가 있습니다. Go service-control layer는 이 list를 사용해 선택 agent의 범위에 속하지 않는 action을 거부합니다. 이는 service-control action을 ready로 취급하기 전의 prototype-level safety boundary입니다.

## Go CLI

등록 agent 목록:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

단일 agent 조회:

```bash
go run ./cmd/aiops-service-control show-agent \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent
```

Agent action 검증:

```bash
go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent \
  --action app_scale_deployment
```

## API Path

| 기능 | Path |
| --- | --- |
| Agent list | `GET /api/v1/agents` |
| Integrated readiness report | `POST /api/v1/service-operations/run` |
