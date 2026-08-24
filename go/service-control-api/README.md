# Service Control API

## 역할

이 서비스는 경희대학교 AI 어플리케이션 자동화 에이전트 PoC의 Go 실행 모듈입니다.

```text
Natural-language request or Structured App Spec
→ LLM_Op Qwen Safeguard
→ Requirement Analyzer
→ ApplicationProfile
→ Mock Resource Recommender
→ ResourceRecommendation
→ Agent Registry에서 배포 판단 Agent 선택
→ Internal executor 또는 Runtime HTTP endpoint 호출
→ Request Guard / Result Guard
→ DEPLOY / REJECT / RETRY
→ Domain Go Guard
→ DesiredDeploymentSpec
→ deployment.status.changed
→ optimization.feedback.created
→ OperationOptimizationAgent
→ KEEP / SCALE_OUT / SCALE_IN recommendation
```

핵심 Flow는 AppDeploy, VM, Kubernetes 없이 로컬에서 독립 실행할 수 있습니다. 실제 배포와 플랫폼 전용 변환은 외부 시스템의 책임입니다.

## 바로 실행

저장소 루트에서 다음 중 하나를 실행합니다.

Windows PowerShell 또는 VS Code 터미널:

```powershell
ollama pull qwen3.5:4b
.\run-agent-control.cmd
```

Git Bash, Linux 또는 macOS:

```bash
ollama pull qwen3.5:4b
./run-agent-control.sh
```

