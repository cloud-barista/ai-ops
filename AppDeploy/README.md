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
| [`cmd/server/`](cmd/server/) | Go/Echo 기반 AI App Deployer API 서버 진입점 |
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

### 1. 로컬 API 서버 실행

```powershell
go mod tidy
go run ./cmd/server
```

기본 서버 포트는 `8080`입니다. 서버 실행 후 API 문서는 아래에서 확인합니다.

```text
http://localhost:8080/swagger
http://localhost:8080/openapi.yaml
```

### 2. 기본 API 검증

```powershell
.\scripts\api-smoke.ps1 -BaseUrl http://localhost:8080
.\scripts\interface-smoke.ps1 -BaseUrl http://localhost:8080
```

증적 패키지를 한 번에 수집할 때는 다음 명령을 사용합니다.

```powershell
.\scripts\collect-evidence.ps1 -Port 18080
```

결과는 `deliverables/evidence/results/{timestamp}` 아래에 저장됩니다.

### 3. ai-ops-geon SSH 배포 검증

`ai-ops-geon` service-control API를 AppDeploy SSH runner로 원격 VM에 배포해 확인할 때는 `conf/aiops-config-ssh.json`을 먼저 조정합니다. 기본 설정은 SSH alias `nhn-cloud`, 로컬 AppDeploy 포트 `18088`, 원격 서비스 포트 `18089`, 로컬 터널 포트 `18189`를 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action deploy
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\aiops-deploy-ssh.ps1 -ConfigPath .\conf\aiops-config-ssh.json -Action stop
```

주요 action은 다음과 같습니다.

| Action | 설명 |
| --- | --- |
| `package` | `../ai-ops-geon`의 service-control API를 Linux amd64 tar.gz로 패키징 |
| `deploy` | AppDeploy 서버 준비, App/Runtime/Target 등록, 원격 VM 배포, 원격 API 검증, SSH 터널 생성 |
| `status` | AppDeploy readiness, 최근 deployment 상태, 원격 `/healthz`, 로컬 터널 상태 확인 |
| `tunnel` | 기존 deployment에 대한 로컬 SSH 터널만 생성 또는 재사용 |
| `stop` | 관련 RUNNING deployment와 로컬 SSH 터널 중지 |

실행 로그, 상태 파일, 패키지는 `tmp/aiops-geon-ssh-appdeploy-run/` 아래에 저장됩니다. 터널이 켜져 있으면 로컬에서 `http://localhost:18189`로 원격 ai-ops-geon API를 확인할 수 있습니다.

## License

AI App Deployer는 [Apache License 2.0](./LICENSE)에 따라 배포됩니다.
