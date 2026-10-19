# LLM_Op 문서 지도

현재 실행 계약은 다음 문서를 기준으로 합니다.

| 문서 | 역할 |
| --- | --- |
| [`../README.md`](../README.md) | 프로젝트 역할, 실행, 설정, 검증 |
| [`../go/service-control-api/README.md`](../go/service-control-api/README.md) | Go module과 HTTP 경계 |
| [`design/integration_boundary.md`](design/integration_boundary.md) | `llmop`과 `deployment_agent` 책임 분리 |
| [`../go/service-control-api/docs/swagger/swagger.yaml`](../go/service-control-api/docs/swagger/swagger.yaml) | 생성된 공개 API 계약 |
| [`../examples/application-profile/`](../examples/application-profile/) | 요청/응답 예시 |
| [`../config/ops_llm_benchmark.json`](../config/ops_llm_benchmark.json) | Ops LLM 정책별 ranking baseline |
| [`../data/ops_llm_eval_scenarios.jsonl`](../data/ops_llm_eval_scenarios.jsonl) | ApplicationProfile 요구 분석 비교 시나리오 |

이 디렉터리의 나머지 설계서, 증적, 제출 자료와 과거 계획서는 기존 통합 service-control prototype의 연구 이력을 보존한 자료입니다. 그 문서에 등장하는 Agent Registry, AppDeploy, Manifest, 운영 최적화, 배포 실행 route는 현재 `llmop-api`의 실행 기능이 아닙니다. 현재 동작은 위 활성 문서와 Go entrypoint를 우선합니다.

API 계약 변경 시 godoc 주석을 수정한 뒤 `make swag`로 `swagger.json`과 `swagger.yaml`을 함께 갱신합니다.
