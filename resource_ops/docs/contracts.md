# Resource Ops JSON Contracts

canonical 계약은 다음 두 개다.

- `schemas/application_profile_recommendation_request.schema.json`
- `schemas/resource_recommendation.schema.json`

모든 계약의 `schema_version`은 `1.0`이다.

## ApplicationProfileRecommendationRequest

정식 입력은 LLM_Op이 생성한 `ApplicationProfile`과 흐름 식별용 `request_id`다. Resource Ops는 profile의 compute, accelerator, replica 요구만 내부 `ResourceQuery`로 투영한다. 자연어를 분석하거나 profile을 수정하지 않는다.

## ResourceQuery (internal/legacy)

ResourceQuery는 Resource Ops가 ApplicationProfile에서 투영한 내부 조회 조건이다. 원문 자연어, artifact, model output, deployment 상태, manifest는 포함하지 않는다. 기존 HTTP 소비자는 `/legacy/api/v1/resource-recommendations`에서만 이 형식을 사용할 수 있다.

- `request_id` 또는 `profile_id` 중 하나가 필요하다.
- `request_id`가 없으면 `profile_id`를 사용한다.
- CPU와 메모리는 필수 양수다.
- 스토리지는 0 이상이다.
- GPU가 필요하면 유형, 최소 장치 수, 장치당 최소 VRAM을 표현한다.
- selector는 source가 제공하는 provider/region/architecture/type/label을 제한한다.
- `max_results` 기본값은 3, 상한은 100이다.

## ResourceSnapshot

ResourceSnapshot은 외부 계약이 아니라 내부 정규화 산출물이다. 원본 소스가 바뀌어도 이 모델 이후 단계는 변경하지 않는다.

총 용량과 가용량은 서로 다른 의미다. 하드웨어 적합성은 총 용량으로, 현재 추천 가능성은 가용량으로 모두 검사한다. 가용 metric이 누락되면 0으로 가장하지 않고 warning과 필터 실패로 처리한다.

`observed_at`은 조회를 실행한 시각이 아니다. Snapshot은 사용한 유효 표본 전체 중 가장 오래된 시각을, 각 ResourceProfile은 해당 노드에 사용한 유효 표본 중 가장 오래된 시각을 기록한다. 시각이 없는 RawFact는 무시한다. Prometheus adapter는 `RESOURCE_PROMETHEUS_MAX_SAMPLE_AGE`(기본 `2m`)보다 오래되거나 평가 시각보다 미래인 표본을 제외하고 metric별 경고를 남긴다. 새 표본과 낡은 표본이 한 query에 섞여 있어도 노드별 필요한 값이 빠지면 해당 노드는 필터에서 제외된다.

## ResourceRecommendation

추천 목록은 score 내림차순, 동점 `resource_id` 오름차순이다. 각 후보는 정규화 ResourceProfile, score breakdown, 추천 사유를 포함한다. 제외 목록은 조건 code, field, required, actual, 사람이 읽을 수 있는 reason을 포함한다.

ResourceRecommendation에는 deployment action, replica count, manifest, scheduler handoff가 없다.

## 오류

| 상황 | HTTP | code |
|---|---:|---|
| malformed/unknown/복수 JSON | 400 | `MALFORMED_JSON` |
| ApplicationProfile identity/replica 오류 | 422 | `INVALID_APPLICATION_PROFILE` |
| 조회 조건 의미 오류 | 422 | `INVALID_RESOURCE_QUERY` |
| Prometheus 또는 향후 source 장애 | 502 | `RESOURCE_SOURCE_UNAVAILABLE` |
| 내부 오류 | 500 | `INTERNAL_ERROR` |
| 조건을 만족하는 후보 없음 | 200 | 빈 recommendations + warning |

Source 장애의 내부 URL·응답 본문은 외부 오류 응답에 그대로 노출하지 않는다.
