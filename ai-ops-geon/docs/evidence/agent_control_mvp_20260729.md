# AI 어플리케이션 자동화 에이전트 PoC 실행 증적

## 1. 시험 범위

2026-07-29에 `geon`의 다음 독립 연구 흐름을 실제 API와 브라우저에서 검증했다.

```text
자연어 요청 또는 구조화 App Spec
→ Requirement Analyzer
→ ApplicationProfile
→ Mock Resource Recommender
→ ResourceRecommendation
→ Agent Registry 권한 검증
→ DEPLOY / REJECT / RETRY
→ Go Guard
→ DesiredDeploymentSpec
→ Qwen 추론 비교
→ 배포 상태와 성능 Feedback
→ 최소 스케일링 판단
```

AppDeploy, 실제 VM, Kubernetes는 사용하지 않았다. 이번 시험은 경희대학교 담당 범위인 배포 판단, 검증, 플랫폼 독립적 요구 스펙, 추론 비교, Feedback 기반 스케일링 판단만 검증한다.

## 2. 시험 환경

| 항목 | 값 |
| --- | --- |
| 검증 서버 | `http://127.0.0.1:18083/` |
| 공식 기본 포트 | `18080` |
| 검증 포트를 분리한 이유 | `18080`~`18082`의 기존 사용자·WSL 프로세스를 중단하지 않음 |
| 기본 Requirement Analyzer | `local_rule` |
| Resource Recommender | `mock_catalog` |
| Mock Catalog | `config/mock_resource_catalog.json` |
| Qwen Provider | Ollama OpenAI-compatible API |
| Qwen Model | `qwen3.5:4b` |
| Candidate ID | `qwen3.5-ops-planner` |
| Common JSON | `1.0` |
| 브라우저 | Playwright + 로컬 Chrome |

자동 3단계 Flow는 Ollama 없이 `local_rule`로 검증했다. 별도 추론 비교 시험에서는 다음 로컬 후보 설정을 사용했다.

```bash
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
```

## 3. 핵심 Flow 결과

| 검증 항목 | 실제 결과 |
| --- | --- |
| run_id | `run-099c1a4be7d8fa0d` |
| correlation_id | `flow-c26b6f994dd59d3d` |
| trace_id | `trace-9df05ef3a4cf99a6` |
| profile_id | `profile-ai-application` |
| 단일 자연어 입력 | 성공 |
| Requirement Analyzer | `local_rule` |
| ApplicationProfile 자동 생성 | 성공 |
| ResourceRecommendation 자동 생성 | 성공 |
| Automation Agent | `AIApplicationAutomationAgent` |
| Registry capability | `ai_application_automation` |
| Registry bounded action | `generate_deployment_decision` |
| Agent Registry 권한 | 승인 |
| 최소 동작 결정 | `DEPLOY` |
| Flow 상태 | `DEPLOY_APPROVED` |
| 선택 후보 | `mock-gpu-l4` |
| Go Guard | `APPROVED` |
| 완료 단계 | `3 / 3` |
| 출력 | 플랫폼 독립적 `DesiredDeploymentSpec` |

핵심 결과 구조:

```json
{
  "run_id": "run-099c1a4be7d8fa0d",
  "status": "COMPLETED",
  "requirement_analysis": {
    "mode": "local_rule",
    "application_profile": {
      "profile_id": "profile-ai-application"
    }
  },
  "resource_recommendation": {
    "resource_recommendation": {
      "selected_candidate_id": "mock-gpu-l4"
    }
  },
  "flow": {
    "state": "DEPLOY_APPROVED",
    "agent_authorization": {"authorized": true},
    "decision": {"action": "DEPLOY"},
    "guard": {"status": "APPROVED"}
  },
  "desired_deployment_spec": {
    "spec_version": "1.0",
    "target_runtime": "VM"
  }
}
```

### 최소 동작 분기

| 입력 조건 | 실제 상태 | 실제 결정 | Guard | DesiredDeploymentSpec |
| --- | --- | --- | --- | --- |
| 충족 가능한 GPU 요구 | `DEPLOY_APPROVED` | `DEPLOY` | `APPROVED` | 생성 |
| `replicas_min=3`, `replicas_max=1` | `REJECTED` | `REJECT` | `REJECTED` | 미생성 |
| Mock 카탈로그를 초과하는 CPU/GPU 요구 | `RETRY_REQUIRED` | `RETRY` | `RETRY_REQUIRED` | 미생성 |

`REJECT`는 `application_profile` 수정 요청을, `RETRY`는 `resource_recommendation` 재생성 요청을 포함했다.

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
| 기본 입력 | 자연어 요청 |
| 대체 입력 | 구조화 App Spec |
| 기본 실행 API 호출 | `POST /api/v1/agent-control/automation-runs` 1회 |
| 3단계 표시 | 요구사항 분석, 인프라 추천, Agent 배포 판단 |
| 중간 결과 | 접힌 증거 영역에서 ApplicationProfile과 ResourceRecommendation 조회 |
| 고급 입력 | Common JSON v1.0 직접 검증 유지 |
| 개별 Flow 삭제 전 | `1` |
| 개별 Flow 삭제 후 | `0` |
| 삭제 후 안내 | `저장된 실험 결과가 없습니다.` |
| 데스크톱 viewport | `1440 px` |
| 데스크톱 document 폭 | `1440 px` |
| 모바일 viewport | `390 px` |
| 모바일 document 폭 | `390 px` |
| Agent 표 | 문서 폭을 늘리지 않고 표 내부에서만 가로 스크롤 |
| 브라우저 콘솔 오류 | 없음 |

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

Swagger는 `internal/agentcontrol` 모델 경로를 포함해 다시 생성했고, AutomationRun 입력·응답, RequirementAnalysisResult, RecommendationResult, DesiredDeploymentSpec schema를 확인했다.
