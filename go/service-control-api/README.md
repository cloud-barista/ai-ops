# Service Control API

## geon Agent Control 실행 가이드

geon Agent Control은 backend `ControlRun`을 중심으로 Agent Registry, Qwen 기반 Manifest 생성, Go Guard 승인·거부, 선택적 AppDeploy 전달, 배포 후 Autonomous Loop와 실행 Feedback을 연결하는 웹 화면입니다. AppDeploy 소스는 수정하지 않으며 두 서버를 별도 프로세스로 실행합니다.

### 구성과 포트

| 구성 요소 | 역할 | 기본 주소 |
| --- | --- | --- |
| AppDeploy | App/Target 등록 및 실제 배포 실행 | `http://127.0.0.1:8080/` |
| Ollama | Qwen `qwen3.5:4b` 추론 | `http://127.0.0.1:11434/` |
| geon Agent Control | Agent 판단·Guard·AppDeploy 연동 | `http://127.0.0.1:18080/` |

### 1. 사전 확인

- Go가 설치되어 있어야 합니다. Go `1.22` 이상을 권장합니다.
- Ollama가 설치되어 있어야 합니다.
- `qwen3.5:4b` 모델이 Ollama에 준비되어 있어야 합니다.
- geon 저장소를 로컬에 준비합니다.
- 실제 배포까지 실행할 때만 AppDeploy 저장소를 준비합니다.

Git Bash에서 Go를 찾지 못하면 다음과 같이 PATH를 추가합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
go version
```

Ollama와 Qwen 모델을 확인합니다.

```bash
ollama list
ollama pull qwen3.5:4b   # 목록에 모델이 없을 때만 실행
```

Git Bash에서 `ollama: command not found`가 나오면 Windows 설치 경로의 실행 파일을 직접 사용합니다.

```bash
"$HOME/AppData/Local/Programs/Ollama/ollama.exe" list
"$HOME/AppData/Local/Programs/Ollama/ollama.exe" pull qwen3.5:4b
```

### 2. AppDeploy 실행(선택)

Manifest 생성만 시험할 때는 이 단계를 생략할 수 있습니다. 실제 Target 선택과 VM 배포까지 시험할 때 AppDeploy 저장소 내부에서 첫 번째 터미널을 열고 실행합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
export APPDEPLOY_ROOT="$(git rev-parse --show-toplevel)"

if [[ -f "$APPDEPLOY_ROOT/AppDeploy/go.mod" ]]; then
  cd "$APPDEPLOY_ROOT/AppDeploy"
  go mod download
  go run ./cmd/web
else
  echo "오류: AppDeploy 저장소 내부에서 이 명령을 실행하세요."
fi
```

다른 터미널에서 상태를 확인합니다.

```bash
curl http://127.0.0.1:8080/api/v1/healthz
```

정상이라면 `http://127.0.0.1:8080/`에서 AppDeploy 웹 화면이 열립니다.

### 3. geon Agent Control 실행

geon 저장소 내부에서 두 번째 터미널을 열고 다음 명령을 실행합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
export AIOPS_REPO_ROOT="$(git rev-parse --show-toplevel)"
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
export AIOPS_PLANNER_GUARD_POLICY_PATH="config/planner_guard_policy.json"
# Manifest 생성만 시험할 때는 아래 변수를 생략할 수 있습니다.
export AIOPS_APPDEPLOY_BASE_URL="http://127.0.0.1:8080/api/v1"
export AIOPS_BIND_ADDRESS="127.0.0.1"
export PORT=18080

if [[ -f "$AIOPS_REPO_ROOT/go/service-control-api/go.mod" ]]; then
  cd "$AIOPS_REPO_ROOT/go/service-control-api"
  go mod download
  go run ./cmd/service-control-api
else
  echo "오류: geon 저장소 내부에서 이 명령을 실행하세요."
fi
```

상태를 확인합니다.

```bash
curl http://127.0.0.1:18080/healthz
```

정상 응답 예시는 다음과 같습니다.

```json
{"service":"service-control-api","status":"ok"}
```

브라우저에서 `http://127.0.0.1:18080/`을 엽니다.

### 4. 화면 사용 순서

