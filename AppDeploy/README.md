# KHU AI App Deployer

경희대학교 담당 범위인 `AI 반도체 기반 AI 응용 배포 및 운용 구조 설계`와
`CPU/GPU VM 기반 AI 응용 등록·배포 프로토타입`을 관리하는 작업 패키지이다.

1차년도 구현 범위는 Go/Echo 기반 Control Plane, App 등록, Runtime/Target Profile,
Resource Check, CPU/GPU VM 배포, 로그, 모니터링, inference proxy, stop 제어이다.
Docker, Kubernetes, OCI Image, Container Registry 기반 배포는 현재 범위에서 제외한다.

## Quick Start

```powershell
go mod tidy
go run ./cmd/server
```

서버 실행 후 API 문서는 아래에서 확인한다.

```text
http://localhost:8080/swagger
http://localhost:8080/openapi.yaml
```

기본 검증은 다음 스크립트를 사용한다.

```powershell
.\scripts\api-smoke.ps1 -BaseUrl http://localhost:8080
.\scripts\interface-smoke.ps1 -BaseUrl http://localhost:8080
```

## Current Status

| 영역 | 상태 |
| --- | --- |
| API 서버 | Go + Echo, `/api/v1`, request_id middleware |
| OpenAPI/Swagger | `contracts/openapi/openapi.yaml`, `/swagger`, `/openapi.yaml` |
| App Registry | App Spec 등록, version 관리, container artifact 거부 |
| CPU VM Adapter | dry-run, SSH runner, local `file://` script upload, process stop |
| GPU VM Adapter | `nvidia-smi` readiness, SSH runner, GPU VM 배포 방식 테스트 완료, process stop |
| Inference Proxy | `GET /inference/{deployment_id}/health`, `POST /inference/{deployment_id}/invoke` |
| Monitoring | summary, runtime health, alarms, metric placeholder |
| External API | ETRI AI-Infra mock/fixture skeleton, 실 API는 계약 확정 후 구현 |

## Documentation

문서 지도는 `docs/README.md`를 먼저 본다. 이 프로젝트는 산출물 문서를 많이 늘리기보다 프레임워크 프롬프트, OpenAPI, 로그·에러, 시험 증적을 중심으로 관리한다.

| 문서 | 용도 |
| --- | --- |
| `docs/prompts/프레임워크_공유_프롬프트.md` | 개발·검증·문서 정리 공통 프롬프트 |
| `contracts/openapi/openapi.yaml` | API source of truth |
| `docs/ops/로그_에러_가이드.md` | 로그·에러 메시지 작성 기준 |
| `docs/evidence/증적_패키지_가이드.md` | 제출 증적 구성 기준 |
| `docs/release/1차년도_제출_패키지_체크리스트.md` | 제출 전 점검표 |

## Agent Work Order

작업 전에는 `AGENTS.md`와 아래 문서를 기준으로 범위를 맞춘다.

1. `agent_md/00_scope_common_contract.md`
2. 관련 `agent_md/*.md`
3. `contracts/openapi/openapi.yaml`
4. `contracts/schemas/*.json`
5. `examples/requests/*.json`
