# 테스트 가이드

## 1. 목적

이 가이드는 1차년도 Go 기반 service-control prototype의 기능 test와 validation 절차를 정의합니다. 테스트는 prototype behavior를 검증하며, production performance 또는 standardized LLM benchmark quality를 증명하지 않습니다.

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

## 5. 기대 신호

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

## 6. Ops LLM 평가 Dry-Run

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

## 7. 검증 증거 파일

`team-validation`을 `--output-dir`와 함께 실행하면 다음 JSON 파일을 validation evidence로 보존할 수 있습니다.

| 파일 | 검증 의미 |
| --- | --- |
| `00_team_validation_summary.json` | 전체 validation step 요약 |
| `01_select_ops_llm.json` | Ops LLM policy selection |
| `02_list_agents.json` | Registered agent list |
| `03_validate_agent_action.json` | Agent bounded-action validation |
| `04_recommend_inference_placement.json` | CPU/GPU VM placement recommendation |
| `05_plan_inference_deployment.json` | AI 응용 배포·제어 계획 생성 |
| `06_run_service_operations.json` | Integrated service-operations readiness |

## 8. 실패 로그와 오류 메시지 보존

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

## 9. 사람 검토 항목

사람 검토자는 다음을 확인해야 합니다.

- 테스트가 올바른 Go module directory에서 실행되었는지
- README link와 문서 link가 정상적으로 연결되는지
- DOCX 제출본이 있다고 주장하는 경우 실제 파일이 존재하는지
- prototype boundary statement가 포함되어 있는지
- repository가 production readiness를 주장하지 않는지
- repository가 final standardized LLM benchmark result를 주장하지 않는지
- dry-run 결과를 actual LLM benchmark로 표현하지 않았는지
