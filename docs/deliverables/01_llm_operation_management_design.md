# LLM 운영 관리 구조 설계서

영문 제목: LLM Operation Management Structure Design

## 1. 목적

이 문서는 1차년도 Go 기반 프로토타입에 구현된 LLM 운영 관리 구조를 설명합니다. 목적은 운영 관점의 LLM 후보를 어떤 기준으로 선정하는지, 선정된 후보가 서비스 제어 흐름에서 어떻게 사용되는지, 그리고 주변 운영 pipeline을 Go 기반 결정론적 로직으로 어떻게 검증하는지 정의하는 것입니다.

본 문서는 공식 설계 산출물 원본입니다. Go 구현, JSON 정책 설정, 제출 검증 기록과 함께 검토해야 합니다.

## 2. 1차년도 구현 범위

구현 범위는 다음과 같습니다.

- Ops 분석 및 최적 LLM 선정 정책 구조
- AI LLM 운영 관리 구조 설계
- 정책 기반 candidate ranking
- Go CLI/API 기반 기능 검증
- 에이전트 registry, 배치 추천, 서비스 운영 준비도 보고와의 통합

이 문서는 완성형 production LLMOps platform을 설명하지 않습니다. 1차년도 연구 범위 안에서 구현 및 검증 가능한 정책 기반 LLM 선정 구조에 초점을 둡니다. 현재 구현은 대규모 log 분석 시스템이나 실시간 LLM serving platform을 대체하지 않으며, Go API/CLI와 JSON 설정을 통해 selection policy, candidate ranking, service-control readiness reporting을 검증합니다.

## 3. 전체 구조

```text
Ops policy/config
-> Ops LLM candidate ranking
-> selected runtime candidate
-> agent registry validation
-> CPU/GPU VM placement recommendation
-> Kubernetes deployment-plan generation
-> service-operations readiness report
```

Go service-control 계층은 LLM 선정 판단과 결정론적 검증을 분리합니다. 정책 설정은 candidate metric과 policy weight를 제공합니다. Go 로직은 후보를 ranking하고 runtime label을 선택한 뒤, 선택 결과를 더 넓은 service-control readiness 흐름에 전달합니다.

## 4. 정책 설정

LLM 정책 설정 파일은 다음 위치에 있습니다.

```text
config/ops_llm_benchmark.json
```

해석 규칙은 다음과 같습니다.

- 현재 값은 수동 정의된 프로토타입 정책 기준값입니다.
- 최종 표준 벤치마크 결과가 아닙니다.
- 최종 정량 보고에는 통제된 per-model Ops 평가로 재생성한 값이 필요합니다.
- 고정 프롬프트, 데이터셋, metric collection, 반복 가능한 scoring rule이 필요합니다.
- candidate name은 prototype policy label이며 검증 완료된 provider benchmark claim이 아닙니다.

설정 파일에는 `quality_first`, `cost_first` 같은 policy weight, candidate role label, Go ranking function에서 사용하는 normalized score input이 포함됩니다.

## 5. 후보 역할

| Candidate | 역할 |
| --- | --- |
| `primary-ops-llm` | 서비스 제어 판단을 위한 기본 Ops reasoning 후보 |
| `low-cost-ops-llm` | 저비용 smoke-test 및 fallback 후보 |
| `code-cross-check-agent` | 코드와 문서 교차 검증 후보 |

이 이름들은 프로토타입 정책 연결을 위한 role label입니다. 특정 상용 또는 오픈소스 LLM의 최종 벤치마크가 완료되었음을 의미하지 않습니다.

## 6. 점수 산정 방법

| Metric | 의미 |
| --- | --- |
| `accuracy` | 운영 판단의 프로토타입 정확도 신호 |
| `metric_success` | 사용 가능한 운영 metric 활용 능력 |
| `action_validity` | 제안 action이 bounded-control rule 안에 머무는지 여부 |
| `consistency` | 판단 반복 가능성 |
| `ttd` | time-to-decision의 역방향 score |
| `cost` | 추정 운영 비용의 역방향 score |
| `latency` | latency의 역방향 score |

각 policy는 normalized metric value와 설정된 weight를 조합합니다. 예를 들어 `quality_first` policy는 운영 정확도, metric 활용, action validity, consistency를 우선하고, `cost_first` policy는 cost와 latency에 더 높은 가중치를 둡니다. 요청된 policy에서 weighted score가 가장 높은 candidate가 선택됩니다.

## 7. Go 구현

| 구성요소 | 경로 |
| --- | --- |
| 서비스 로직 | `go/service-control-api/internal/api/service.go` |
| 요청/응답 model | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| 정책 설정 | `config/ops_llm_benchmark.json` |

구현은 JSON policy file을 읽고, 요청 policy를 검증하고, score input을 normalize한 뒤, weighted score를 계산하여 선택된 candidate와 ranking 결과를 반환합니다.

## 8. CLI 검증

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

기대 신호는 다음과 같습니다.

```text
selected_model = primary-ops-llm
```

이 출력은 policy selection flow가 올바르게 연결되었음을 확인합니다. 최종 표준 LLM 성능 결과를 의미하지는 않습니다.

## 9. API 검증

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/ops-llm/select \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}'
```

API는 선택된 candidate, score, ranked candidate list, rationale, validity flag를 반환합니다.

## 10. 설계 경계

이 설계서는 표준 LLM 평가 완료를 주장하지 않습니다. 향후 표준화된 평가 데이터와 연결 가능한 prototype policy-selection structure를 정의합니다. 현재 구현은 LLM 후보 선정이 AIOps service-control framework에 통합되는 방식을 보여주는 데 유용하지만, 운영 배포를 위해서는 실제 운영 trace, 반복 가능한 시험 scenario, provider별 model evaluation, monitoring, governance가 추가로 필요합니다.

## 11. 관련 산출물

| 산출물 | 경로 |
| --- | --- |
| LLM 정책 설정 | `config/ops_llm_benchmark.json` |
| Go 서비스 로직 | `go/service-control-api/internal/api/service.go` |
| API/CLI model | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| 보조 선정 가이드 | `docs/design/ops_llm_selection_guide.md` |
| 교차 검증 설계 노트 | `docs/design/go_and_llm_cross_validation.md` |
| 기능/API 가이드 | `docs/submission/functional_api_guide.md` |
| 테스트 가이드 | `docs/submission/test_guide.md` |
