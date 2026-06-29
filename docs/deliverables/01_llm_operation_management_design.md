# LLM 운영 관리 구조 설계서

영문 제목: LLM Operation Management Structure Design

## 1. 한눈에 보는 구조

| 항목 | 내용 |
| --- | --- |
| 목적 | Ops 상황에서 사용할 LLM 후보를 정책 기준으로 선정하고, 서비스 제어 흐름에 연결한다. |
| 입력 | `config/ops_llm_benchmark.json`의 candidate, metric, policy weight |
| 처리 | policy별 weighted score 계산 및 candidate ranking |
| 출력 | selected model, score, ranking, 선정 사유 |
| 검증 | Go CLI/API로 LLM 선정 결과를 재현 |

## 2. 운영 흐름

```text
Ops policy/config
-> LLM candidate ranking
-> selected runtime candidate
-> agent registry validation
-> CPU/GPU placement
-> deployment plan
-> service readiness report
```

LLM은 판단 후보를 제안하는 역할이고, 최종 검증은 Go service-control layer가 담당합니다.

## 3. 선정 정책

| 요소 | 설명 |
| --- | --- |
| candidate | 운영 판단에 사용할 LLM 역할 label |
| metric | accuracy, consistency, latency, cost 등 비교 기준 |
| policy | `quality_first`, `cost_first` 등 가중치 조합 |
| ranking | policy에 따른 후보별 score 정렬 |
| selected_model | 가장 높은 score를 받은 runtime candidate |

## 4. Candidate 역할

| Candidate | 역할 |
| --- | --- |
| `primary-ops-llm` | 운영 판단 품질을 우선하는 기본 후보 |
| `low-cost-ops-llm` | 비용을 줄인 smoke-test 및 fallback 후보 |
| `code-cross-check-agent` | 코드와 문서 교차 검토 후보 |

## 5. 검증 방법

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

기대 신호:

```text
selected_model = primary-ops-llm
```

## 6. 설계 경계

- 현재 score는 prototype policy baseline이다.
- 최종 LLM benchmark 결과가 아니다.
- 정량 평가에는 고정 prompt, dataset, metric, scoring rule이 별도로 필요하다.
- LLM 판단은 Go 기반 action validation을 통과해야 서비스 제어 결과로 사용할 수 있다.
