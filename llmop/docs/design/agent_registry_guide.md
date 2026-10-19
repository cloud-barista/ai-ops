# 에이전트 Registry 가이드

## 목적

Agent Registry는 LLM Deployment Planner의 역할, capability 및 bounded Action을 Go 코드가 검증할 수 있도록 관리합니다.

설정 파일:

```text
config/agent_registry.json
```

## 핵심 내부 에이전트

| Agent | 역할 | 허용 Action |
| --- | --- | --- |
| `AIApplicationAutomationAgent` | 자연어 App 요구를 분석해 AppDeploy Manifest 생성·전달 | `generate_deployment_manifest`, `submit_deployment_manifest`, `observe_deployment_status` |

VM 자원 적합성은 보조 Go validator가 검사합니다. 실제 Target과 Runtime Adapter 선택은 AppDeploy의 책임이며, 이를 별도 Planner 에이전트로 가장하지 않습니다.

## AppDeploy 연계

Go Guard를 통과한 Manifest는 고정된 AppDeploy 계약으로 전달됩니다.

1. `POST /api/v1/deployments`로 배포 요청
2. `GET /api/v1/deployments/{deployment_id}`로 상태 조회
3. `GET /api/v1/deployments/{deployment_id}/logs`로 로그 조회

`POST /api/v1/agents` 기반 runtime 등록과 범용 handoff API는 다른 실행 주체와의 연구 연계를 위한 호환 경로입니다. AppDeploy Planner의 주 실행 경로에는 runtime agent 등록이 필요하지 않습니다.

## CLI

```bash
cd go/service-control-api

go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json

go run ./cmd/aiops-service-control show-agent \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationAutomationAgent

go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationAutomationAgent \
  --action generate_deployment_manifest
```

## 상태 해석

| 상태 | 의미 |
| --- | --- |
| `executed` | 실제 LLM 호출과 Manifest 생성 수행 |
| `MANIFEST_REJECTED` | LLM 응답 또는 Manifest Go Guard 검증 실패 |
| `APPDEPLOY_REQUEST_FAILED` | AppDeploy 배포 요청 실패 |
| `POLL_TIMEOUT` | 제한된 횟수 안에 terminal status를 확인하지 못함 |
| `RUNNING` | AppDeploy가 실제 배포를 RUNNING으로 보고하고 로그 조회까지 완료 |

`pending_executor`, `not_executed` 등은 기존 범용 Action handoff API의 상태이며 AppDeploy Planner 결과 상태가 아닙니다.
