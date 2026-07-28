# 🏛️ Kyung Hee AIOps 🦁

> AI 기반 서비스 제어 및 관리 자동화 프레임워크
> 1차년도 Go 기반 service-control prototype

[![Go](https://img.shields.io/badge/Go-1.25+-00ADD8?logo=go&logoColor=white)](go/service-control-api/go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 🧭 개요

이 저장소는 경희대학교 1차년도 연구 범위 중 **AI 기반 서비스 제어 및 관리 자동화 프레임워크**를 위한 제출용/시연용 패키지입니다.

핵심 구현은 Go 언어로 구성되어 있습니다. 하나의 `ControlRun` 안에서 Go Request Guard가 자연어 요청을 검사하고, Agent Registry가 capability와 bounded Action을 기준으로 Agent를 승인합니다. Agent Dispatcher는 내장 `AIApplicationAutomationAgent`를 Qwen Planner로 실행하거나 등록된 Runtime Agent endpoint를 한 번 호출합니다. Qwen은 승인된 요구를 `DeploymentManifest`로 변환하고, Go Manifest Guard가 계약·자원 값·권한·보안 정책을 검증합니다.

검증된 Manifest 생성은 AppDeploy 없이도 완료됩니다. 사용자가 별도로 제출을 선택한 경우에만 별도 AppDeploy 서버와 저장소가 실제 Target과 Runtime Adapter를 선택하고 배포를 실행합니다. Automatic Feedback은 같은 `run_id`에 이미 있는 Guard·Planner·Manifest 증적을 읽기 전용으로 투영하며 Qwen을 재학습하지 않습니다. 배포 후 Autonomous Loop는 `DEPLOYED` 이후에만 사용하는 선택 경로입니다.

현재 기본 Planner 모델은 **Qwen 3.5 4B (`qwen3.5:4b`)**입니다. Go 구현은 OpenAI-compatible endpoint 계약을 사용하므로 Ollama, vLLM 또는 연구 서버는 Qwen을 제공하는 실행 런타임으로 교체할 수 있습니다. 약 3.4GB의 Ollama 양자화 모델을 사용해 로컬과 AWS NVIDIA L4 24GB VM에서 같은 Planner 설정을 검증합니다.

## 🚀 geon Control Plane 빠른 실행

geon Control Plane은 자연어 운영 요청을 Registry가 승인한 Qwen Planner로 계획하고, 이중 Go Guard로 검증된 Manifest를 생성합니다. AppDeploy 제출은 선택 사항이며 각 구성 요소는 별도 프로세스로 실행합니다.

| 구성 요소 | 역할 | 기본 주소 |
| --- | --- | --- |
| Ollama | Qwen `qwen3.5:4b` 추론 | `http://127.0.0.1:11434/` |
| AppDeploy | App/Target 등록과 실제 배포 실행 | `http://127.0.0.1:8080/swagger` |
| geon Agent Control | Agent Registry, Qwen 계획, Go Guard, 자동화 제어 | `http://127.0.0.1:18080/` |

### 1. 사전 준비

- Go 1.25 이상
- Ollama와 Qwen `qwen3.5:4b`
- geon 브랜치 저장소
- 실제 배포까지 시험할 때만 AppDeploy 브랜치 저장소

Windows Git Bash에서 Go 경로와 Qwen 모델을 확인합니다.

```bash
export PATH="/c/Program Files/Go/bin:$PATH"
go version

ollama list
ollama pull qwen3.5:4b  # 목록에 없을 때만 실행
```

Git Bash에서 `ollama`를 찾지 못하면 `"$HOME/AppData/Local/Programs/Ollama/ollama.exe"`를 사용합니다.

### 2. AppDeploy 실행

AppDeploy 저장소 내부에서 첫 번째 터미널을 엽니다. Git이 실제 저장소 루트를 자동으로 찾으므로 복제 위치나 폴더 이름을 수정할 필요가 없습니다.

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

### 3. geon Agent Control 실행

geon 저장소 내부에서 두 번째 터미널을 엽니다. 현재 Git 저장소 루트를 기준으로 설정과 Go 모듈을 찾습니다.

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

### 4. 상태와 웹 화면 확인

세 번째 터미널에서 확인합니다.

```bash
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:8080/api/v1/healthz
curl http://127.0.0.1:18080/healthz
```

- AppDeploy Swagger: `http://127.0.0.1:8080/swagger`
- geon Agent Control: `http://127.0.0.1:18080/`

### 5. 첫 사용 순서

1. **Manifest Workflow**를 엽니다.
2. 자연어 요청과 `app_version_id`를 입력합니다. Manifest-only 시험에는 형식이 유효한 시험 ID를 사용할 수 있습니다.
3. **Generate Manifest**로 ControlRun을 생성합니다.
4. 같은 `run_id`에 기록된 Request Guard → Agent Registry → Agent Dispatcher → Qwen Planner → Manifest Guard를 확인합니다. Agent Registry는 관리형 내부 단계이고 **Agents & Guard**는 capability와 bounded Action을 확인하는 보조 화면이며, 사용자 진입점이 아닙니다.
5. 승인된 `DeploymentManifest`를 확인합니다.
6. 실제 배포가 필요할 때만 **Submit to AppDeploy**를 선택합니다. AppDeploy는 별도 서버·저장소이며 geon Manifest 생성의 필수 조건이 아닙니다.
7. `DEPLOYED`와 `deployment_id`가 확인된 뒤에만 **Post-deployment**를 사용합니다.
8. **Feedback**에서 같은 `run_id`의 Automatic Run Feedback을 확인합니다. 이는 기존 Run 증적의 읽기 전용 투영이며 Qwen을 재학습하지 않습니다. **External Executor Callback Test**는 선택 사항이고 승인된 `correlation_id`가 있을 때만 사용합니다.

각 서버는 실행한 터미널에서 `Ctrl+C`로 종료합니다. Autonomous Loop, Guarded Auto, 기록 삭제와 문제 해결 절차는 [geon Agent Control 상세 실행 가이드](go/service-control-api/README.md#geon-agent-control-실행-가이드)를 참고합니다.

## 🎯 담당 범위

- Ops 분석 시험 및 최적 LLM 선정 흐름
- AI LLM 운영 관리 구조 설계 및 검증
- Agent Registry를 실제 Planner 선택·권한 검증을 수행하는 내부 단계로 사용
- Agent Dispatcher로 내장 Agent와 등록 Runtime Agent의 공통 실행 계약 제공
- `AIApplicationAutomationAgent`를 기본 Manifest Planner 프로필로 등록·관리
- 등록 Runtime Agent endpoint를 실행 전·후 Guard와 함께 bounded HTTP 요청 1건으로 호출
- 자연어 App 요구 분석과 CPU·메모리·GPU·디스크·accelerator 요구량 결정
- AppDeploy 공식 Deployment Manifest 생성과 요청·Manifest 이중 Go Guard 검증
- 승인된 Manifest의 선택적 AppDeploy 전달, 배포 상태 polling, 로그 조회와 재시도 가능 여부 판단
- 인프라 계층이 제공한 실제 CPU/GPU VM snapshot과 workload 요구사항의 보조 적합성 검증

이 저장소는 VM 후보를 임의로 만들거나 VM을 직접 프로비저닝하지 않습니다. 실제 인프라 생성은 인프라 계층이, App Spec 조회·Target 선택·Adapter 선택·배포 실행은 AppDeploy가 담당합니다. Runtime Agent 호출은 등록된 endpoint에 대한 단일 요청으로 제한하며 범용 다중 Agent 워크플로 엔진과 Job Scheduling Agent는 포함하지 않습니다. LLM, Runtime Agent 또는 AppDeploy 호출 실패를 가짜 성공 결과로 대체하지 않습니다.

## 🗂️ 코드 구조

| 경로 | 설명 |
| --- | --- |
| [`go/service-control-api/`](go/service-control-api/) | LLM Deployment Planner, Agent Registry, 이중 Go Guard, AppDeploy 연계를 제공하는 Go API/CLI |
| [`contracts/appdeploy/`](contracts/appdeploy/) | 최신 Target 자동 선택 방식과 호환되는 AppDeploy Deployment Manifest 계약 snapshot |
| [`go/aiops-guard/`](go/aiops-guard/) | 서비스 제어 action의 허용 범위를 검증하는 Go guard |
| [`config/`](config/) | LLM 후보, 에이전트 registry, workload별 VM 요구사항 설정 |
| [`data/`](data/) | Ops LLM 평가 scenario |
| [`docs/`](docs/) | 산출물, 실행 가이드, 검증 문서, 구조도 |
| [`examples/`](examples/) | API 요청/응답 예제 |

## 📦 공식 산출물

| 산출물 | 원본 | DOCX |
| --- | --- | --- |
| 요구사항 정의서 | [Markdown](docs/submission/requirements_definition.md) | [DOCX](docs/submission/requirements_definition.docx) |
| LLM 운영 관리 구조 설계서 | [Markdown](docs/deliverables/01_llm_operation_management_design.md) | [DOCX](docs/deliverables/docx/01_LLM_Operation_Management_Design.docx) |
| 에이전트 등록 관리 프로토타입 | [Markdown](docs/deliverables/02_agent_registration_management_prototype.md) | [DOCX](docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx) |
| AI 응용 배포·제어 추론 최적화 전략 설계서 | [Markdown](docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md) | [DOCX](docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx) |

## 📚 문서 바로가기

| 문서 | 설명 |
| --- | --- |
| [문서 지도](docs/README.md) | 전체 문서와 산출물 진입점 |
| [설치 및 실행 가이드](docs/submission/install_and_run_guide.md) | 로컬/VM 실행 절차 |
| [테스트 가이드](docs/submission/test_guide.md) | Go 테스트와 검증 명령 |
| [기능/API 가이드](docs/submission/functional_api_guide.md) | API 기능과 응답 구조 |
| [OpenAPI 계약](docs/submission/openapi_service_control.yaml) | Swagger/OpenAPI 산출물 |
| [LLM Deployment Planner·Go Guard 흐름](docs/design/main_llm_go_guard_control_flow.md) | 자연어 요구 분석, Manifest 검증, AppDeploy 전달과 상태 조회 구조 |
| [플래너·AppDeploy 책임 경계](docs/design/integration_boundary.md) | 최신 AppDeploy 계약, 통합 준비 순서와 Planner·배포 실행 책임 구분 |
| [Ops LLM 평가 방법](docs/submission/ops_llm_benchmark_method.md) | dry-run과 실제 endpoint 실행 기준 |
| [1차년도 VM 통합·개별 동작 시나리오](docs/design/year1_vm_operation_scenarios.md) | 컨테이너를 제외한 VM-only 통합 흐름과 개별 시험 초안 |
| [검증 증적 가이드](docs/evidence/증적_패키지_가이드.md) | 실행 결과와 증적 정리 기준 |
| [AWS GPU VM 검증 결과](docs/evidence/vm_validation_20260707.md) | `validate-system --target vm` 실행 결과와 GPU 증적 |

## 🛠️ 개발 환경

- 개발 언어: Go
- Go 기준 버전: Go 1.25+
- 검증 기준: `geon` 브랜치는 Go 1.25 기준으로 검증됨
- 백엔드 프레임워크: Echo
- 소스 코드 관리: GitHub
- 라이선스: Apache 2.0

## License

ai-ops는 [Apache License 2.0](./LICENSE)에 따라 배포됩니다.
