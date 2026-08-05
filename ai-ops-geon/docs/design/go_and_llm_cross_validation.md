# Go와 LLM 교차 검증

## 목표

시스템은 LLM reasoning과 deterministic service-control check를 분리합니다. LLM selection과 high-level readiness decision은 config와 API response에 표현하고, Go는 bounded action, placement constraint, deployment-plan structure, guard readiness를 검증합니다.

## 제어 경계

```text
LLM/model policy
-> selected runtime candidate
-> registered agent roles
-> bounded action validation
-> CPU/GPU placement constraints
-> deployment manifest dry-run
-> guard-readiness result
```

## Go 책임

| Layer | Go 책임 |
| --- | --- |
| LLM selection | 설정된 prototype policy 아래 candidate label ranking |
| Agent registry | agent list 조회 및 bounded action 검증 |
| 배치 | SLO, 비용, capacity 기준 CPU/GPU VM candidate scoring |
| Deployment plan | Kubernetes 배포·제어 계획 생성 |
| 준비도 | LLM, placement, manifest, agent review, guard-readiness 결과 결합 |
| Guard boundary | `go/aiops-guard`에 standalone bounded-action validator 유지 |

## Guard 관계

`service-control-api`는 LLM selection, agent registry validation, placement, deployment-plan generation, manifest dry-run, readiness reporting을 수행합니다.

`aiops-guard`는 standalone bounded-action validator입니다. service-control readiness response는 Go guard backend와 recovery context가 준비되었음을 보여주기 위해 `guard_validation`을 포함합니다. `service-control-api`에서 standalone guard CLI를 full runtime으로 호출하는 wiring은 다음 단계이며 완료된 production integration이 아닙니다.

## 안전 원칙

Runtime model은 operation을 추천하거나 정당화할 수 있지만, action이 실행 가능한 상태가 되기 전의 deterministic validation은 Go layer가 담당합니다.
