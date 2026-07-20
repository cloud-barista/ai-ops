# 테스트 가이드

## 1. 정적·단위 검증

```bash
make test
make vet
```

검증 대상은 실제 LLM Manifest 생성, Go Guard, AppDeploy client·polling·retry 판단, LLM 선정, Agent Registry, 실제 VM 보조 적합성과 HTTP API입니다.

Planner 핵심 패키지만 빠르게 확인하려면 다음을 실행합니다.

```bash
cd go/service-control-api
go test ./internal/appdeploy ./internal/deploymentplanner
```

## 2. Team validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/team-validation
```

| 단계 | 의미 |
| --- | --- |
| `select-ops-llm` | LLM 정책 선정 |
| `list-agents` | 등록 에이전트 조회 |
| `validate-agent-action` | bounded Action 검증 |
| `validate-vm-suitability` | 기록된 실제 VM snapshot 적합성 검증 |
| `plan-ai-application-control` | 범용 외부 에이전트 handoff 계획 |
| `run-service-operations` | 전체 계획 결과 통합 |

## 3. 로컬 검증

```bash
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-local
```

## 4. VM 검증

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --output-dir ../../runs/full-validation-vm
```

VM에서 추가되는 증적:

- `04_vm_nvidia_smi.txt`
- `05_vm_aws_metadata.json`
- `06_vm_resource_snapshot.json`
- CPU·메모리·GPU·VRAM·driver·CUDA 값이 반영된 team-validation 결과

`performance.status=not_measured`는 실패가 아니라 workload latency·throughput·cost를 아직 측정하지 않았다는 뜻입니다.

## 5. 실제 LLM benchmark

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/full-validation-vm-llm
```

실제 endpoint 응답이 있어야 `benchmark_status=executed`가 됩니다. `dry_run`과 `not_executed`는 실제 모델 성능 결과로 해석하지 않습니다.

실제 LLM 자동화 Action까지 검증하려면 다음 옵션을 추가합니다.

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-llm-decision \
  --llm-decision-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --llm-decision-candidate-id local-ollama-ops-llm \
  --output-dir ../../runs/full-validation-vm-action
```

## 6. 결과 해석 경계

| 신호 | 의미 |
| --- | --- |
| `resource_checks_passed=true` | 선언된 실제 VM 자원 조건 통과 |
| `compatibility_status=provisionally_compatible` | 자원 통과, 성능 미측정 |
| `selected_executor` | 조건에 맞는 등록 외부 실행 에이전트 발견 |
| `decision_execution_status=executed` | 실제 LLM endpoint가 Action 제안을 반환함 |
| `guard.status=approved` | LLM Action이 VM·capability·bounded Action 정책을 통과함 |
| `execution_status=not_executed` | 계획만 생성, 외부 제어 미실행 |
| `operation_pipeline_ready=false` | 실제 운영 실행 완료를 주장하지 않음 |

## 7. AppDeploy Planner 계약 검증

`go test ./...`에는 테스트용 OpenAI-compatible LLM과 AppDeploy HTTP 서버를 사용하는 end-to-end 계약 테스트가 포함됩니다. 테스트는 다음 순서를 확인합니다.

```text
LLM Manifest 생성
  -> Go Guard 승인
  -> POST /api/v1/deployments
  -> GET /api/v1/deployments/{deployment_id}
  -> GET /api/v1/deployments/{deployment_id}/logs
  -> RUNNING 결과 반환
```

이 테스트는 실제 HTTP 요청과 계약 처리를 검증하지만 테스트 프로세스 안의 mock upstream을 사용합니다. 실제 통합 완료를 주장하려면 별도로 실제 LLM endpoint, 실행 중인 AppDeploy, 등록된 App Version과 준비된 Target을 사용해 `run-appdeploy-planner`를 수행해야 합니다.

`runs/`는 로컬 증적이며 Git 추적 대상이 아닙니다. 제출할 증적만 검토 후 `docs/evidence/artifacts/`에 redacted copy로 보존합니다.
