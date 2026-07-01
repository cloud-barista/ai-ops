# Ops LLM 평가 방법

## 1. 목적

이 문서는 1차년도 service-control prototype에서 Ops LLM 후보를 어떻게 평가 대상으로 연결하는지 정의합니다. 현재 저장소는 실제 provider LLM API benchmark를 완료했다고 주장하지 않습니다. 대신 Go 기반 dry-run/evaluator를 제공하여 향후 동일한 scenario set으로 실제 모델 응답을 수집하고 평가할 수 있게 합니다.

## 2. 핵심 경계

| 항목 | 현재 상태 |
| --- | --- |
| 실행 언어 | Go |
| 별도 Python runner | 사용하지 않음 |
| 기본 실행 | dry-run |
| 실제 LLM API 호출 | 기본값에서는 실행하지 않음 |
| 현재 benchmark status | `dry_run` 또는 `not_executed` |

`primary-ops-llm`, `low-cost-ops-llm`, `code-cross-check-agent`는 내부 역할 label입니다. 실제 provider model 이름은 `actual_model` 또는 `selected_actual_model` 필드로 별도 관리합니다.

## 3. 입력 파일

| 파일 | 역할 |
| --- | --- |
| `data/ops_llm_eval_scenarios.jsonl` | Ops LLM 평가 scenario set |
| `config/ops_llm_eval_candidates.json` | role label과 future actual model 후보 연결 |
| `config/ops_llm_benchmark.json` | service-control prototype의 정책 기반 LLM selection baseline |

`config/ops_llm_eval_candidates.json`의 후보는 기본적으로 `enabled: false`입니다. 따라서 로컬 검증에서는 provider API key가 있더라도 실수로 외부 API를 호출하지 않습니다.

## 4. Dry-Run 실행

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.json \
  --output-dir ../../runs/ops-llm-evaluation-dry-run \
  --dry-run
```

생성 파일:

```text
runs/ops-llm-evaluation-dry-run/model_outputs.jsonl
```

이 파일은 각 scenario와 candidate에 대해 평가 prompt, role label, actual model placeholder, benchmark status를 기록합니다. dry-run 결과는 실제 LLM 품질 점수가 아닙니다.

## 5. 평가 요약 생성

```bash
go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/ops-llm-evaluation-dry-run/model_outputs.jsonl \
  --summary ../../runs/ops-llm-evaluation-dry-run/evaluation_summary.json
```

생성 파일:

```text
runs/ops-llm-evaluation-dry-run/evaluation_summary.json
```

dry-run 입력을 평가하면 `benchmark_status = dry_run`이 유지됩니다. 이 경우 `selected_actual_model`은 최종 선정 모델로 채우지 않습니다.

## 6. 실제 모델 평가로 확장하는 방법

실제 provider 또는 local OpenAI-compatible endpoint를 평가하려면 다음 절차가 필요합니다.

| 단계 | 내용 |
| --- | --- |
| 1 | `config/ops_llm_eval_candidates.json`에 실제 endpoint와 model 이름 설정 |
| 2 | 필요한 API key를 환경 변수로 설정 |
| 3 | 해당 candidate만 `enabled: true`로 변경 |
| 4 | `run-ops-llm-benchmark`를 `--dry-run` 없이 실행 |
| 5 | `evaluate-ops-llm-outputs`로 JSON 응답을 평가 |

실제 실행 결과가 존재하고 `benchmark_status = executed`인 경우에만 `selected_actual_model`을 최종 평가 선정 모델로 해석할 수 있습니다.
