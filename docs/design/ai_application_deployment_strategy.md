# AI 응용 배포·제어 handoff 전략

## 목적

실제 VM 적합성 결과를 AI 응용 배포·제어 실행 주체가 처리할 수 있는 비실행 계획으로 변환합니다. 본 service-control 계층은 특정 팀 프레임워크를 고정하지 않습니다.

## 연결 계약

외부 에이전트는 registry에 다음 정보를 등록합니다.

- endpoint와 invocation path
- capability: `ai_application_deployment_control`
- bounded actions: `deploy_application`, `observe_status`, `restart_application`, `stop_application`
- 역할과 책임, 상태, version

실제 LLM이 workload 관측값을 바탕으로 이 목록 중 하나를 제안합니다. service-control은 capability와 Action이 모두 일치하는 enabled 외부 실행 주체를 찾아 `selected_executor`로 기록합니다. 일치 실행 주체가 없으면 `pending_executor`로 남기며 실행하지 않습니다.

## 계획 생성

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control plan-ai-application-control \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference
```

## 책임 경계

| 계층 | 책임 |
| --- | --- |
| Service-control | LLM 선정, registry, 실제 VM 검증, Action 경계, handoff 계획 |
| 인프라 계층 | VM 생성·조회·삭제와 실제 자원 정보 제공 |
| 등록 실행 에이전트 | 배포·제어 실행과 결과 feedback 제공 |

출력의 `execution_status=not_executed`는 계획 생성과 실제 실행을 구분하기 위한 필수 신호입니다.
