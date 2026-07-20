# 에이전트 Action 검증 정책

## 목적

등록 에이전트가 선언한 capability와 bounded Action 범위 안에서만 제어 계획을 만들도록 제한합니다.

## 흐름

```text
에이전트 등록
-> capability + bounded Action 확인
-> VM 적합성 결과와 요청 Action 대조
-> Go Guard 경계 검증
-> approved / rejected
-> 승인 시에도 실행 상태는 not_executed
```

## 범용 실행 계약

```text
capability = ai_application_deployment_control
actions = deploy_application | observe_status | restart_application | stop_application
```

LLM은 이 범위 안에서 Action을 제안하고 Go Guard가 최종 검증합니다. 계약을 만족하는 여러 외부 실행 주체가 등록될 수 있으며 특정 팀·제품 이름은 핵심 로직에 고정하지 않습니다.
