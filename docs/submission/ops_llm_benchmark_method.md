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
| `config/ops_llm_eval_candidates.local_ollama.json` | local OpenAI-compatible endpoint 실행용 설정 |
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
selected_actual_model = ""
```

## 5. 실제 LLM 실행

OpenAI-compatible endpoint가 준비되어 있으면 `--dry-run` 없이 실행합니다.

예시: Ollama local endpoint

```bash
ollama serve
ollama pull llama3.1:8b
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
selected_actual_model = llama3.1:8b
```

## 6. System Validation과 연결

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

## 7. 점수 산정

`evaluate-ops-llm-outputs`는 실행된 응답만 점수화합니다.

| 항목 | 배점 |
| --- | --- |
| JSON validity | 25 |
| allowed action validity | 25 |
| required fields present | 20 |
| expected action match | 20 |
| latency score | 10 |

dry-run row는 점수화하지 않으며, executed row만 candidate 평균 점수에 반영합니다.