1. 실제 배포 시험이면 AppDeploy에 Target과 App을 등록하고 `app_version_id`를 복사합니다. Manifest-only 시험에서는 형식이 유효한 시험 ID를 사용할 수 있습니다.
2. **Agents & Guard**에서 `deployment_manifest_planning` capability와 `generate_deployment_manifest` bounded Action을 가진 활성 Agent를 확인합니다.
3. **Deployment Planner**의 자연어 요구, `app_version_id`, Qwen candidate를 입력합니다.
4. **Generate Manifest**를 실행합니다.
5. 동일한 `run_id` 아래 Request Guard → Agent Registry → Qwen Planner → Manifest Guard가 순서대로 기록되고, 성공 시 `MANIFEST_APPROVED`와 최종 Manifest가 표시됩니다.
6. 실제 배포가 필요할 때만 **Submit to AppDeploy**를 실행합니다. Target hint는 선택 사항이며 AppDeploy가 최종 Target과 Runtime Adapter를 결정합니다.
7. `DEPLOYED` Run만 **배포 후 자율 운영 실험**의 선택지에 나타납니다. Run을 선택해도 Loop가 자동 시작되지는 않습니다.
8. Action Proposal과 Feedback은 동일한 `run_id`와 correlation ID로 배포 후 기록에 연결할 수 있습니다.

자연어 요청 예시는 다음과 같습니다.

```text
Mock 환경에서 CPU 1개, 메모리 1Gi, GPU 0개,
스토리지 1Gi를 사용하는 테스트 앱을 배포해 주세요.
```

처리 흐름은 다음과 같습니다.

```text
자연어 요청
→ Go Request Guard
→ Agent Registry에서 Manifest Planner 권한 확인
→ Qwen Deployment Manifest 생성
→ Go Manifest Guard
→ 최종 DeploymentManifest + MANIFEST_APPROVED
→ (선택) AppDeploy API 호출
→ (선택) 배포 상태·로그·Autonomous Loop·Feedback
```

Manifest 전용 API와 선택적 제출 API는 다음처럼 분리됩니다.

```text
POST /api/v1/control-runs
GET  /api/v1/control-runs/{run_id}
POST /api/v1/control-runs/{run_id}/submit
```

### 5. 실행 상태의 의미

- `approved`: Qwen 제안과 Go Guard 검증이 통과했습니다.
- `rejected`: 정책 또는 Agent bounded Action 검증에서 거부되었습니다.
- `MANIFEST_APPROVED`: geon의 최종 산출물인 Manifest가 생성·검증됐으며 AppDeploy 제출 전입니다.
- `APPDEPLOY_FAILED`: Manifest는 보존됐지만 선택적 AppDeploy 제출 또는 조회가 실패했습니다.
- `DEPLOYED`: AppDeploy가 Manifest를 수락해 `deployment_id`가 Run에 연결됐습니다.
- `pending_executor`: 판단과 검증은 완료됐지만 실행 Agent가 등록되지 않았습니다.
- `not_executed`: 실행 계획만 생성했으며 해당 Action을 직접 실행하지 않았습니다.
- `RUNNING`, `STOPPED`, `FAILED`: AppDeploy가 반환한 실제 배포 상태입니다.

`AppDeployExecutorAgent`는 프로세스 메모리에 등록되므로 geon 서버를 재시작하면 다시 등록해야 합니다. 실제 배포를 실행하려면 AppDeploy에 유효한 App Version과 Target Profile이 먼저 등록되어 있어야 합니다.

### 6. 종료와 문제 확인

각 서버를 실행한 터미널에서 `Ctrl+C`를 누르면 종료됩니다.

```bash
curl http://127.0.0.1:8080/api/v1/healthz
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:18080/healthz
```

- `connection refused`: 해당 서버가 실행되지 않았거나 포트가 다릅니다.
- `candidate not found`: Qwen 후보 설정 파일 또는 candidate ID를 확인합니다.
- `model not found`: Ollama에서 `qwen3.5:4b`를 먼저 pull 합니다.
- `AppDeploy base URL is required`: Manifest 생성은 완료할 수 있지만 제출하려면 `AIOPS_APPDEPLOY_BASE_URL`을 설정해야 합니다.
- AppDeploy 배포 요청 실패: App과 Target이 등록됐는지 먼저 확인합니다.

### 7. 배포 후 자율 운영 실험

**Autonomous Loop**는 Manifest 생성의 필수 단계가 아닌 선택적 배포 후 실험입니다. `DEPLOYED` ControlRun의 AppDeploy 배포 상태와 추론 지표를 주기적으로 읽고, SLO 위반을 Qwen과 Go Guard로 판단한 뒤 설정된 모드에 따라 Action을 제안하거나 실행합니다.

```text
AppDeploy 상태·Metric
→ SLO Evaluator
→ Qwen bounded Action
→ Agent Registry
→ Go Guard
→ Monitor Only: would_execute
→ Guarded Auto: AppDeploy Action 1건
→ cooldown · 다음 주기 재평가
```

서버를 재시작하면 Loop는 항상 `STOPPED`, 모드는 항상 `Monitor Only`로 초기화됩니다. Credential이나 Secret은 설정·프롬프트·이벤트에 포함할 수 없습니다.

