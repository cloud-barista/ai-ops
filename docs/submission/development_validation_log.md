# 개발/테스트 검증 로그

## 1. 목적

이 문서는 1차년도 Go 기반 service-control prototype의 validation command, expected output, log preservation method, human verification item, current known limitation을 기록합니다.

## 2. 검증 명령

Go guard test:

```bash
cd go/aiops-guard && go test ./...
```

Service-control API test:

```bash
cd go/service-control-api && go test ./...
```

Integrated team validation:

```bash
cd go/service-control-api && go run ./cmd/aiops-service-control team-validation
```

Repository-level validation:

```bash
make test
make vet
```

## 3. 기대 출력

현재 주 배포 경로의 기대 signal:

```text
generation.execution_status = executed
generation.guard_valid = true
manifest.kind = DeploymentManifest
deployment.status = RUNNING 또는 AppDeploy가 반환한 명시적 상태
```

위 결과는 실제 LLM endpoint와 AppDeploy 서버가 준비된 통합 환경에서만 기대합니다. 기본 단위 테스트는 mock HTTP server를 사용해 계약, Go Guard, polling과 오류 처리를 검증합니다.

기대되는 Go test behavior:

```text
go test ./... exits with status 0
```

## 4. 오류 로그 정책

명령 실패 시 다음을 보존합니다.

- 정확한 command
- working directory
- 전체 stdout 및 stderr
- Go version
- Git branch와 latest commit
- 생성 JSON 파일
- 추정 원인을 설명하는 human note

오류 메시지는 과도하게 의역하지 않습니다. 정확한 error text를 기록하고, 해석은 별도 짧은 문장으로 덧붙입니다.

## 5. 사람 검증 항목

사람 검토자는 다음을 확인해야 합니다.

- test output이 올바른 directory에서 생성되었는지
- README link가 기존 repository file을 가리키는지
- OpenAPI YAML이 존재하고 연결되어 있는지
- 필수 Markdown deliverable이 존재하는지
- DOCX file이 있다고 설명하기 전에 실제 존재하는지
- prototype boundary statement가 있는지
- LLM policy value가 수동 정의 prototype baseline으로 설명되었는지
- production-ready claim이 추가되지 않았는지
- final standardized LLM benchmark claim이 추가되지 않았는지

## 6. 현재 알려진 한계

- 실제 LLM 실행에는 enabled candidate와 OpenAI-compatible endpoint가 필요합니다.
- 실제 배포에는 AppDeploy 서버, 등록 App Version과 준비된 Target Profile이 필요합니다.
- Planner는 VM을 직접 생성하거나 Target·Runtime Adapter를 선택하지 않습니다.
- LLM policy value는 수동 정의된 prototype policy baseline입니다.
- final quantitative model reporting에는 fixed prompt, dataset, metric, scoring rule을 갖춘 controlled per-model evaluation run이 필요합니다.

## 7. 최신 검증 기록

검증 날짜: 2026-07-20

| 항목 | 결과 |
| --- | --- |
| `make test` | WSL Ubuntu-22.04에서 전체 Go package test, pass |
| `make vet` | WSL Ubuntu-22.04에서 두 Go module 정적 검사, pass |
| Planner/AppDeploy package | `internal/appdeploy`, `internal/deploymentplanner` test, pass |
| Git 상태 | `geon` branch가 `origin/geon`과 동기화된 상태에서 검증 시작 |

## 8. 최신 명령 증거

Go guard tests:

```text
?    github.com/cloud-barista/ai-ops/go/aiops-guard/cmd/aiops-guard [no test files]
ok   github.com/cloud-barista/ai-ops/go/aiops-guard/internal/guard
```

Service-control API 주요 package:

```text
ok   kyunghee-aiops/service-control-api/cmd/aiops-service-control
?    kyunghee-aiops/service-control-api/cmd/service-control-api [no test files]
ok   kyunghee-aiops/service-control-api/internal/api
ok   kyunghee-aiops/service-control-api/internal/appdeploy
ok   kyunghee-aiops/service-control-api/internal/automation
ok   kyunghee-aiops/service-control-api/internal/benchmark
ok   kyunghee-aiops/service-control-api/internal/deploymentplanner
ok   kyunghee-aiops/service-control-api/internal/llmclient
```

Go vet:

```text
go/aiops-guard: go vet ./... pass
go/service-control-api: go vet ./... pass
```

환경 note:

```text
Windows PowerShell did not have go on PATH, so Go validation was executed
through WSL Ubuntu-22.04 using /usr/local/go/bin/go.
```
