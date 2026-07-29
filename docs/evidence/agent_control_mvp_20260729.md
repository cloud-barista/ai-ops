# AI 응용 자동화 에이전트 MVP 실행 증적

## 1. 시험 범위

2026-07-29에 geon Agent Control의 다음 흐름을 로컬에서 실행했다.

```text
Application Context
+ Resource Recommendation
→ 배포 계획
→ DEPLOY / REJECT / RETRY
→ Go Guard
→ deployment.create.request + DeploymentManifest
→ Qwen 추론 비교
→ 배포 상태와 최적화 Feedback
→ 성공·실패 원인 요약
```

AppDeploy와 실제 VM은 사용하지 않았다. 이 시험은 geon이 담당하는 배포 결정과 Manifest 생성, 검증, Feedback 분석 범위만 검증한다.

## 2. 시험 환경

| 항목 | 값 |
| --- | --- |
| geon | `http://127.0.0.1:18080/` |
| Qwen Provider | Ollama OpenAI-compatible API |
| Qwen Model | `qwen3.5:4b` |
| Candidate ID | `qwen3.5-ops-planner` |
| Common JSON | `1.0` |

## 3. 실행 결과

| 검증 항목 | 결과 |
| --- | --- |
| correlation_id | `flow-demo-001` |
| Application Context 수신 | 성공 |
| Resource Recommendation 수신 | 성공 |
| 최소 동작 결정 | `DEPLOY` |
| Flow 상태 | `DEPLOY_APPROVED` |
| 선택 후보 | `candidate-demo-001` |
| Go Guard | `APPROVED` |
| 출력 메시지 | `deployment.create.request` |
| 규칙 기반 결정 | `DEPLOY` |
| Qwen 원시 제안 | `DEPLOY` |
| 검증된 Qwen 제안 | `DEPLOY` |
| Qwen 제안 Guard | `APPROVED` |
| Qwen 응답 지연시간 | `21702 ms` |
| 배포 상태 | `RUNNING` |
| Feedback outcome | `SUCCEEDED` |
| 추론 p95 지연시간 | `1480 ms` |
| 처리량 | `6.2 rps` |
| 오류율 | `0.2%` |
| SLO 위반 | 없음 |

자동 원인 요약:

```text
Deployment succeeded and the reported metrics satisfy the SLO.
```

## 4. 검증 명령

```bash
cd go/service-control-api
go test ./... -count=1
go vet ./...
```

웹에서는 **자동화 실행** 화면의 두 입력을 순서대로 전송한 후, **추론 비교 · 배포 Feedback 실험**에서 비교 실행, 배포 상태 전송, 성능 Feedback 전송 순서로 재현한다.