실행 후 [http://127.0.0.1:18080/](http://127.0.0.1:18080/)을 엽니다. 종료할 때는 `Ctrl+C`를 누릅니다.

LLM_Op의 별도 수동 시연은 현재 `LLM_Op`과 geon 최신화 통합 브랜치에서 제공하며, GitHub에 push한 것만으로 호스팅되지 않는다. 병합 전 `geon` checkout에는 route가 없을 수 있다. 서버 없이 보려면 저장소 루트의 `.\open-llm-op-demo.cmd`를 실행한다. 통합 route를 보려면 먼저 위 launcher로 서버를 실행하고 [healthz](http://127.0.0.1:18080/healthz)를 확인한 뒤 [http://127.0.0.1:18080/llm-op-demo](http://127.0.0.1:18080/llm-op-demo)를 연다. 환경변수 없이 `go run ./cmd/service-control-api`를 직접 실행했다면 포트는 `18080`이 아니라 기본값 `8080`이다. 페이지는 모델 API나 AppDeploy를 호출하지 않으며, 저장 예시 재생 또는 두 단계 prompt 복사·raw JSON 붙여넣기를 지원한다. 상세 절차는 [수동 2단계 LLM 브라우저 시연](../../docs/llm-op/07-manual-two-stage-browser-demo.md)을 참조한다.

공식 연결에서는 LLM_Op 최초 Safeguard가 모든 live Requirement Analyzer·Manifest LLM보다 먼저 실행되어야 한다. geon은 승인된 `Flow`, `DesiredDeploymentSpec`, `ManifestRevision`을 소유하고, LLM_Op은 승인된 `INITIAL` revision의 AppDeploy `prepare_only` 초안과 Safeguard 증거를 소유한다. Operation Optimization과 `Revision 2`는 LLM_Op 범위가 아니다. 별도 구현도 [Guard-first 연결 기준](../../docs/coordination/llm-op-guard-first-integration-standard.md)의 순서와 fail-closed 결과를 따라야 한다.

신뢰된 단일 프로세스 연결은 `internal/trustedorchestration`이 `ReviewWithConfig → AutomationRunner.RunAnalysisRequest` 순서를 강제한다. 웹 기본 경로는 여기서 canonical Revision 1을 반환하며 AppDeploy에 제출하지 않는다. 선택적인 후속 통합 경로만 `ProjectApprovedInitialFlow`로 prepare-only 요청을 투영한다. allow 이외 결과와 불완전한 continuation은 geon 실행 전에 종료된다.

```bash
go test ./internal/llmop ./internal/llmopbridge ./internal/trustedorchestration -count=1
```

## VS Code F5 실행

저장소 루트에 포함된 VS Code 구성으로 실행하는 방법을 권장합니다.

1. `ai-ops` 저장소 루트를 VS Code로 엽니다. `go/service-control-api` 폴더만 따로 열지 않습니다.
2. 권장 확장 `golang.go`를 설치하고 VS Code를 다시 시작합니다.
3. **Run and Debug**에서 `geon: Agent Control (18080)`을 선택합니다.
4. `F5`를 누릅니다.
5. [http://127.0.0.1:18080/healthz](http://127.0.0.1:18080/healthz)가 `status=ok`인지 확인합니다.
6. [http://127.0.0.1:18080/](http://127.0.0.1:18080/)에서 실험을 시작합니다.

F5 구성은 저장소 루트, 18080 포트와 Mock Adapter를 자동으로 설정합니다.

```text
AIOPS_REPO_ROOT=${workspaceFolder}
AIOPS_BIND_ADDRESS=127.0.0.1
AIOPS_DEPLOYMENT_ADAPTER=mock
AIOPS_LLM_CANDIDATES_PATH=config/ops_llm_eval_candidates.local_ollama.json
AIOPS_LLMOP_ALLOW_LIVE_COMPLETION=true
PORT=18080
```

VS Code의 **Terminal > Run Task**에서는 다음 작업을 제공합니다.

| Task | 역할 |
| --- | --- |
| `geon: 서버 실행 (18080)` | 디버거 없이 Agent Control 서버 실행 |
| `geon: 전체 테스트` | 전체 Go 테스트 실행 |
| `geon: Go Vet` | Go 정적 검사 실행 |

Go 확장이 실행되지 않으면 터미널에서 `go version`을 확인하고 VS Code를 다시 시작합니다. 포트 충돌은 PowerShell에서 확인할 수 있습니다.

```powershell
Get-NetTCPConnection -LocalPort 18080 -State Listen
```

Runtime Agent는 Registry에 등록하는 것만으로 실행되지 않습니다. Runtime Agent를 선택하려면 등록한 `endpoint + invocation_path`에 응답하는 별도 HTTP 서버가 먼저 실행 중이어야 합니다.

## CLI 실행

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

### 자동화 실행

핵심 실험의 시작 화면입니다. 자연어 요청 또는 구조화 App Spec 하나를 입력합니다.

1. Requirement Analyzer가 `ApplicationProfile` 생성
2. Mock Resource Recommender가 `ResourceRecommendation` 생성
3. 선택한 배포 판단 Agent의 Registry 권한 확인
4. Internal executor 또는 Runtime HTTP endpoint에서 `DEPLOY`, `REJECT`, `RETRY` 판단
5. Agent 밖의 Request/Result/Domain Go Guard 검증
6. 승인된 경우에만 `DesiredDeploymentSpec` 생성

**자동 분석 및 판단**을 한 번 누르면 세 단계가 같은 `run_id`, `correlation_id`, `trace_id`로 연결됩니다. 생성된 `ApplicationProfile`과 `ResourceRecommendation`은 고급 증거 영역에서 확인합니다. 기존 Common JSON 입력은 고급 프로토콜 검증용으로만 유지됩니다.

정상 샘플의 핵심 결과:

```json
{
  "run_id": "run-...",
  "status": "COMPLETED",
  "requirement_analysis": {
    "mode": "local_rule",
    "application_profile": {}
  },
  "resource_recommendation": {
    "resource_recommendation": {
      "selected_candidate_id": "mock-gpu-l4"
    }
  },
  "flow": {
    "state": "DEPLOY_APPROVED",
    "requested_decision_agent": "AIApplicationAutomationAgent",
    "agent_execution": {
      "agent_name": "AIApplicationAutomationAgent",
      "source": "configuration",
      "status": "completed",
      "request_guard": {"status": "APPROVED"},
      "result_guard": {"status": "APPROVED"}
    },
    "decision": {"action": "DEPLOY"},
    "guard": {"status": "APPROVED"}
  },
  "desired_deployment_spec": {
    "spec_version": "1.0"
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

설정 파일의 기본 Agent는 웹에서 삭제할 수 없고, 현재 프로세스에 등록한 Runtime Agent만 삭제할 수 있습니다. 다음 조건을 모두 만족하는 Agent만 **자동화 실행**의 선택 목록에 나타납니다.

- `enabled=true`
- capability `ai_application_automation`
- bounded action `generate_deployment_decision`

선택한 Runtime Agent는 기본 Internal Agent 대신 실제 HTTP 호출로 배포 판단을 수행합니다. 실패 시 Internal Agent로 조용히 대체하지 않으며, `AGENT_AUTHORIZATION_REJECTED`, `AGENT_EXECUTION_FAILED`, `AGENT_RESULT_REJECTED` 중 하나를 같은 Flow에 기록하고 Adapter 제출을 중단합니다. Runtime 등록 정보는 프로세스 메모리에만 유지됩니다.

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

메인 `Revision 1 생성`은 LLM_Op의 최초 자연어 Safeguard를 실제 실행하므로 Ollama가 필요합니다. **실험 결과 → 추론 방식 비교**도 같은 로컬 Qwen endpoint를 사용합니다.

```bash
ollama pull qwen3.5:4b
ollama list
export AIOPS_LLM_CANDIDATES_PATH="config/ops_llm_eval_candidates.local_ollama.json"
```

기본 후보 설정:

```text
config/ops_llm_eval_candidates.local_ollama.json
```

저장소 루트의 `run-agent-control.cmd`와 `run-agent-control.sh`는 위 로컬 후보와 live Safeguard를 자동 설정합니다. Ollama가 꺼져 있으면 최초 Safeguard가 fail-closed로 종료되고 Revision 1은 생성되지 않습니다.

## API 순서

### Guard-first 핵심 배포 판단

```text
POST /api/v1/agent-control/trusted-automation-runs
GET  /api/v1/agent-control/automation-runs/{run_id}
```

자연어 입력 예:

```bash
curl -s -X POST http://127.0.0.1:18080/api/v1/agent-control/trusted-automation-runs \
  -H "Content-Type: application/json" \
  -d '{
    "app_version_id": "appver-geon-poc-001",
    "candidate_id": "qwen3.5-ops-planner",
    "input": {
      "input_type": "natural_language",
      "request": "GPU 1개, CPU 4코어, 메모리 8GiB, 스토리지 20GiB로 AI 추론 서비스를 배포해 주세요.",
      "requested_by": "geon-web",
      "decision_agent": "AIApplicationAutomationAgent"
    }
  }'
```

구조화 입력은 `input_type`을 `structured`로 지정하고 `app_spec`에 CPU, 메모리, 스토리지, GPU 요구량을 전달합니다.

### 고급 Common JSON 검증

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

### Two-Agent Mock Experiment

The main Flow uses `AIApplicationAutomationAgent` for the deployment decision and `OperationOptimizationAgent` after deployment Feedback. Request, Result, Domain, and Scaling Guards remain outside both Agents. `DesiredDeploymentSpec` is produced only after the first Agent passes Guard validation; the second Agent emits a scaling recommendation and does not execute VM control or AppDeploy.

1. Start Ollama and the server with the Mock Adapter, then submit `POST /api/v1/agent-control/trusted-automation-runs` and verify the LLM_Op Safeguard approval, the first Agent's approved Guards, and `DesiredDeploymentSpec`.
2. Send `POST /api/v1/agent-control/deployment-status` with `RUNNING` and matching `correlation_id`, `trace_id`, and `profile_id`.
3. Send `POST /api/v1/agent-control/optimization-feedback?operation_agent=OperationOptimizationAgent` with SLO and resource Feedback. Omitting the query uses the Registry default Operation Agent.
4. Read `GET /api/v1/agent-control/flows/{correlation_id}` and verify `requested_operation_agent`, `operation_agent_execution`, its three Guards, and `scaling_decision`. A normal result is `KEEP`; the SLO-violation sample is `SCALE_OUT 1 -> 2`.

`NO_ACTION` remains accepted only as a legacy Operation Agent proposal/result value, not an `optimization-feedback` client request, and is normalized to `KEEP` before Guard validation. New results emit `KEEP`. `SIMULATED` is Mock Adapter evidence, not real deployment, VM control, AppDeploy execution, or scaling execution.

The Guard-first web Flow requires Ollama for the initial LLM_Op Safeguard. The deployment and operation decisions remain deterministic Agent/Guard steps after that approval. Selecting a Runtime Agent additionally requires its registered `endpoint + invocation_path` to serve the execution request; an endpoint failure is recorded and never falls back to an Internal Agent.

### Agent Registry

```text
GET    /api/v1/agents
POST   /api/v1/agents
DELETE /api/v1/agents/{name}
```

`GET /api/v1/agents`의 `eligible_decision_agents`가 자동화 실행에서 선택 가능한 Agent 목록이고, `defaults.ai_application_automation`이 요청에서 `decision_agent`를 생략했을 때 사용하는 기본 Agent입니다.

Runtime Agent endpoint는 등록한 `endpoint + invocation_path`에서 POST 요청을 받고 아래 형식으로 응답해야 합니다. 응답의 `run_id`, `agent`, `proposal.action`은 요청과 정확히 일치해야 합니다.

```json
{
  "run_id": "run-...",
  "agent": "RuntimeDeploymentAgent",
  "status": "completed",
  "proposal": {
    "action": "generate_deployment_decision",
    "parameters": {
      "decision": "DEPLOY",
      "selected_candidate_id": "mock-gpu-l4",
      "reason": "The candidate satisfies the requested resources.",
      "confidence": 0.92
    }
  },
  "latency_ms": 12,
  "domain_validation": "deployment_decision"
}
```

Go Guard는 Runtime Agent 밖에서 요청 권한, 응답 신원·Action, 후보 ID, 자원 적합성, confidence 범위를 다시 검증합니다.

## Deployment Adapter 경계

- `AIOPS_DEPLOYMENT_ADAPTER=mock`: 승인된 요청을 외부로 보내지 않고 `SIMULATED` 증거를 생성합니다.
- `AIOPS_DEPLOYMENT_ADAPTER=handoff`: 외부 연동 가능한 Common JSON을 `READY` 상태로 기록합니다.
- 두 모드 모두 실제 INNO/ETRI API 호출이나 VM 배포 성공을 의미하지 않습니다. 향후 실제 연동 시 Adapter 구현만 교체합니다.

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
