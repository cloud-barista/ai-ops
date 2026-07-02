# 테스트 가이드

## 1. 목적

이 가이드는 1차년도 Go 기반 service-control prototype의 기능 test와 validation 절차를 정의합니다. 테스트는 prototype behavior를 검증하며, production performance 또는 standardized LLM benchmark quality를 증명하지 않습니다.

검증 결과를 제출 증적으로 정리할 때는 `docs/evidence/증적_패키지_가이드.md`를 기준으로 command, environment, JSON/log output을 함께 보존합니다.

## 2. Go Guard 테스트

```bash
cd go/aiops-guard
go test ./...
```

검증 항목:

- Go guard bounded-action validation

## 3. Service-Control API 테스트

```bash
cd go/service-control-api
go test ./...
```

검증 항목:

- API route behavior
- Service-control model behavior
- LLM policy selection logic
- Ops LLM dry-run/evaluator wiring
- Agent registry validation
- CPU/GPU placement 및 deployment-plan logic

## 4. Team Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

검증 항목:

- Ops LLM selection policy prototype
- Agent registry listing
- Agent bounded-action validation
- CPU/GPU VM placement recommendation
- AI 응용 배포·제어 계획 생성
- Mock 배포 dry-run 및 guard-readiness 검증
- 통합 service-operations readiness

## 5. System Validation

`validate-system`은 로컬과 VM에서 동일한 검증 command를 사용하기 위한 통합 검증 명령입니다.

로컬 검증:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --output-dir ../../runs/full-validation-local
```

VM 내부 검증:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --output-dir ../../runs/full-validation-vm
```

공통 검증 항목:

- Go version, Git branch, Git commit, hostname 기록
- `go/aiops-guard` 테스트
- `go/service-control-api` 테스트
- `team-validation`
- service-operations readiness
- 선택 사항: `--run-llm-benchmark` 사용 시 실제 LLM benchmark와 evaluator 실행

VM 추가 검증 항목:

- `nvidia-smi`
- GPU driver/CUDA visibility
- AWS instance metadata

주의: `--target vm`은 AWS GPU VM 내부에서 실행해야 합니다. 로컬 WSL에서 실행하면 GPU/metadata 검증이 실패하는 것이 정상입니다.

실제 LLM benchmark를 포함하는 로컬 검증:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/full-validation-local-executed
```

위 명령은 `--llm-dry-run`을 붙이지 않는 한 실제 endpoint를 호출합니다.

## 6. 기대 신호

기대되는 prototype-level output signal:

```text
selected_model = primary-ops-llm
selected_actual_model = to-be-evaluated-primary-model
benchmark_status = not_executed
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

이 신호는 Go API/CLI validation flow가 올바르게 연결되었음을 확인합니다. standardized LLM evaluation quality, production performance, live GPU scheduling, actual cloud provisioning을 증명하지 않습니다.

## 7. Ops LLM 평가 Dry-Run

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.json \
  --output-dir ../../runs/ops-llm-evaluation-dry-run \
  --dry-run

go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-dry-run/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-dry-run/evaluation_summary.json
```

기대 신호:

```text
benchmark_status = dry_run
selected_actual_model = ""
```

dry-run은 실제 LLM API를 호출하지 않으므로 최종 LLM 품질 benchmark 결과가 아닙니다.

## 8. Ops LLM 실제 실행 Benchmark

OpenAI-compatible endpoint가 실행 중이면 `--dry-run` 없이 실행합니다.

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/ops-llm-evaluation-executed

go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-executed/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-executed/evaluation_summary.json
```

기대 신호:

```text
benchmark_status = executed
dry_run = false
selected_actual_model = llama3.1:8b
```

endpoint가 없거나 model이 준비되지 않은 경우 benchmark command는 실패합니다. 이 실패는 실제 실행 검증이 수행되지 않았다는 명확한 증거로 보존합니다.

## 9. 검증 증거 파일

`team-validation` 또는 `validate-system`을 `--output-dir`와 함께 실행하면 validation evidence를 보존할 수 있습니다.

| 파일 | 검증 의미 |
| --- | --- |
| `00_team_validation_summary.json` | 전체 validation step 요약 |
| `01_select_ops_llm.json` | Ops LLM policy selection |
| `02_list_agents.json` | Registered agent list |
| `03_validate_agent_action.json` | Agent bounded-action validation |
| `04_recommend_inference_placement.json` | CPU/GPU VM placement recommendation |
| `05_plan_inference_deployment.json` | AI 응용 배포·제어 계획 생성 |
| `06_run_service_operations.json` | Integrated service-operations readiness |

`validate-system`은 추가로 `00_system_validation_summary.json`, `01_environment.json`, Go test output, LLM benchmark output, VM target의 GPU/metadata evidence를 저장합니다.

## 10. 실패 로그와 오류 메시지 보존

검증 실패 시 전체 terminal output과 생성 JSON 파일을 날짜가 포함된 directory에 보존합니다.

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/validation-YYYYMMDD-HHMMSS
```

권장 실패 로그 기록:

| 항목 | 보존 내용 |
| --- | --- |
| Command | 실패한 정확한 command |
| Environment | OS, Go 버전, branch, latest commit |
| Error output | 전체 stderr/stdout text |
| JSON evidence | 생성 JSON 파일 |
| Human note | 관찰된 실패와 다음 조치에 대한 짧은 설명 |

## 11. 사람 검토 항목

사람 검토자는 다음을 확인해야 합니다.

- 테스트가 올바른 Go module directory에서 실행되었는지
- README link와 문서 link가 정상적으로 연결되는지
- DOCX 제출본이 있다고 주장하는 경우 실제 파일이 존재하는지
- prototype boundary statement가 포함되어 있는지
- repository가 production readiness를 주장하지 않는지
- repository가 final standardized LLM benchmark result를 주장하지 않는지
- dry-run 결과를 actual LLM benchmark로 표현하지 않았는지
- actual LLM benchmark라고 주장하는 결과가 `benchmark_status = executed`인지
- VM 검증이라고 주장하는 결과가 실제 VM 내부에서 `--target vm`으로 실행되었는지
