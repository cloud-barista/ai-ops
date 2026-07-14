# AI App Deployer

> AI 응용 등록/배포 프로토타입

[![Go](https://img.shields.io/badge/Go-1.23+-00ADD8?logo=go&logoColor=white)](go.mod)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## 🧭 개요

이 저장소는 경희대학교 1차년도 연구 범위 중 **AI 반도체 기반 AI 모델 및 응용 관리 프레임워크**를 위한 제출용/시연용 패키지입니다.

핵심 구현은 Go 언어로 구성되어 있으며, AI 반도체 기반 AI 응용 배포 및 운용 구조 설계, CPU/GPU VM 기반 AI 응용 등록/배포 프로토타입 개발을 하나의 service-control prototype으로 검증합니다.

이 저장소는 실제 인프라 생성이나 운영 배포 완료를 직접 주장하지 않습니다. 실제 VM/GPU 적용 결과는 외부 인프라/배포 계층의 실행 결과와 구분합니다. Docker, Docker Compose, Kubernetes, OCI Image, Container Registry 기반 배포는 1차년도 범위에 포함하지 않습니다.

## 🎯 담당 범위

- AI 반도체 기반 AI 응용 배포 및 운용 구조 설계
- CPU/GPU VM 기반 AI 응용 등록/배포 프로토타입 개발
- App Spec, Runtime Profile, Target Profile 기반 등록/배포 흐름
- Resource Check, Deployment 로그, Monitoring, Inference Proxy, Stop 제어

## 🗂️ 코드 구조

| 경로 | 설명 |
| --- | --- |
| [`cmd/web/`](AppDeploy/cmd/web/) | 영속 저장소를 기본 사용하는 AI App Deployer 웹 콘솔 진입점 |
| [`cmd/appdeployer/`](AppDeploy/cmd/appdeployer/) | 서버를 별도로 실행하지 않고 전체 기능을 사용하는 대화형 CLI 진입점 |
| [`cmd/server/`](cmd/server/) | Go/Echo 기반 AI App Deployer API 서버 진입점 |
| [`internal/webui/`](AppDeploy/internal/webui/) | 서버 바이너리에 내장되는 반응형 웹 콘솔과 정적 자산 |
| [`internal/`](internal/) | App registry, deployment, runtime adapter, resource, monitoring, inference 구현 |
| [`contracts/openapi/openapi.yaml`](contracts/openapi/openapi.yaml) | `/api/v1` API source of truth |
| [`contracts/schemas/`](contracts/schemas/) | App Spec, Profile, Deployment, Inference JSON Schema |
| [`examples/`](examples/) | API 요청 예제와 CPU/GPU VM smoke artifact 예제 |
| [`scripts/`](scripts/) | API smoke, interface smoke, evidence 수집, ai-ops-geon SSH 배포 실행 스크립트 |
| [`conf/`](conf/) | 로컬 실행용 설정 템플릿과 ai-ops-geon SSH 배포 JSON 설정 |
| [`docs/`](docs/) | 설치/시험/API/운영 가이드와 문서 지도 |
| [`deliverables/`](deliverables/) | 공식 산출물, 외부 인터페이스 패키지, 증적, 릴리스 체크리스트 |

## 📦 공식 산출물

| 산출물 | 원본 | DOCX |
| --- | --- | --- |
| AI 반도체 기반 AI 응용 배포 및 운용 구조 설계서 | [Markdown](deliverables/design/AI_반도체기반_AI응용배포_및_운용구조설계서_최신본.md) | [DOCX](deliverables/design/AI_반도체기반_AI응용배포_및_운용구조설계서_최신본.docx) |
| CPU/GPU VM 기반 AI 응용 등록/배포 프로토타입 개발설계서 | [Markdown](deliverables/design/CPU_GPU_VM기반_AI응용등록_배포프로토타입_개발설계서_최신본.md) | [DOCX](deliverables/design/CPU_GPU_VM기반_AI응용등록_배포프로토타입_개발설계서_최신본.docx) |

## 📚 문서 바로가기

| 문서 | 설명 |
| --- | --- |
| [문서 지도](docs/README.md) | 전체 문서와 산출물 진입점 |
| [설치 및 활용 가이드](docs/install/설치_활용_가이드_초안.md) | 로컬 실행, VM SSH runner, smoke/evidence 실행 절차 |
| [시험 가이드](docs/test/시험_가이드_초안.md) | TC 기준, 로그 수집, 증적 저장 방식 |
| [기능/API 가이드](docs/api/기능_API_가이드_초안.md) | API 기능과 호출 흐름 |
| [OpenAPI 계약](contracts/openapi/openapi.yaml) | `/api/v1` Swagger/OpenAPI 계약 |
| [로그·에러 가이드](docs/ops/로그_에러_가이드.md) | 표준 로그 필드, error_code, 증적 우선순위 |
| [외부 제공 인터페이스 명세](deliverables/interface/spec/KHU_AI_App_Deployer_외부제공인터페이스_명세서.md) | 외부 시스템 제공 API 명세 |
| [증적 패키지 가이드](deliverables/evidence/증적_패키지_가이드.md) | 제출 증적 구성 기준 |

## 🛠️ 개발 환경

- 개발 언어: Go
- Go 기준 버전: Go 1.23+
- 검증 기준: 해당 브랜치는 `go.mod`의 Go 1.23 기준으로 검증
- 백엔드 프레임워크: Echo
- API prefix: `/api/v1`
- 소스 코드 관리: GitHub
- 라이선스: Apache 2.0

## 🚀 실행 방법

먼저 Go 모듈 디렉터리로 이동합니다.

```powershell
Set-Location .\AppDeploy
go mod tidy
```

### 1. 웹 콘솔 실행 (권장)

```powershell
go run ./cmd/web
```

브라우저에서 아래 주소를 엽니다.

```text
http://localhost:8080/
```

웹 콘솔은 별도의 Node 설치나 프런트엔드 빌드 없이 Go 서버에 내장되어 실행됩니다. 다음 기능을 화면에서 사용할 수 있습니다.

- 전체 배포·알람·Runtime 준비 상태 대시보드
- AI App, Runtime Profile, Target Profile 등록
- 각 입력 변수의 의미, 허용값, 형식과 예시를 보여주는 인라인 도움말
- Target 자원 준비 상태 점검
- 배포 생성, 상태·이벤트 로그 조회, 중지
- Runtime health, 알람, 추론 메트릭 모니터링
- 실행 중인 배포의 health 확인과 추론 호출

처음 사용하는 경우 왼쪽 메뉴의 **사용 가이드**를 엽니다. Credential이 필요 없는 Mock 시험, 실제 CPU VM SSH 배포, NVIDIA GPU VM 배포·추론 시나리오를 실제 입력값과 예상 결과에 따라 순서대로 진행할 수 있습니다. 같은 화면 하단에는 오류 코드별 원인과 다음 조치가 정리되어 있습니다.

웹 콘솔 데이터는 기본적으로 `data/appdeployer-store.json`에 저장됩니다. 포트를 변경할 때는 다음처럼 실행합니다.

```powershell
$env:AIAPP_SERVER_PORT = "18090"
go run ./cmd/web
```

이 경우 접속 주소는 `http://localhost:18090/`입니다. 실제 CPU/GPU VM에 배포하려면 기존 SSH Runner 환경변수도 함께 설정합니다.

### 2. 대화형 CLI 실행

```powershell
go run ./cmd/appdeployer
```

실행 후 `help`를 입력하면 앱·런타임·대상 등록, 자원 점검, 배포, 로그, 모니터링, 추론, 메트릭, 중지 명령을 확인할 수 있습니다.

CLI는 시작할 때 API 준비 상태, 등록된 App/Runtime/Target 수, 실행 중·실패 배포 수를 요약합니다. `home` 또는 `status`로 이 요약을 다시 볼 수 있으며, 대화형 터미널에서는 표·상태·오류·JSON에 색상을 적용합니다. 파일 리다이렉트나 `NO_COLOR=1` 환경에서는 색상 코드가 자동으로 비활성화됩니다.

웹의 유형 선택형 Package 생성은 CLI와 HTTP 셸에서도 사용할 수 있습니다. `packages build`는 `aiops-geon-service-control`, `go`, `python`, `node`, `binary`, `script` 중 하나로 Package를 만들고, `packages deploy`는 App 등록과 Target 자원 점검을 거쳐 `available`일 때만 배포를 생성합니다.

```powershell
go run ./cmd/appdeployer packages deploy --type script `
  --source .\examples\cpu-smoke-run.sh --entrypoint cpu-smoke-run.sh `
  --runtime-id rt-cpu-001 --target-id target-cpu-001
```

실행 중인 웹 서버와 같은 저장 상태를 사용하려면 `scripts/package-deploy.ps1` 또는 Bash 3.2+용 `scripts/package-deploy.sh`를 사용합니다.

#### CPU VM dry-run 배포 예시

예제 JSON으로 App, Runtime Profile, Target Profile을 등록합니다.

```text
appdeployer> help
appdeployer> status
appdeployer> apps add examples/requests/app-cpu-script.json
appdeployer> runtimes add examples/requests/runtime-cpu-vm.json
appdeployer> targets add examples/requests/target-cpu-vm.json
```

등록된 항목을 확인합니다.

```text
appdeployer> apps list
appdeployer> runtimes list
appdeployer> targets list
```

`apps add` 결과에 출력된 `app_version_id`를 사용해 자원을 점검하고 배포를 생성합니다.

```text
appdeployer> resources check target-cpu-001 rt-cpu-001
appdeployer> deployments create appver-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx rt-cpu-001 target-cpu-001
```

`deployments create` 결과의 `deployment_id`로 상태와 로그를 확인합니다.

```text
appdeployer> deployments list
appdeployer> deployments get dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
appdeployer> deployments logs dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
appdeployer> monitoring summary
appdeployer> deployments stop dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
appdeployer> exit
```

#### 안내형 입력

등록 명령에서 JSON 파일을 생략하면 필요한 값을 차례로 입력할 수 있습니다.

```text
appdeployer> apps add
AI App 등록 안내 (필수 항목은 빈 값으로 둘 수 없습니다)
앱 이름 (소문자/숫자/하이픈): my-ai-app
버전 [0.1.0]:
Artifact 종류 (package/git/binary/script) [script]:
Artifact URI: file:///opt/apps/my-ai-app/run.sh
실행 명령 [bash]:

appdeployer> runtimes add
appdeployer> targets add
appdeployer> deployments create
```

#### 추론과 메트릭

HTTP 서비스를 제공하는 `RUNNING` 배포는 추론 상태 확인과 호출이 가능합니다.

```text
appdeployer> inference health dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
appdeployer> inference invoke dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx examples/requests/inference-qwen-generate.json
appdeployer> metrics add dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx examples/requests/metric-cpu-sample.json
appdeployer> metrics list dep-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
appdeployer> monitoring metrics
```

명령 하나만 실행하는 방식도 지원합니다.

```powershell
go run ./cmd/appdeployer apps list
go run ./cmd/appdeployer apps delete <app-id> --yes
go run ./cmd/appdeployer deployments get <deployment-id>
```

`apps delete`는 App Registry 등록 정보만 삭제합니다. 해당 App 또는 App Version을 참조하는 Deployment가 없거나 모두 `STOPPED`이면 삭제할 수 있습니다. `STOPPED` Deployment 이력, Package/Git/Binary/Script 원본과 VM에 배포된 파일은 유지되며, 그 외 상태의 참조가 하나라도 있으면 409 오류로 삭제가 거부됩니다. 웹 콘솔에서는 Applications 표의 `등록 삭제` 버튼을 사용할 수 있습니다.

CLI 데이터는 기본적으로 `data/appdeployer-store.json`에 유지됩니다. 다른 경로를 사용하려면 `AIAPP_STORE_PATH` 환경변수를 설정합니다.

같은 이름과 버전의 App을 다시 등록하면 `app name/version already exists` 오류가 발생합니다. 독립된 시험 데이터가 필요할 때는 새 저장 경로를 지정합니다.

```powershell
$env:AIAPP_STORE_PATH = ".\data\my-test-store.json"
go run ./cmd/appdeployer
```

실행 파일이 필요하면 다음처럼 빌드합니다.

```powershell
go build -o .\bin\appdeployer.exe ./cmd/appdeployer
.\bin\appdeployer.exe
```

### 3. 로컬 API 서버 실행

```powershell
go run ./cmd/server
```

기본 서버 포트는 `8080`입니다. 서버 실행 후 API 문서는 아래에서 확인합니다.

```text
http://localhost:8080/swagger
http://localhost:8080/openapi.yaml
```

`cmd/server`에도 동일한 웹 콘솔이 포함되지만 저장소 기본값은 기존과 같이 메모리입니다. 영속 웹 실행에는 `cmd/web`을 권장합니다.

### 4. 기본 API 검증

```powershell
.\scripts\api-smoke.ps1 -BaseUrl http://localhost:8080
.\scripts\interface-smoke.ps1 -BaseUrl http://localhost:8080
```

증적 패키지를 한 번에 수집할 때는 다음 명령을 사용합니다.

```powershell
.\scripts\collect-evidence.ps1 -Port 18080
```

결과는 `deliverables/evidence/results/{timestamp}` 아래에 저장됩니다.

### 5. ai-ops-geon SSH 배포 검증

`ai-ops-geon` service-control API를 AppDeploy SSH runner로 원격 VM에 배포해 확인할 때는 `conf/aiops-config-ssh.json`을 먼저 조정합니다. 기본 설정은 SSH alias `nhn-cloud`, 로컬 AppDeploy 포트 `18088`, 원격 서비스 포트 `18089`, 로컬 터널 포트 `18189`를 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action deploy
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action stop
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action cleanup
```

주요 action은 다음과 같습니다.

| Action | 설명 |
| --- | --- |
| `package` | `../ai-ops-geon`의 service-control API를 Linux amd64 tar.gz로 패키징 |
| `deploy` | AppDeploy 서버 준비, App/Runtime/Target 등록, 원격 VM 배포, 원격 API 검증, SSH 터널 생성 |
| `status` | AppDeploy readiness, 최근 deployment 상태, 원격 `/healthz`, 로컬 터널 상태 확인 |
| `tunnel` | 기존 deployment에 대한 로컬 SSH 터널만 생성 또는 재사용 |
| `stop` | 관련 RUNNING deployment와 로컬 SSH 터널 중지. 원격 배포 파일은 유지 |
| `cleanup` | state에 기록된 원격 artifact 디렉터리를 안전 검증 후 삭제 |

실행 로그, 상태 파일, 패키지는 `tmp/aiops-geon-ssh-appdeploy-run/` 아래에 저장됩니다. 터널이 켜져 있으면 로컬에서 `http://localhost:18189`로 원격 ai-ops-geon API를 확인할 수 있습니다.

## License

AI App Deployer는 [Apache License 2.0](./LICENSE)에 따라 배포됩니다.
