# LLM 운영 관리 구조 설계서

영문 제목: LLM Operation Management Structure Design

## 1. 설계 목적

본 설계서는 Ops 분석 시험을 통해 서비스 제어에 적합한 LLM 후보를 선정하고, 선정 결과를 AI 운영 관리 흐름에 연결하는 구조를 정의합니다. 핵심은 LLM 판단을 그대로 실행하지 않고, Go 기반 검증 로직과 결합하여 운영 준비도 판단에 사용하는 것입니다.

## 2. 한눈에 보는 구조

| 항목 | 내용 |
| --- | --- |
| 입력 | LLM candidate, metric, policy weight |
| 처리 | policy별 weighted score 계산과 ranking |
| 출력 | selected model, score, ranking, rationale |
| 연계 | agent registry, CPU/GPU placement, AI 응용 배포·제어 계획 |
| 검증 | Go CLI/API 기반 재현 검증 |

## 3. 운영 흐름

```text
Ops policy/config
-> LLM candidate ranking
-> selected runtime candidate
-> agent registry validation
-> CPU/GPU VM placement
-> AI 응용 배포·제어 계획
-> service readiness report
```

LLM은 운영 판단 후보를 제공하고, Go service-control layer는 선정 결과와 후속 action이 허용 범위 안에 있는지 검증합니다.

## 4. 정책 설정

| 항목 | 설명 |
| --- | --- |
| 설정 파일 | `config/ops_llm_benchmark.json` |
| 실제 모델 연결 | `config/ops_llm_eval_candidates.json` |
| 평가 scenario | `data/ops_llm_eval_scenarios.jsonl` |
| policy 예시 | `quality_first`, `cost_first` |
| candidate 예시 | `primary-ops-llm`, `low-cost-ops-llm`, `code-cross-check-agent` |
| score 성격 | prototype policy baseline |
| 주의점 | 최종 표준 benchmark 결과가 아님 |

## 5. Candidate 역할

| Candidate | 역할 | 사용 의도 |
| --- | --- | --- |
| `primary-ops-llm` | 기본 운영 판단 후보 | 품질 중심 정책에서 우선 선택 |
| `low-cost-ops-llm` | 저비용 후보 | smoke-test 또는 비용 중심 검증 |
| `code-cross-check-agent` | 교차 검토 후보 | 코드/문서 consistency 확인 |

위 candidate 값은 내부 역할 label입니다. 실제 provider model 이름은 `actual_model`, `selected_actual_model`, `selected_provider`, `benchmark_status` 필드로 분리합니다. 현재 기본 benchmark status는 `not_executed`이며, 실제 모델 API 평가가 완료된 상태로 해석하지 않습니다.

## 6. Score 구성

| Metric | 의미 |
| --- | --- |
| `accuracy` | 운영 판단 정확도 기준 |
| `metric_success` | 운영 metric 활용 가능성 |
| `action_validity` | 제안 action이 허용 범위에 들어오는 정도 |
| `consistency` | 반복 판단 안정성 |
| `ttd` | time-to-decision 기준 |
| `cost` | 비용 기준 |
| `latency` | 응답 지연 기준 |

정책별 weight를 적용해 candidate score를 계산하고, 가장 높은 score를 받은 후보를 runtime model로 선택합니다.

## 7. API/CLI 검증

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

기대 신호:

```text
selected_model = primary-ops-llm
selected_actual_model = to-be-evaluated-primary-model
benchmark_status = not_executed
```

API 검증:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/ops-llm/select \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}'
```

## 8. Ops LLM 평가 Dry-Run

실제 provider API를 호출하지 않고 scenario/candidate 연결과 evaluator 구조를 검증합니다.

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

dry-run 결과는 `benchmark_status = dry_run`으로 기록됩니다. `benchmark_status = executed`인 실제 모델 응답이 수집되기 전까지는 최종 LLM 품질 평가로 주장하지 않습니다.

## 9. 설계 경계

| 경계 | 설명 |
| --- | --- |
| Benchmark 경계 | 현재 score는 prototype baseline이며 최종 LLM benchmark가 아니다. |
| Actual model 경계 | role label과 실제 provider model 이름을 분리한다. |
| 실행 경계 | LLM이 직접 infrastructure action을 실행하지 않는다. |
| 검증 경계 | Go layer가 action validity와 readiness를 검증한다. |
| 확장 경계 | 향후 실제 Ops dataset과 반복 평가 metric을 연결할 수 있다. |
