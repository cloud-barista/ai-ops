# AI 어플리케이션 자동화 에이전트 PoC 실행 증적

## 1. 시험 범위

2026-07-29에 `geon`의 다음 독립 연구 흐름을 실제 API와 브라우저에서 검증했다.

```text
Application Context
+ Resource Recommendation
→ Agent Registry 권한 검증
→ DEPLOY / REJECT / RETRY
→ Go Guard
→ Desired Deployment Spec
→ Qwen 추론 비교
→ 배포 상태와 성능 Feedback
→ 최소 스케일링 판단
```

AppDeploy, 실제 VM, Kubernetes는 사용하지 않았다. 이번 시험은 경희대학교 담당 범위인 배포 판단, 검증, 플랫폼 독립적 요구 스펙, 추론 비교, Feedback 기반 스케일링 판단만 검증한다.

## 2. 시험 환경

| 항목 | 값 |
| --- | --- |
| 검증 서버 | `http://127.0.0.1:18081/` |
| 공식 기본 포트 | `18080` |
| 검증 포트를 분리한 이유 | `18080`에 이전 사용자 프로세스가 실행 중이어서 해당 프로세스를 중단하지 않음 |
| Qwen Provider | Ollama OpenAI-compatible API |
| Qwen Model | `qwen3.5:4b` |
| Candidate ID | `qwen3.5-ops-planner` |
| Common JSON | `1.0` |
| 브라우저 | Playwright + 로컬 Chrome |

서버 시작 시 다음 로컬 후보 설정을 사용했다.

```bash
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
```

## 3. 핵심 Flow 결과

| 검증 항목 | 실제 결과 |
| --- | --- |
| correlation_id | `flow-demo-001` |
| profile_id | `profile-demo-001` |
| Application Context 수신 | 성공 |
| Resource Recommendation 수신 | 성공 |
| Automation Agent | `AIApplicationAutomationAgent` |
| Registry capability | `ai_application_automation` |
| Registry bounded action | `generate_deployment_decision` |
| Agent Registry 권한 | 승인 |
| 최소 동작 결정 | `DEPLOY` |
| Flow 상태 | `DEPLOY_APPROVED` |
| 선택 후보 | `candidate-demo-001` |
| Go Guard | `APPROVED` |
| 완료 단계 | `6 / 6` |
| 출력 | 플랫폼 독립적 Desired Deployment Spec |

핵심 결과 구조:

```json
{
  "correlation_id": "flow-demo-001",
  "state": "DEPLOY_APPROVED",
  "agent_authorization": {
    "agent_name": "AIApplicationAutomationAgent",
    "capability": "ai_application_automation",
    "action": "generate_deployment_decision",
    "authorized": true
  },
  "decision": {
    "action": "DEPLOY",
    "selected_candidate_id": "candidate-demo-001"
  },
  "guard": {
    "status": "APPROVED"
  },
  "desired_deployment_spec": {
    "manifest_version": "1.0"
  }
}
```

## 4. 추론 비교

| 비교 방식 | 실제 결과 |
| --- | --- |
| 규칙 기반 | `DEPLOY` |
| Qwen 원시 제안 | `DEPLOY · executed` |
| Qwen + Go Guard | `DEPLOY · APPROVED` |
| 실제 모델 | `qwen3.5:4b` |
| Provider | `local-openai-compatible` |
| Qwen 지연시간 | `3554 ms` |

Qwen은 등록된 `candidate-demo-001`을 선택했고, Go Guard가 동일 후보와 `DEPLOY` 동작을 승인했다.

## 5. Feedback과 스케일링 결과

SLO 위반 샘플은 다음 값을 사용했다.

| 항목 | 값 |
| --- | --- |
| Deployment 상태 | `RUNNING` |
| 현재 replica | `1` |
| replica 상한 | `2` |
| p95 지연시간 | `2600 ms` |
| p95 SLO | `2000 ms` |
| SLO 위반 | `latency_p95_ms` |

실제 스케일링 판단:

```json
{
  "action": "SCALE_OUT",
  "current_replicas": 1,
  "desired_replicas": 2,
  "reason": "SLO evidence requires one bounded replica increase.",
  "evidence": [
    "latency_p95_ms"
  ]
}
```

이 결과는 실행 명령이 아니라 외부 배포기가 사용할 수 있는 **제한된 스케일링 판단**이다.

## 6. 웹 및 삭제 검증

| 검증 항목 | 실제 결과 |
| --- | --- |
| 기본 화면 수 | `3` |
| 사이드 메뉴 | 자동화 에이전트, Agent 및 정책, 실험 결과 |
| 개별 Flow 삭제 전 | `1` |
| 개별 Flow 삭제 후 | `0` |
| 삭제 후 안내 | `저장된 실험 결과가 없습니다.` |
| 데스크톱 viewport | `1440 px` |
| 데스크톱 document 폭 | `1440 px` |
| 모바일 viewport | `390 px` |
| 모바일 document 폭 | `390 px` |
| Agent 표 | 문서 폭을 늘리지 않고 표 내부에서만 가로 스크롤 |
| 브라우저 콘솔 오류 | 없음 |

브라우저 QA 이미지는 로컬 `tmp/simplified-web-qa/`에 생성했다.

- `desktop-flow.png`
- `desktop-results-final.png`
- `mobile-core.png`
- `mobile-agents-fixed.png`
- `mobile-results.png`

## 7. 코드 검증

```bash
cd go/service-control-api
go test ./... -count=1
go vet ./...
```

실행 결과:

```text
go test ./... -count=1  PASS
go vet ./...            PASS
JavaScript syntax       PASS
```

Swagger는 `internal/agentcontrol` 모델 경로를 포함해 다시 생성했고, Flow 삭제 operation과 `ScalingDecision` schema를 확인했다.
