# 설치 및 실행 가이드

## 1. 목적

이 가이드는 1차년도 Go 기반 service-control 기능 프로토타입의 설치와 실행 방법을 설명합니다. 기본 검증 경로는 로컬 실행이며 `mock` mode를 사용합니다. 기본 검증에는 live Kubernetes cluster나 실제 GPU VM provisioning이 필요하지 않습니다.

## 2. Go 버전 요구사항

- Go 1.25 이상을 권장합니다.
- service-control API dependency set이 `go mod tidy` 기준 `go 1.25.0`으로 정리되어 있어 두 Go module 모두 Go 1.25 계열을 기준으로 합니다.

Go 버전 확인:

```bash
go version
```

## 3. 저장소 설정

```bash
git checkout geon
git pull --ff-only origin geon
```

Go module dependency 다운로드:

```bash
cd go/service-control-api
go mod download

cd ../aiops-guard
go mod download
```

## 4. Team Validation 실행

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/my-first-validation
```

기대 통합 신호:

```text
valid = true
```

생성되는 검증 증거:

| 파일 | 의미 |
| --- | --- |
| `runs/my-first-validation/01_select_ops_llm.json` | Ops LLM policy selection 결과 |
| `runs/my-first-validation/02_list_agents.json` | Agent registry 목록 |
| `runs/my-first-validation/03_validate_agent_action.json` | Agent bounded-action validation |
| `runs/my-first-validation/04_recommend_inference_placement.json` | CPU/GPU VM placement recommendation |
| `runs/my-first-validation/05_plan_inference_deployment.json` | Deployment/control plan |
| `runs/my-first-validation/06_run_service_operations.json` | Integrated service-operations readiness |

## 5. CLI 명령 실행

Ops LLM policy selection:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

Agent registry listing:

```bash
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

CPU/GPU placement recommendation:

```bash
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Deployment/control plan:

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

통합 service-operations readiness:

```bash
go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --recovery-namespace aiops-demo \
  --recovery-deployment aiops-service \
  --mode mock \
  --guard-backend go
```

## 6. Ops LLM 평가 Dry-Run

실제 provider API를 호출하지 않고, Go 기반 scenario/candidate 연결과 평가 요약 생성을 검증합니다.

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

생성되는 검증 증거:

| 파일 | 의미 |
| --- | --- |
| `runs/ops-llm-evaluation-dry-run/model_outputs.jsonl` | scenario별 prompt, role label, actual model placeholder, dry-run status |
| `runs/ops-llm-evaluation-dry-run/evaluation_summary.json` | dry-run evaluation wiring summary |

dry-run 결과는 실제 LLM API benchmark 결과가 아닙니다. 실제 모델 응답이 기록되고 `benchmark_status = executed`인 경우에만 최종 모델 평가 결과로 해석합니다.

## 7. API 서버 실행

터미널 1:

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

터미널 2:

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/openapi.yaml
```

통합 API 예시:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

## 8. 기대 결과

기대되는 prototype-level signal:

```text
selected_model = primary-ops-llm
selected_actual_model = to-be-evaluated-primary-model
benchmark_status = not_executed
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

위 값은 prototype의 policy와 control-flow wiring을 검증합니다. 최종 표준 LLM benchmark result가 아닙니다.

## 9. Mock Mode

기본 `mock` mode는 live cluster를 변경하지 않고 service-control readiness structure를 생성하고 검증합니다. mock mode에서는 다음이 수행됩니다.

- AI 응용 배포·제어 manifest 생성
- simulated deployment dry-run output 생성
- agent review와 guard-readiness field 생성
- 실제 GPU VM provisioning 미수행
- live Kubernetes mutation 미수행

## 10. DOCX 변환

DOCX 제출본은 저장소에 포함되어 있습니다. 재생성이 필요한 경우 Bash 변환 script를 사용할 수 있습니다.

```bash
bash scripts/generate_docx_deliverables.sh
```

Windows PowerShell에서 Pandoc을 직접 사용할 수도 있습니다.

```powershell
pandoc docs/submission/requirements_definition.md -o docs/submission/requirements_definition.docx
pandoc docs/deliverables/01_llm_operation_management_design.md -o docs/deliverables/docx/01_LLM_Operation_Management_Design.docx
pandoc docs/deliverables/02_agent_registration_management_prototype.md -o docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx
pandoc docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md -o docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx
```

변환 도구가 없으면 Markdown 원본을 기준 산출물로 유지하고, DOCX 파일은 변환 대상 파일로 관리합니다.
