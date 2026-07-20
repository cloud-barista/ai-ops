# 설치 및 실행 가이드

## 1. 환경

- Ubuntu 22.04 또는 WSL Ubuntu 22.04
- Go 1.25 이상
- Git
- 실제 VM 검증 시 AWS VM metadata 접근과 `nvidia-smi`
- 실제 LLM 평가 시 OpenAI-compatible endpoint

Python virtual environment는 핵심 Go 실행에 필요하지 않습니다.

## 2. 저장소 준비

```bash
git checkout geon
git pull --ff-only origin geon
go version
git log --oneline -1
```

```bash
make test
make vet
```

## 3. 기본 통합 검증

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/team-validation
```

이 명령은 저장소의 기록된 실제 VM snapshot을 사용합니다. VM을 생성하거나 변경하지 않습니다.

## 4. 개별 기능

LLM 선정:

```bash
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

에이전트 목록과 Action 검증:

```bash
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json

go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationAutomationAgent \
  --action observe_status
```

실제 VM snapshot 검증과 handoff 계획:

```bash
go run ./cmd/aiops-service-control validate-vm-suitability \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference

go run ./cmd/aiops-service-control plan-ai-application-control \
  --requirements ../../config/vm_workload_requirements.json \
  --vm-snapshot ../../docs/evidence/artifacts/vm_20260707_resource_snapshot.json \
  --workload llm-chat-inference
```

## 5. API 서버

터미널 1:

```bash
go run ./cmd/service-control-api
```

터미널 2:

```bash
curl http://127.0.0.1:8080/healthz
```

전체 API flow:

```bash
go run ./cmd/aiops-service-control api-integration-validation \
  --output-dir ../../runs/api-integration-local \
  --port 18080
```

AppDeploy Planner 연계 시에는 실제 LLM endpoint와 AppDeploy 서버를 먼저 실행하고 다음 값을 지정합니다.

```bash
export AIOPS_LLM_CANDIDATES_PATH=../../config/ops_llm_eval_candidates.local_ollama.json
export AIOPS_APPDEPLOY_BASE_URL=http://127.0.0.1:8081/api/v1

go run ./cmd/service-control-api
```

CLI로 동일한 흐름을 실행할 수도 있습니다.

```bash
go run ./cmd/aiops-service-control run-appdeploy-planner \
  --request "GPU 1개와 메모리 16Gi가 필요한 추론 앱을 배포해 주세요." \
  --app-version-id appver-llm-inference-v1 \
  --candidate-id local-ollama-ops-llm \
  --candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --appdeploy-base-url http://127.0.0.1:8081/api/v1
```

`app_version_id`는 AppDeploy에 미리 등록되어 있어야 합니다. Target Profile과 Runtime Adapter 선택은 AppDeploy가 수행합니다.

## 6. VM에서 전체 검증

CB-Tumblebug 또는 다른 인프라 계층에서 VM을 생성한 뒤 SSH 접속합니다. VM 안에서 저장소를 준비하고 다음을 실행합니다.

```bash
cd ~/ai-ops/go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-vm
```

`validate-system --target vm`은 현재 VM에서 직접 다음을 수집합니다.

- AWS instance type, region, availability zone
- CPU core와 system memory
- GPU model과 VRAM
- NVIDIA driver와 CUDA visibility
- `performance.status` (`not_measured`가 기본)

수집된 `06_vm_resource_snapshot.json`은 같은 실행의 team-validation에 자동 전달됩니다.

## 7. 실제 LLM endpoint 포함

Endpoint와 모델이 실행 중인 경우:

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --run-llm-decision \
  --llm-decision-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --llm-decision-candidate-id local-ollama-ops-llm \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-vm-complete
```

`benchmark_status=executed`는 Ops benchmark가 실제 실행되었음을 뜻합니다. 별도로 `07_llm_automation_action.json`의 `decision.decision_execution_status=executed`를 확인해야 실제 LLM이 배포·제어 Action을 제안한 것입니다. Ollama는 로컬 또는 VM 검증용 예시이며 다른 OpenAI-compatible endpoint로 교체할 수 있습니다.

## 8. 결과 확인

```text
00_system_validation_summary.json
01_environment.json
04_vm_nvidia_smi.txt
05_vm_aws_metadata.json
06_vm_resource_snapshot.json
07_llm_automation_action.json
team-validation/00_team_validation_summary.json
```

`decision_execution_status=executed`와 `handoff.execution_status=not_executed`는 동시에 나타날 수 있습니다. 전자는 LLM 판단이 실제 수행되었다는 뜻이고, 후자는 외부 배포·제어 실행은 아직 수행되지 않았다는 뜻입니다.
