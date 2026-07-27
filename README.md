# AI-MCMP / AI App Deployer

AI App Deployer는 AI 자동화 에이전트가 전달한 Application과
DeploymentRequest를 검증·배치·실행하는 headless Go/Echo Control Plane이다.
외부 ETRI 시스템이나 원격 VM 없이도 Local Provider와 실제 로컬 프로세스로
전체 배포 흐름을 검증할 수 있다.

## 빠른 실행

```powershell
Set-Location .\AppDeploy
$env:RESOURCE_PROVIDER = "local"
$env:PLACEMENT_PROVIDER = "local"
go run ./cmd/server
```

기본 API 주소는 `http://localhost:8080`이며, API prefix는 `/api/v1`이다.

재현 가능한 로컬 E2E 데모:

```powershell
Set-Location .\AppDeploy
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-demo.ps1
```

데모는 App 등록, VM 2개 등록, 결정론적 placement, 실제 프로세스 실행,
로그 조회, 중지, 자원 예약 반환을 확인한다.

## 구성

```text
Automation Agent
 ├─ Application       → App Management → Application Repository
 └─ DeploymentRequest → Orchestrator
                         ├─ ResourceInformationProvider
                         ├─ PlacementProvider
                         ├─ RuntimeAdapter
                         └─ State/Event/Log/Reservation Store
```

현재 로컬 구현은 다음과 같다.

- Local VM Registry: VM 상태, 자원, Runtime, labels, 비용, 예약량 관리
- Local Scheduler: READY와 요구사항을 필터링하고
  `min_cost → 잔여 자원 → VM ID` 순서로 선택
- Local Process Runtime: `os/exec`, PID, stdout/stderr, 상태, stop 지원
- File/Memory Repository: 원본 Application과 배포 상태·이벤트 저장

## 주요 경로

| 경로 | 역할 |
| --- | --- |
| `AppDeploy/cmd/server` | API 서버 |
| `AppDeploy/cmd/appdeployer` | 선택적 CLI |
| `AppDeploy/internal/app` | Application 검증·Registry |
| `AppDeploy/internal/deployment` | Orchestrator |
| `AppDeploy/internal/provider` | Local/ETRI Provider 경계 |
| `AppDeploy/internal/runtime` | Runtime Adapter 및 Router |
| `AppDeploy/internal/store` | Memory/File 저장소와 예약 |
| `AppDeploy/contracts/openapi/openapi.yaml` | API source of truth |
| `AppDeploy/docs/architecture.md` | 책임 경계와 교체 지점 |
| `AppDeploy/scripts/local-demo.ps1` | 로컬 E2E 데모 |

## 검증

현재 워크스페이스는 Go 1.25.0으로 검증한다.

```powershell
Set-Location .\AppDeploy
$env:GOCACHE = ".\\.gocache"
go test ./...
go vet ./...
```

문서와 API 사용법은 [AppDeploy/README.md](AppDeploy/README.md)와
[문서 지도](AppDeploy/docs/README.md)를 참고한다.

## ETRI 연동 범위

설정으로 `RESOURCE_PROVIDER=etri`, `PLACEMENT_PROVIDER=etri`를 선택할 수
있지만 실제 ETRI endpoint, DTO, 인증 계약은 아직 추측하지 않는다.
`ETRIResourceMetadataAdapter`와 `ETRIPlacementAdapter`는 계약 확정 후
교체할 Stub이다. 누락된 endpoint는 오류로 처리하며 local fallback하지 않는다.

## 범위 제외

Docker, Docker Compose, Kubernetes, OCI image, Container Registry는 현재
프로토타입 범위가 아니다. 실제 원격 VM과 ETRI API는 승인된 접속정보와
파트너 계약이 제공된 후 별도 통합 검증한다.
