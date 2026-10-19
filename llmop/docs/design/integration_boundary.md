# LLM_Op과 Deployment Agent 책임 경계

## 원칙

`LLM_Op`은 자연어나 structured App Spec을 실행 명령으로 바꾸는 Agent가 아니라, 정규화된 `ApplicationProfile`로 바꾸는 분석 서비스입니다. Ops LLM 후보 비교·선정은 이 분석에 사용할 모델을 고르는 내부 지원 기능입니다. `ApplicationProfile`은 다음 단계의 입력 계약이며 배포 승인이나 실행 결과가 아닙니다.

```text
사용자 자연어
  → LLM_Op 입력 검증과 입력 모드 판별
  → 정책별 LLM 선정 또는 structured 결정적 변환
  → LLM 요구 분석, 재시도, 명시적 local-rule fallback
  → ApplicationProfile 검증/생성
  → resource_ops 조회 조건 투영·후보 추천
  → ApplicationProfile + ResourceRecommendation를 deployment_agent에 전달
```

## 책임 표

| 기능 | LLM_Op | resource_ops | deployment_agent / 외부 시스템 |
| --- | --- | --- | --- |
| 자연어 요청 형식·민감정보 검증 | 수행 | 수행하지 않음 | 필요 시 추가 검증 |
| structured App Spec 검증·결정적 변환 | 수행 | 수행하지 않음 | 입력으로 소비 |
| Profile 생성용 LLM 후보 비교·선정 | 수행 | 수행하지 않음 | 수행하지 않음 |
| workload와 최소 CPU/memory/storage/GPU 요구 분석 | 수행 | 조회 조건으로 투영 | 입력으로 소비 |
| replica 범위, SLO, cost 요구 분석 | 수행 | 필요한 후보 수에 반영 | feasibility와 정책 판단에 사용 |
| `ApplicationProfile` 생성 및 shape 검증 | 수행 | 변경 없이 입력 | 변경 없이 입력하고 재검증 |
| resource 수집·정규화·후보 추천 | 수행하지 않음 | 수행 | 수행하지 않고 결과 재검증 |
| Agent 선택·권한 검증·실행 | 수행하지 않음 | 수행하지 않음 | 수행 |
| 배포 승인/거부/재시도 판단 | 수행하지 않음 | 수행하지 않음 | 수행 |
| Manifest 또는 실행 명령 생성 | 수행하지 않음 | 수행하지 않음 | 수행 |
| AppDeploy/VM/cloud/Kubernetes 호출 | 수행하지 않음 | 수행하지 않음 | 외부 배포 계층 책임 |
| 배포 상태, metric, feedback, scaling | 수행하지 않음 | 수집 source 제공 가능 | 결과 요약·최적화 판단 |

## HTTP 계약

`LLM_Op`은 다음 route만 공개합니다.

- `POST /api/v1/application-profiles`
- `POST /api/v1/ops-llm/select`
- `GET /healthz`
- `GET /openapi.yaml`

과거 service-control route는 호환 route로 남기지 않습니다. 잘못된 호출이 조용히 다른 구현으로 이어지지 않도록 `404 Not Found`가 경계의 일부입니다.

## LLM 신뢰 경계

- 자연어는 untrusted data로 prompt에 넣습니다.
- caller가 구조화해 제공한 `app_id`, `app_version`, artifact는 모델 권한 밖에서 보존합니다. 이 보존 자체는 caller나 artifact provenance 인증이 아닙니다.
- LLM은 `profile_id`를 만들 수 없고 Go가 결정적으로 생성합니다.
- unknown field, VM/Target/Agent/실행 필드, 범위를 벗어난 자원값은 profile 생성 실패로 처리합니다.
- 모델 장애 또는 잘못된 JSON은 최대 설정 횟수만큼 재요청합니다. 계속 실패하면 명시적인 `analysis.mode=local_rule`과 `fallback_reason`을 붙인 bounded parser를 사용하며 이를 LLM 결과로 표시하지 않습니다.
- structured App Spec은 LLM으로 보내지 않고 `analysis.mode=structured`로 결정적으로 변환합니다.

## 벤치마크 경계

- `config/ops_llm_benchmark.json`의 `not_executed` baseline은 후보 정책 연결을 검증하기 위한 합성값입니다.
- 실제 provider 호출 결과만 `executed`로 기록합니다.
- 시나리오는 요구 분석·Profile shape·금지 필드 배제만 평가하며 배포 실행이나 상태 polling을 평가하지 않습니다.
- 랭킹 결과는 모델 선택 근거이지 인프라 feasibility, 배포 승인 또는 실행 권한이 아닙니다.

## downstream 규칙

`ApplicationProfile`을 받은 시스템은 이를 실제 capacity, 배포 가능성, 권한 또는 비용 보장으로 해석하면 안 됩니다. `resource_ops`가 실제 snapshot과 결합해 후보를 추천하고, `deployment_agent`가 Profile과 추천 결과의 상관관계·적합성을 다시 검증한 후에만 계획을 생성해야 합니다.
