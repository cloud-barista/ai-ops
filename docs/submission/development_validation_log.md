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

## 3. 기대 출력

기대되는 prototype-level signal:

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

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

- 기본 검증 경로는 mock mode를 사용합니다.
- actual GPU VM provisioning은 local default validation path 밖입니다.
- live Kubernetes mutation은 기본적으로 수행하지 않습니다.
- LLM policy value는 수동 정의된 prototype policy baseline입니다.
- final quantitative model reporting에는 fixed prompt, dataset, metric, scoring rule을 갖춘 controlled per-model evaluation run이 필요합니다.

## 7. 최신 검증 기록

검증 날짜: 2026-06-29

| 항목 | 결과 |
| --- | --- |
| Go guard tests | WSL Ubuntu-22.04에서 `/usr/local/go/bin/go test ./...` 실행, pass |
| Service-control API tests | WSL Ubuntu-22.04에서 `/usr/local/go/bin/go test ./...` 실행, pass |
| Team validation | WSL Ubuntu-22.04에서 실행, `valid = true` |
| Team validation output directory | `runs/submission-validation-20260629-131247/` |
| DOCX conversion | PowerShell `pandoc`으로 실행, DOCX 4개 생성 후 `python-docx`로 구조 재확인 |
| Link validation | 로컬 Markdown link 확인, pass |

## 8. 최신 명령 증거

Go guard tests:

```text
?    github.com/cloud-barista/ai-ops/go/aiops-guard/cmd/aiops-guard [no test files]
ok   github.com/cloud-barista/ai-ops/go/aiops-guard/internal/guard
```

Service-control API tests:

```text
?    kyunghee-aiops/service-control-api/cmd/aiops-service-control [no test files]
?    kyunghee-aiops/service-control-api/cmd/service-control-api [no test files]
ok   kyunghee-aiops/service-control-api/internal/api
```

Team validation summary:

```text
valid = true
select-ops-llm = true
list-agents = true
validate-agent-action = true
recommend-inference-placement = true
plan-inference-deployment = true
run-service-operations = true
```

DOCX structural validation:

```text
requirements_definition.docx = generated and reopened
01_LLM_Operation_Management_Design.docx = generated and reopened
02_Agent_Registration_Management_Prototype.docx = generated and reopened
03_AI_Application_Deployment_Control_Optimization_Strategy.docx = generated and reopened
```

환경 note:

```text
Windows PowerShell did not have go on PATH, so Go validation was executed
through WSL Ubuntu-22.04 using /usr/local/go/bin/go.
```

DOCX visual render note:

```text
DOCX files were generated and structurally validated. Visual render QA with
the local document renderer could not be completed because soffice/libreoffice
was not available in the current environment.
```
