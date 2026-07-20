# Ops LLM 평가 방법

## 1. 목적

이 문서는 Go 기반 service-control prototype에서 Ops LLM 후보를 어떻게 평가하는지 정의합니다.

평가 목적은 일반 지식 benchmark가 아니라, 본 과제의 운영 판단 흐름에 맞는 LLM을 고르는 것입니다.

- JSON 응답 형식 준수
- bounded action 준수
- CPU/GPU VM 배치 판단
- SLO 위반 상황의 scale/rollback/monitor 판단
- reason/confidence 필드 포함
- 응답 지연 시간

## 2. 평가 모드

| 모드 | 의미 |
| --- | --- |
| `dry_run` | 실제 LLM API를 호출하지 않고 prompt/output/evaluator 연결만 검증 |
| `executed` | enabled candidate의 OpenAI-compatible endpoint를 실제 호출 |
| `not_executed` | 후보가 비활성화되었거나 endpoint/API key 문제로 실행되지 않음 |

제출/보고서에서 실제 LLM 품질 평가로 말할 수 있는 것은 `benchmark_status = executed` 결과뿐입니다.

## 3. 입력 파일

| 파일 | 역할 |
| --- | --- |
| `data/ops_llm_eval_scenarios.jsonl` | Ops LLM 평가 scenario set |
| `config/ops_llm_eval_candidates.json` | 안전한 기본 candidate 설정. 기본적으로 provider 호출 없음 |
| `config/ops_llm_eval_candidates.local_ollama.json` | local validation용 example provider 설정. 실행 결과가 아니라 candidate config |
| `config/ops_llm_eval_candidates.local_multi_ollama.json` | local OpenAI-compatible endpoint에서 여러 example model 후보를 비교하기 위한 candidate config |
| `config/ops_llm_eval_candidates.openai_compatible.example.json` | OpenAI-compatible provider 교체 예시 |
| `config/ops_llm_benchmark.json` | service-control prototype의 정책 기반 LLM selection baseline |

## 4. Dry-Run 실행

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
executed_count = 0
dry_run_count > 0
selected_actual_model = ""
```

## 5. 실제 LLM 실행

OpenAI-compatible endpoint가 준비되어 있으면 `--dry-run` 없이 실행합니다.

Ollama는 local validation용 example provider입니다. benchmark runner는 특정 LLM runtime에 종속되지 않으며, vLLM, LM Studio, OpenAI API, Azure OpenAI, 연구용 GPU 서버 endpoint, AI-MCMP 또는 배포 계층이 제공하는 OpenAI-compatible endpoint로 교체할 수 있습니다.

예시: local example endpoint

```bash
ollama serve
ollama pull llama3.2:3b
```

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
selected_actual_model = llama3.2:3b
```

Candidate config는 실행 결과가 아닙니다. 실제 executed benchmark는 `runs/.../evaluation_summary.json`에 `benchmark_status = executed`가 기록된 경우에만 주장할 수 있습니다.

## 6. Local Example Multi-Model 실행

여러 local example model을 비교하려면 다음과 같이 실행합니다. 아래 Ollama 명령은 로컬 예시이며 통합 환경의 필수 조건이 아닙니다.

```bash
ollama serve
ollama pull qwen2.5:3b
ollama pull llama3.2:3b
ollama pull gemma2:2b
```

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.local_multi_ollama.json \
  --output-dir ../../runs/ops-llm-evaluation-local-multi-executed

go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-local-multi-executed/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-local-multi-executed/evaluation_summary.json
```

`config/ops_llm_eval_candidates.local_multi_ollama.json`은 실제 결과가 아니라 실행 후보 설정입니다. 실제 비교 결과는 `runs/.../evaluation_summary.json`에 `benchmark_status = executed`가 기록된 경우에만 주장할 수 있습니다.

실제 multi-LLM 비교가 완료되었다고 말하려면 다음 조건을 모두 만족해야 합니다.

- `--dry-run`을 사용하지 않아야 함
- candidate가 2개 이상이어야 함
- 각 candidate endpoint가 실제 응답해야 함
- `benchmark_status = executed`
- `dry_run = false`
- candidate별 `executed > 0`
- `selected_actual_model`이 존재해야 함
- candidate별 `average_score`가 계산되어야 함

현재 환경에서 endpoint가 실행 중이 아니면 `benchmark_status = executed`인 가짜 결과를 만들지 않습니다.

## 7. 통합 Endpoint 설정 예시

통합 환경에서는 local example config 대신 다음 예시 파일을 복사하여 endpoint, provider, actual model, API key environment variable을 교체합니다.

```bash
cp ../../config/ops_llm_eval_candidates.openai_compatible.example.json \
  ../../config/ops_llm_eval_candidates.integration.local.json
```

이 example config는 실행 결과가 아니며 기본 `enabled=false` 상태입니다. 실제 실행 전에 `endpoint`, `actual_model`, `provider`, `api_key_env`, `enabled`를 대상 환경에 맞게 조정해야 합니다. 외부 플랫폼과 연구용 GPU 서버도 같은 파일을 사용하며, 구조화 응답을 지원하는 provider에는 `json_mode=true`를 사용할 수 있습니다.

## 8. System Validation과 연결

로컬 또는 VM 검증에 실제 LLM benchmark를 포함하려면 다음과 같이 실행합니다.

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/full-validation-local-executed
```

VM 내부에서는 `--target vm`으로 바꿔 실행합니다.

```bash
go run ./cmd/aiops-service-control validate-system \
  --target vm \
  --run-llm-benchmark \
  --llm-candidates ../../config/ops_llm_eval_candidates.local_ollama.json \
  --output-dir ../../runs/full-validation-vm-executed
```

API 통합 검증도 `validate-system`에 포함할 수 있습니다.

```bash
go run ./cmd/aiops-service-control validate-system \
  --target local \
  --run-api-integration \
  --api-port 18080 \
  --output-dir ../../runs/full-validation-local-with-api
```

`--run-llm-benchmark` 또는 `--run-api-integration`을 주지 않으면 해당 optional step은 실패가 아니라 `skipped=true`와 reason으로 기록됩니다.

## 9. 점수 산정

`evaluate-ops-llm-outputs`는 실행된 응답만 점수화합니다.

| 항목 | 배점 |
| --- | --- |
| JSON validity | 25 |
| allowed action validity | 25 |
| required fields present | 20 |
| expected action match | 20 |
| latency score | 10 |

dry-run row는 점수화하지 않으며, executed row만 candidate 평균 점수에 반영합니다.

dry-run summary는 scenario, candidate, output, evaluator 연결이 깨지지 않았는지 확인하는 pipeline evidence입니다. dry-run 결과에서 보이는 평균 점수나 candidate summary는 실제 LLM 품질 점수로 해석하지 않습니다.