geon 서버는 기본적으로 `127.0.0.1`에만 bind됩니다. 공동 VM 등에서 외부 접속을 명시적으로 허용할 때는 관리자 토큰도 함께 설정해야 하며, 외부의 상태 변경 API 요청은 Bearer 토큰 없이는 거부됩니다.

```bash
export AIOPS_BIND_ADDRESS="0.0.0.0"
export AIOPS_AUTONOMY_ADMIN_TOKEN="충분히-긴-임의-토큰"

curl -X POST http://SERVER:18080/api/v1/autonomy/emergency-stop \
  -H "Authorization: Bearer $AIOPS_AUTONOMY_ADMIN_TOKEN"
```

토큰은 웹페이지나 설정 JSON에 입력하지 않습니다. 원격 운영에서는 TLS reverse proxy와 접근 제어를 함께 사용하고, Agent Control 웹의 상태 변경 버튼은 loopback 접속에서 사용합니다.

#### Monitor Only 안전 데모

1. Agent Control에서 **Autonomous Loop**를 엽니다.
2. 실행 중인 AppDeploy `deployment_id`와 SLO를 입력합니다.
3. `Monitor Only`를 선택하고 **Save Policy**를 누릅니다.
4. AppDeploy에 위반 Metric을 기록합니다.

```bash
curl -X POST http://127.0.0.1:8080/api/v1/deployments/DEPLOYMENT_ID/metrics \
  -H "Content-Type: application/json" \
  -d '{"latency_ms":900,"throughput_rps":0.5,"request_count":100,"error_count":8}'
```

5. 같은 조건을 확인할 수 있도록 새 Metric을 한 번 더 기록하고 **Run Cycle**을 두 번 실행합니다.
6. Timeline에서 `violated → proposed → would_execute`를 확인합니다. Monitor Only에서는 AppDeploy 상태 변경 API를 호출하지 않습니다.

REST API로 같은 정책을 등록할 수도 있습니다.

```bash
curl -X PUT http://127.0.0.1:18080/api/v1/autonomy/config \
  -H "Content-Type: application/json" \
  -d '{
    "mode":"monitor_only",
    "poll_interval_seconds":10,
    "consecutive_violations":2,
    "cooldown_seconds":120,
    "max_actions_per_deployment":3,
    "max_metric_age_seconds":60,
    "deployment_id":"DEPLOYMENT_ID",
    "standby_target_profile_id":"",
    "rollback_app_version_id":"",
    "slo":{"max_latency_ms":500,"min_throughput_rps":1,"max_error_rate":0.05}
  }'

curl -X POST http://127.0.0.1:18080/api/v1/autonomy/cycles
curl http://127.0.0.1:18080/api/v1/autonomy/events
```

#### Guarded Auto 데모

실제 비용이나 서비스 영향이 없는 AppDeploy Mock App·Mock Target으로 먼저 실행합니다. **Guarded Auto**를 저장한 뒤 Loop를 시작하거나 **Run Cycle**을 누르면, 신선한 증거·연속 위반·Agent Registry·Go Guard·cooldown·Action budget을 모두 통과한 Action 하나만 실행됩니다.

- `restart_application`: 현재 Deployment를 중지하고 동일 Manifest로 새 Deployment를 요청합니다.
- `rollback_application`: 명시한 `rollback_app_version_id`로만 교체합니다.
- `scale_out_application`: 명시한 `standby_target_profile_id`에 추가 VM Deployment를 요청합니다.
- `stop_application`: 현재 Deployment의 stop API를 호출합니다.
- `observe_status`: 상태와 Metric만 다시 조회합니다.

VM-only `scale_out_application`은 추가 Deployment 생성까지만 수행합니다. Load Balancer나 트래픽 분산은 포함하지 않으며 결과에 `traffic_handoff_required: true`가 표시됩니다. 중지 후 재생성에 실패한 `partial_failure`가 발생하면 자동 실행이 잠기고 모드는 Monitor Only로 돌아갑니다.

#### geon 기록 삭제

Agent Control의 삭제 기능은 geon이 소유한 시험 데이터에만 적용됩니다.

