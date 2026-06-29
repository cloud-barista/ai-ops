# 에이전트 등록 관리 프로토타입

영문 제목: Agent Registration Management Prototype

## 1. 한눈에 보는 구조

| 항목 | 내용 |
| --- | --- |
| 목적 | AI 에이전트의 역할, 책임, 허용 action을 등록하고 검증한다. |
| 입력 | `config/agent_registry.json` |
| 처리 | agent 조회, action boundary 확인, 승인 여부 판단 |
| 출력 | agent list, agent detail, action validation result |
| 검증 | Go CLI/API로 registry와 action 검증 결과를 재현 |

## 2. Registry 구성

| 필드 | 의미 |
| --- | --- |
| `name` | agent 식별자 |
| `korean_name` | 한글 agent 이름 |
| `role` | 담당 역할 |
| `responsibilities` | 책임 범위 |
| `bounded_actions` | 허용 action 목록 |
| `reward_signals` | 향후 평가 기준 |
| `enabled` | 사용 여부 |

## 3. 등록 에이전트

| Agent | 주요 역할 |
| --- | --- |
| `AIServiceHASupportAgent` | 서비스 가용성과 recovery 필요성 검토 |
| `AIApplicationManagementAgent` | AI 응용 배포·제어 action 검토 |
| `AISemiconductorInfraOpsAgent` | CPU/GPU VM 및 인프라 제약 검증 |
| `CostOptimizationAgent` | 비용과 resource efficiency 검토 |

## 4. Action 검증 규칙

| 규칙 | 설명 |
| --- | --- |
| 등록 여부 확인 | 요청 agent가 registry에 있어야 한다. |
| 활성 상태 확인 | `enabled=true`인 agent만 사용할 수 있다. |
| action boundary 확인 | 요청 action이 `bounded_actions`에 포함되어야 한다. |
| 결과 설명 | 승인 여부와 사유를 함께 반환해야 한다. |

## 5. 검증 방법

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json

go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent \
  --action app_scale_deployment
```

기대 신호:

```text
valid = true
```

## 6. 설계 경계

- 완전한 autonomous multi-agent orchestration을 구현한 문서가 아니다.
- LLM이 임의 infrastructure command를 직접 실행하지 못하도록 action boundary를 둔다.
- reward signal은 향후 평가 방향을 위한 설계 정보이며 RL 학습 결과가 아니다.
- 실제 운영 연결 전에는 registry 검증과 Go guard 검증을 통과해야 한다.
