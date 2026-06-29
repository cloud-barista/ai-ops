# 에이전트 등록 관리 프로토타입

영문 제목: Agent Registration Management Prototype

## 1. 목적

이 문서는 1차년도 Go 기반 service-control framework에 구현된 AI 에이전트 등록 관리 프로토타입을 설명합니다. 목적은 AI 에이전트를 어떻게 표현하는지, 허용 가능한 action boundary를 어떻게 등록하는지, Go service-control 계층이 agent action을 수용 가능한 서비스 제어 판단으로 처리하기 전에 어떻게 검증하는지 정의하는 것입니다.

본 문서는 공식 설계 산출물 원본입니다. 에이전트 registry JSON 파일, Go service-control 구현, API/CLI 검증 출력과 함께 검토해야 합니다.

## 2. 프로토타입 범위

프로토타입 범위는 다음과 같습니다.

- 에이전트 metadata 등록
- 에이전트 역할과 책임 정의
- bounded action list 관리
- 향후 평가를 위한 reward signal 문서화
- 등록 에이전트와 action에 대한 Go CLI/API 검증
- 통합 service-operations readiness report와의 연계

현재 프로토타입은 완전한 autonomous multi-agent orchestration을 구현하지 않습니다. 자율 서비스 제어 agent가 안전하게 동작하기 전에 필요한 등록 및 bounded-action validation foundation을 제공합니다.

## 3. Registry 설정

에이전트 registry 파일은 다음 위치에 있습니다.

```text
config/agent_registry.json
```

각 agent entry는 다음 정보를 포함합니다.

- `name`
- `korean_name`
- `role`
- `responsibilities`
- `bounded_actions`
- `reward_signals`
- `enabled`

registry는 검토 가능한 configuration contract로 취급합니다. Go 로직은 registry를 읽고 agent 존재 여부와 요청 action이 해당 agent의 허용 action set에 포함되는지 검증합니다.

## 4. 등록 에이전트

| Agent | 주요 책임 |
| --- | --- |
| `AIServiceHASupportAgent` | 서비스 가용성과 recovery 필요성 검토 |
| `AIApplicationManagementAgent` | AI 응용 배포 및 제어 action 검토 |
| `AISemiconductorInfraOpsAgent` | CPU/GPU VM 및 인프라 제약 검증 |
| `CostOptimizationAgent` | 비용과 resource efficiency 영향 검토 |

### AIServiceHASupportAgent

서비스 가용성 신호를 확인하고 recovery 관련 action 필요성을 판단합니다. 현재 프로토타입에서는 recovery를 직접 실행하기보다 HA support 관점을 표현합니다.

### AIApplicationManagementAgent

AI 응용 배포와 제어 판단을 담당합니다. inference VM 선택, deployment 계획, deployment scale 조정, service metric 관찰과 같은 application-level action을 제안할 수 있습니다.

### AISemiconductorInfraOpsAgent

인프라와 AI semiconductor resource 관점을 나타냅니다. 1차년도 프로토타입에서는 CPU/GPU VM 제약, accelerator 요구, latency, throughput, memory, capacity에 초점을 둡니다. 향후 GPU/NPU cluster orchestration으로 확장할 수 있습니다.

### CostOptimizationAgent

resource usage와 cost implication을 검토합니다. 불필요한 GPU 사용이나 과도한 scale-out 같은 고비용 action을 제한하는 데 사용합니다.

## 5. Bounded Action

Bounded action은 각 agent가 승인하거나 제안할 수 있는 action을 정의합니다. 이는 안전성을 위한 설계 선택입니다. LLM 또는 agent가 arbitrary infrastructure command를 자유롭게 생성하도록 두지 않고, 요청 action이 선택 agent의 allowed action list에 포함되는지 먼저 확인합니다.

예를 들어 `AIApplicationManagementAgent`는 `app_scale_deployment`, `app_plan_deployment`와 같은 application-level action을 검증할 수 있고, `AISemiconductorInfraOpsAgent`는 infrastructure placement 관련 action을 담당합니다. action이 선택 agent의 범위에 없으면 Go validation function은 false를 반환합니다.

## 6. Reward Signal

Reward signal은 각 agent가 무엇을 최적화하거나 피해야 하는지 설계 수준에서 표현합니다. 현재 프로토타입에서는 reinforcement learning model 학습에 사용하지 않습니다. 대신 agent별 평가 방향을 문서화합니다. 예를 들어 service HA agent는 recovery 필요성 판단이 실제 incident state와 일치할 때 positive reward를 받고, cost optimization agent는 불필요한 고비용 resource 사용을 승인할 때 penalty를 받는 식입니다.

Reward signal은 향후 평가와 learning extension을 위한 설계 산출물이며, 완료된 RL training result가 아닙니다.

## 7. Go 구현

| 구성요소 | 경로 |
| --- | --- |
| 에이전트 registry 설정 | `config/agent_registry.json` |
| Go 서비스 로직 | `go/service-control-api/internal/api/service.go` |
| API/CLI model | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |

Go 구현은 registry loading, agent lookup, action validation, integrated service-operation review output을 제공합니다.

## 8. CLI 검증

에이전트 목록 조회:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

단일 에이전트 조회:

```bash
go run ./cmd/aiops-service-control show-agent \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent
```

Action 검증:

```bash
go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent \
  --action app_scale_deployment
```

기대 신호:

```text
valid = true
```

## 9. API 연동

| 기능 | API path |
| --- | --- |
| 등록 에이전트 목록 조회 | `GET /api/v1/agents` |
| 통합 서비스 운영 준비도 실행 | `POST /api/v1/service-operations/run` |

통합 service-operations 응답에는 `agent_reviews`가 포함되며, application, infrastructure, cost 검토 관점을 함께 보고합니다.

## 10. 설계 경계

현재 프로토타입은 완전한 autonomous agent orchestration을 주장하지 않습니다. 자율 서비스 제어 agent가 안전하게 작동하기 전에 필요한 registration과 bounded-action validation foundation을 제공합니다. 향후 multi-agent planning, real operation metrics, reinforcement learning feedback, runtime policy governance와 연결할 수 있습니다.

## 11. 관련 산출물

| 산출물 | 경로 |
| --- | --- |
| 에이전트 registry 설정 | `config/agent_registry.json` |
| 에이전트 registry 가이드 | `docs/design/agent_registry_guide.md` |
| 에이전트 action/reward policy | `docs/design/agent_action_reward_policy.md` |
| Go 서비스 로직 | `go/service-control-api/internal/api/service.go` |
| API/CLI model | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| 기능/API 가이드 | `docs/submission/functional_api_guide.md` |
| 테스트 가이드 | `docs/submission/test_guide.md` |
