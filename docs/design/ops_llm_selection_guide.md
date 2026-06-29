# Ops LLM 선정 가이드

## 목적

Ops LLM selection은 `quality_first` 또는 `cost_first` 같은 policy 아래에서 service-control reasoning에 사용할 prototype runtime model label을 선택합니다.

Go 구현 위치:

```text
go/service-control-api/internal/api/service.go
```

정책 데이터 위치:

```text
config/ops_llm_benchmark.json
```

## 프로토타입 데이터 경계

현재 candidate score는 수동 정의된 prototype policy value입니다. ranking logic, API wiring, report generation을 검증하기 위한 값이며 standardized benchmark result가 아닙니다.

최종 정량 보고를 위해서는 fixed prompt, dataset, metric collection, repeatable scoring rule을 갖춘 controlled per-model Ops evaluation run으로 값을 재생성해야 합니다.

## Candidate 역할

| Candidate | 역할 |
| --- | --- |
| `primary-ops-llm` | 기본 Ops reasoning candidate |
| `low-cost-ops-llm` | 저비용 smoke-test 및 fallback candidate |
| `code-cross-check-agent` | 코드와 문서 교차 검증 candidate |

## Scoring

각 policy는 normalized metric을 조합합니다.

| Metric | 의미 |
| --- | --- |
| `accuracy` | Ops task correctness baseline |
| `metric_success` | 사용 가능한 operation metric 활용 능력 |
| `action_validity` | bounded and safe action proposal 비율 |
| `consistency` | 결정 반복 가능성 |
| `ttd` | inverse time-to-decision score |
| `cost` | inverse estimated cost score |
| `latency` | inverse latency score |

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

기대 selection:

```text
selected_model = primary-ops-llm
```