- **Agents & Guard**의 휴지통 버튼은 웹/API로 등록한 `source=runtime` Agent만 삭제합니다.
- `config/agent_registry.json`에서 읽은 Configuration Agent에는 삭제 버튼이 표시되지 않습니다.
- **Decision Timeline**의 행 휴지통은 선택한 이벤트만 삭제하고, 헤더 휴지통은 이벤트 전체를 비웁니다. Sequence를 다시 부여하지 않으며 Loop 설정, 상태, cooldown 및 Action budget은 유지됩니다.
- **Feedback**의 행 휴지통은 선택한 실행 Feedback만 삭제하고, 헤더 휴지통은 Feedback 전체를 비웁니다. 승인된 correlation 등록은 유지되므로 같은 실행의 정상 Feedback을 다시 기록할 수 있습니다.
- **최근 Manifest 실행**의 행 휴지통은 geon 프로세스 메모리의 선택 ControlRun만 삭제하고, `Run 기록 삭제`는 ControlRun 전체를 비웁니다. 서버 재시작 시에도 메모리 기록은 초기화됩니다.
- AppDeploy App·Deployment·Runtime Profile·Target Profile과 CB-Tumblebug Infra·VM은 삭제하지 않습니다.

로컬 API에서 같은 동작을 확인할 수 있습니다.

```bash
curl -X DELETE http://127.0.0.1:18080/api/v1/agents/RUNTIME_AGENT_NAME
curl -X DELETE http://127.0.0.1:18080/api/v1/autonomy/events/SEQUENCE
curl -X DELETE http://127.0.0.1:18080/api/v1/autonomy/events
curl http://127.0.0.1:18080/api/v1/automation/feedback
curl -X DELETE http://127.0.0.1:18080/api/v1/automation/feedback/CORRELATION_ID
curl -X DELETE http://127.0.0.1:18080/api/v1/automation/feedback
```

외부 bind 환경에서는 모든 DELETE 요청에 `AIOPS_AUTONOMY_ADMIN_TOKEN` Bearer token이 필요합니다.

AI service-control prototype의 Go 구현 모듈입니다. 이 모듈은 LLM 호출 전 Go Request Guard, 실제 LLM 기반 Deployment Manifest 생성, Go Manifest Guard, AppDeploy 요청·상태 추적, agent 등록과 bounded Action 검증을 제공합니다.

## 테스트 실행

```bash
go test ./...
```

## API 실행

```bash
go run ./cmd/service-control-api
```

## CLI 실행

```bash
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first

go run ./cmd/aiops-service-control validate-vm-suitability \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control plan-ai-application-control \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control plan-llm-automation-action \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --candidate-id qwen3.5-ops-planner \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id qwen3.5-ops-planner \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --guard-policy ../../config/planner_guard_policy.json \
  --appdeploy-base-url http://127.0.0.1:8080/api/v1

go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --vm-requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference \
  --operation-service llm-chat-inference \
  --operation-resource aws-us-west-2-g6-xlarge-l4-20260707 \
  --mode plan_only \
  --guard-backend go
```

## API Endpoint

| Method | Path |
| --- | --- |
| `GET` | `/healthz` |
| `GET` | `/openapi.yaml` |
| `GET` | `/api/v1/agents` |
| `POST` | `/api/v1/agents` |
| `GET` | `/api/v1/agents/:name` |
| `DELETE` | `/api/v1/agents/:name` |
| `POST` | `/api/v1/agents/:name/actions/:action/validate` |
| `POST` | `/api/v1/agents/:name/invocations/plan` |
| `POST` | `/api/v1/ops-llm/select` |
| `POST` | `/api/v1/apps/vm-suitability` |
| `POST` | `/api/v1/apps/deployment-plan` |
| `POST` | `/api/v1/automation/action-proposals` |
| `POST` | `/api/v1/automation/feedback` |
| `GET` | `/api/v1/autonomy/status` |
| `PUT` | `/api/v1/autonomy/config` |
| `POST` | `/api/v1/autonomy/start` |
| `POST` | `/api/v1/autonomy/stop` |
| `POST` | `/api/v1/autonomy/emergency-stop` |
| `POST` | `/api/v1/autonomy/cycles` |
| `GET` | `/api/v1/autonomy/events` |
| `DELETE` | `/api/v1/autonomy/events` |
| `POST` | `/api/v1/planner/deployments` |
| `POST` | `/api/v1/service-operations/run` |

## 응답 신호

통합 pipeline은 다음 값을 반환합니다.

```text
selected_llm
decision_execution_status
llm_automation_action
runtime_model
selected_resource (입력된 실제 VM snapshot ID)
deployment_plan
deployment_validation
agent_reviews
operation_pipeline_ready
guard_backend
guard_validation
```

`deployment_plan.executor_type`은 `registered_external_agent`입니다. 특정 팀 도구를 고정하지 않으며, registry에 등록된 에이전트 중 `ai_application_deployment_control` capability와 요청 Action을 모두 허용한 실행 주체를 선택합니다. 연결된 실행 주체가 없으면 `register_executor_agent`를 precondition으로 남기고 실행하지 않습니다.
