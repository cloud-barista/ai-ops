# LLM_Op Go module

이 module은 자연어 또는 structured App Spec을 `ApplicationProfile`로 변환하고, Profile 생성용 LLM 후보를 비교·선정합니다.

## Entry points

```bash
go run ./cmd/llmop-api
go run ./cmd/ops-llm-benchmark select-ops-llm --policy quality_first
```

HTTP route:

```text
POST /api/v1/application-profiles
POST /api/v1/ops-llm/select
GET  /healthz
GET  /openapi.yaml
```

`POST /api/v1/application-profiles`는 다음 두 입력을 지원합니다.

- `natural_language`: 정책으로 LLM 후보를 선정하고 strict JSON/의미 검증을 수행합니다. 오류 시 최대 2회 호출하며 계속 실패하면 `local_rule` fallback을 명시합니다.
- `structured`: App Spec을 LLM에 보내지 않고 결정적으로 검증·변환합니다.

unknown JSON field와 credential-like 입력은 분석 전에 거부합니다. 모델 응답은 duplicate/unknown/missing field, 자원 범위, replica 범위, accelerator 일관성, caller app identity 보존을 검사합니다. `profile_id`와 trusted artifact 결합은 Go가 수행합니다.

`POST /api/v1/ops-llm/select`는 `quality_first` 또는 `cost_first` 정책으로 전체 후보 ranking을 반환합니다. `benchmark_status=not_executed`는 설정값 기반 baseline이며 실제 비교 결과가 아닙니다.

실행 비교:

```bash
go run ./cmd/ops-llm-benchmark run-ops-llm-benchmark --dry-run
go run ./cmd/ops-llm-benchmark evaluate-ops-llm-outputs \
  --outputs /path/to/model_outputs.jsonl
```

배포, 인프라 추천 또는 Agent 실행 route는 등록하지 않습니다.

## Verification

```bash
go test ./cmd/llmop-api ./cmd/ops-llm-benchmark ./internal/applicationprofile ./internal/benchmark ./internal/profileapi ./internal/llmclient ./internal/llmselection
go vet  ./cmd/llmop-api ./cmd/ops-llm-benchmark ./internal/applicationprofile ./internal/benchmark ./internal/profileapi ./internal/llmclient ./internal/llmselection
```
