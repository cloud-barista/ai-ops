# Service Control API

## 역할

이 서비스는 경희대학교 AI 어플리케이션 자동화 에이전트 PoC의 Go 실행 모듈입니다.

```text
Application Context
+ Resource Recommendation
→ Agent Registry authorization
→ DEPLOY / REJECT / RETRY
→ Go Guard
→ Desired Deployment Spec
→ deployment.status.changed
→ optimization.feedback.created
→ NO_ACTION / SCALE_OUT / SCALE_IN
```

핵심 Flow는 AppDeploy, VM, Kubernetes 없이 로컬에서 독립 실행할 수 있습니다. 실제 배포와 플랫폼 전용 변환은 외부 시스템의 책임입니다.

## 실행

Windows Git Bash에서 저장소 내부로 이동한 뒤 실행합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
export AIOPS_REPO_ROOT="$(git rev-parse --show-toplevel)"
export AIOPS_BIND_ADDRESS="127.0.0.1"
export PORT=18080

cd "$AIOPS_REPO_ROOT/go/service-control-api"
go mod download
go run ./cmd/service-control-api
```

상태 확인:

```bash
curl http://127.0.0.1:18080/healthz
```

웹:

```text
http://127.0.0.1:18080/
```

OpenAPI:

```text
http://127.0.0.1:18080/openapi.yaml
```

## 3개 화면

### 자동화 에이전트

핵심 실험의 시작 화면입니다. `Application Context`와 `Resource Recommendation`을 하나의 폼에서 실행합니다.

1. 요구 분석 결과 수신
2. 인프라 추천 결과 수신
3. `AIApplicationAutomationAgent` 권한 확인
4. `DEPLOY`, `REJECT`, `RETRY` 판단
5. Go Guard 검증
6. Desired Deployment Spec 생성

기본 샘플은 같은 `flow-demo-001` correlation ID와 `profile-demo-001` profile ID를 사용합니다. **배포 판단 실행**을 한 번 누르면 여섯 단계가 순서대로 연결됩니다.

정상 샘플의 핵심 결과:

```json
{
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

### Agent 및 정책

Agent Registry의 현재 정책을 조회합니다.

| 항목 | 기본 값 |
| --- | --- |
| Agent | `AIApplicationAutomationAgent` |
| Capability | `ai_application_automation` |
| Bounded Action | `generate_deployment_decision` |
| 상태 | `enabled` |

Runtime Agent 등록은 capability와 bounded action 정책을 시험하기 위한 보조 기능입니다. 설정 파일의 기본 Agent는 웹에서 삭제할 수 없고, 현재 프로세스에 등록한 Runtime Agent만 삭제할 수 있습니다.

### 실험 결과

모든 Agent Control Flow를 시간순으로 보여 줍니다.

- 판단과 Guard 근거 조회
- Desired Deployment Spec 조회
- 규칙 기반·Qwen·Qwen+Guard 추론 비교
- 배포 상태와 성능 Feedback 연결
- SLO 기반 스케일링 판단
- 개별 Flow 삭제와 전체 Flow 삭제

SLO 위반 샘플은 p95 지연시간을 `2600 ms`로 설정합니다. 기본 SLO `2000 ms`를 초과하고 최대 replica가 2이므로 다음 결과를 확인할 수 있습니다.

```json
{
  "action": "SCALE_OUT",
  "current_replicas": 1,
  "desired_replicas": 2,
  "evidence": ["latency_p95_ms"]
}
```

## Qwen 비교 실험

핵심 결정적 판단에는 Ollama가 필수가 아닙니다. **실험 결과 → 추론 방식 비교**에서만 Qwen endpoint를 호출합니다.

```bash
ollama pull qwen3.5:4b
ollama list
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
```

기본 후보 설정:

```text
config/ops_llm_eval_candidates.local_ollama.json
```

환경 변수 없이 서버를 시작하면 제출용 기본 후보 설정을 사용하므로 로컬 후보가 비활성화될 수 있습니다. 로컬 비교 실험에서는 위 환경 변수를 설정한 뒤 서버를 시작합니다. Ollama가 꺼져 있으면 규칙 기반 결과는 유지되고 Qwen 실행 상태만 `provider_unavailable`로 기록됩니다. 이를 성공 결과로 위장하지 않습니다.

## API 순서

### 핵심 배포 판단

```text
POST /api/v1/agent-control/application-contexts
POST /api/v1/agent-control/resource-recommendations
GET  /api/v1/agent-control/flows/{correlation_id}
```

두 POST 요청의 `correlation_id`, `trace_id`, `profile_id`가 일치해야 합니다.

### 배포 후 Feedback

```text
POST /api/v1/agent-control/deployment-status
POST /api/v1/agent-control/optimization-feedback
GET  /api/v1/agent-control/flows/{correlation_id}
```

`deployment.status.changed`가 `RUNNING`이고 최적화 Feedback에 SLO 위반이 있으면 replica 상한 안에서 `SCALE_OUT`을 판단합니다. 판단만 생성하며 실제 스케일링 명령은 실행하지 않습니다.

### 실험 기록 삭제

```text
DELETE /api/v1/agent-control/flows/{correlation_id}
DELETE /api/v1/agent-control/flows
```

Flow는 현재 프로세스 메모리에 저장됩니다. 서버를 재시작하면 초기화됩니다.

### Agent Registry

```text
GET    /api/v1/agents
POST   /api/v1/agents
DELETE /api/v1/agents/{name}
```

## 호환 API

기존 연구·통합 시험을 위한 ControlRun, AppDeploy 제출, Agent Dispatcher, Autonomous Loop API는 백엔드 호환성을 위해 유지합니다. 간소화된 웹에서는 노출하지 않으며 핵심 Agent Control Flow의 필수 단계도 아닙니다.

## 테스트

```bash
go test ./... -count=1
go vet ./...
```

Swagger 재생성:

```bash
cd ../..
make swag
```

Windows에서 `make`가 없으면 저장소 루트 Makefile의 `swag` 명령을 동일하게 실행합니다.
