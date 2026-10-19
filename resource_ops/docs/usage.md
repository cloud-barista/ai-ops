# AI-MCMP Resource Ops

`resource_ops`는 LLM_Op의 `ApplicationProfile`을 받아 인프라 조회 조건을 결정적으로 투영하고, 서로 다른 소스의 데이터를 공통 Snapshot으로 정규화한 뒤 필수 조건 필터링·점수화·추천을 수행한다.

```text
LLM_Op ApplicationProfile
  → internal ResourceQuery projection
  → ResourceSource
  → source-specific adapter
  → RawFact boundary
  → normalized ResourceSnapshot
  → mandatory filtering
  → candidate scoring and ranking
  → ResourceRecommendation
```

이 서버는 ApplicationProfile을 생성하지 않고, 배포 Manifest를 만들지 않으며, `DEPLOY`·`KEEP`·`SCALE_OUT`·`SCALE_IN`을 결정하지 않는다. 런타임 feedback과 재계획은 downstream Agent Control·Optimization Planner 책임이다.

## 임시 리소스 소스

현재 테스트 소스는 Prometheus다. 기본 URL은 `http://127.0.0.1:9090`이며 다음 환경변수로 교체할 수 있다.

```bash
export RESOURCE_PROMETHEUS_URL=http://127.0.0.1:9090
export RESOURCE_PROMETHEUS_TIMEOUT=10s
export RESOURCE_PROMETHEUS_MAX_SAMPLE_AGE=2m
# 선택: 인증 프록시를 붙였을 때만 사용
export RESOURCE_PROMETHEUS_BEARER_TOKEN=...
```

Prometheus URL, PromQL, metric 이름, label 결합 규칙은 `internal/resource/prometheus`에만 존재한다. 이후 리소스 전달 형식이 바뀌면 `resource.Source` 구현만 교체하며 ApplicationProfile 입력, 내부 Query, Snapshot, 필터, 점수, 응답 계약은 유지한다.

Prometheus의 `/query`는 웹 UI다. 어댑터는 `/api/v1/query`를 사용하며 모든 instant query에 동일한 평가 시각을 전달한다. `node_exporter`와 DCGM exporter 결과는 포트가 다른 `instance`가 아니라 `node_id`로 결합한다.
표본 시각이 평가 시각보다 미래이거나 설정한 최대 나이(기본 2분)를 넘으면 해당 표본을 제외하고 warning을 남긴다. 필요한 값이 빠진 노드는 가용성 필터에서 제외되며, 필수 metric query에 유효한 표본이 하나도 없으면 Source unavailable 오류를 반환한다.

## 실행

Go 1.26.5가 필요하다.

```bash
go run ./cmd/server
```

기본 주소는 `0.0.0.0:8000`이다. `HOST`, `PORT`로 변경할 수 있다.

```bash
curl -fsS \
  -X POST http://127.0.0.1:8000/api/v1/resource-recommendations \
  -H 'Content-Type: application/json' \
  --data-binary @fixtures/application-profile-request-gpu.json
```

서버 실행과 요청을 한 번에 재현하려면 다음을 사용한다.

```bash
./scripts/run-demo.sh
```

## HTTP API

- `GET /health`: 프로세스 liveness와 활성 ResourceSource
- `POST /api/v1/resource-recommendations`: `ApplicationProfile` 기반 canonical recommendation API
- `POST /legacy/api/v1/resource-recommendations`: 기존 `ResourceQuery` 입력 호환 경로

정식 입력 스키마는 `schemas/application_profile_recommendation_request.schema.json`, 출력 스키마는 `schemas/resource_recommendation.schema.json`이다. 입력은 unknown field와 두 번째 JSON 값을 거부한다. `schemas/resource_query.schema.json`은 응답 evidence와 legacy 경로 검증을 위해 유지한다.

### ApplicationProfile recommendation request

```json
{
  "schema_version": "1.0",
  "request_id": "flow-001",
  "application_profile": {
    "profile_id": "profile-001",
    "app_id": "chat-service",
    "app_version": "1.0.0",
    "workload": {"task_type": "LLM_INFERENCE", "request_pattern": "ONLINE"},
    "requirements": {
      "compute": {"cpu_cores_min": 8, "memory_mib_min": 32768, "storage_gib_min": 20},
      "accelerator": {"required": true, "type": "GPU", "count_min": 1, "memory_mib_min_per_device": 12000},
      "deployment": {"replicas_min": 1, "replicas_max": 2, "isolation": "ONE_MAJOR_APP_PER_VM"},
      "slo": {},
      "cost": {}
    },
    "analysis": {"confidence": 0.9}
  },
  "preferences": {"max_results": 3}
}
```

실제 `application_profile`은 LLM_Op 응답의 값을 변경 없이 전달한다. `request_id`는 downstream deployment_agent의 `correlation_id`와 같아야 하며, 없으면 `profile_id`를 사용한다.

LLM_Op ApplicationProfile에서의 기본 투영은 다음과 같다.

| ApplicationProfile | ResourceQuery |
|---|---|
| `profile_id` | `profile_id` |
| `requirements.compute.cpu_cores_min` | `constraints.cpu_cores_min` |
| `requirements.compute.memory_mib_min` | `constraints.memory_mib_min` |
| `requirements.compute.storage_gib_min` | `constraints.storage_gib_min` |
| `requirements.accelerator.*` | `constraints.accelerator.*` |

이 투영은 규칙 기반이며 LLM을 다시 호출하지 않는다. 내부 추천 파이프라인은 투영된 조회 조건만 소비한다.

### ResourceRecommendation

응답에는 다음이 포함된다.

- 정규화된 추천 ResourceProfile
- 후보별 0~100 점수와 CPU·메모리·스토리지·가속기 breakdown
- 추천 사유
- 제외된 리소스와 조건별 제외 사유
- 발견·통과·제외·반환 개수
- source와 사용한 표본 중 가장 오래된 관측 시각

후보가 없어도 HTTP 200과 빈 `recommendations`, `NO_FEASIBLE_RESOURCE` warning을 반환한다. 이는 배포 실패나 scaling 결정이 아니라 조회 결과다.

## 현재 필터 의미

- CPU: 총 logical core와 최근 5분 사용률에서 추정한 가용 core
- 메모리: 총량과 `MemAvailable`
- 스토리지: root filesystem 총량과 가용량
- GPU: DCGM exporter health, 모델, 장치 수
- VRAM: 장치별 총량과 현재 free VRAM

Prometheus 사용률은 예약 가능한 용량을 보장하지 않는다. 추천은 point-in-time advisory이며 실제 할당 시 downstream 배포 계층이 다시 검증해야 한다.

## 검증

```bash
gofmt -l ./cmd ./internal
go vet ./...
go test -count=1 ./...
```

Prometheus가 현재 외부에 평문 HTTP와 무인증으로 노출되어 있으므로 테스트 이후에는 네트워크 접근 제한, TLS 또는 인증 프록시를 적용해야 한다.
