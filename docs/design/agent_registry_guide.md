# AI Agent 등록관리 프로토타입 가이드

## 목적

Agent registry는 서비스 제어 프레임워크에서 사용할 AI 에이전트의 역할,
책임, 허용 action, reward signal을 Go API/CLI가 읽을 수 있는 JSON 계약으로
관리한다.

이 기능은 다음 연구 항목에 대응한다.

- AI 에이전트 등록관리 프로토타입 개발
- Agent별 action 범위 명시
- Agent별 reward signal 명시
- 운영관리 구조에서 에이전트 책임 경계 정의

## 기본 Agent

현재 registry에는 4개 Agent가 등록되어 있다.

| Agent | 역할 |
| --- | --- |
| `AIServiceHASupportAgent` | 서비스 이상 징후, 가용성, 복구 필요성 판단 |
| `AIApplicationManagementAgent` | AI 응용 배포/제어 action 제안 |
| `AISemiconductorInfraOpsAgent` | CPU/GPU VM 및 인프라 제약 검토 |
| `CostOptimizationAgent` | 비용/자원 효율성 관점의 action 승인 |

설정 파일:

```text
config/agent_registry.json
```

## Go CLI

등록 Agent 목록 확인:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

특정 Agent 확인:

```bash
go run ./cmd/aiops-service-control show-agent \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent
```

Agent action 허용 여부 확인:

```bash
go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent \
  --action app_scale_deployment
```

## API 대응

| 기능 | 경로 |
| --- | --- |
| Agent 목록 조회 | `GET /api/v1/agents` |
| Agent 기반 통합 readiness | `POST /api/v1/service-operations/run` |

## 제출 산출물 관점

Agent registry는 단순 설정 파일이 아니라, LLM 운영관리 구조에서 Agent 책임과
action 경계를 검증하는 핵심 산출물이다. Go 구현은 registry를 읽어 Agent
목록을 노출하고, 요청된 action이 해당 Agent의 허용 범위에 있는지 검증한다.
