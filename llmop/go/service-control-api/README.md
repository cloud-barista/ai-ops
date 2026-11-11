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
POST /api/v1/ops-llm/interpret
GET  /api/v1/ops-llm/runs/{run_id}
GET  /healthz
GET  /openapi.yaml
GET  /swagger
```

[통합 Swagger UI](http://127.0.0.1:18100/swagger)에서 세 핵심 서비스의 요청·응답 모델을 함께 조회하고 API를 호출합니다. Go 주석을 수정한 뒤 `llmop` 루트에서 `make swag`로 원본 명세를 갱신하고, 저장소 루트에서 `python platform/scripts/generate-openapi.py`로 통합 명세를 재생성합니다.

`POST /api/v1/application-profiles`는 다음 두 입력을 지원합니다.

- `natural_language`: 정책으로 LLM 후보를 선정하고 strict JSON/의미 검증을 수행합니다. 오류 시 최대 2회 호출하며 계속 실패하면 `local_rule` fallback을 명시합니다.
- `structured`: App Spec을 LLM에 보내지 않고 결정적으로 검증·변환합니다.

unknown JSON field와 credential-like 입력은 분석 전에 거부합니다. 모델 응답은 duplicate/unknown/missing field, 자원 범위, replica 범위, accelerator 일관성, caller app identity 보존을 검사합니다. `profile_id`와 trusted artifact 결합은 Go가 수행합니다.

`POST /api/v1/ops-llm/select`는 `quality_first` 또는 `cost_first` 정책으로 전체 후보 ranking을 반환합니다. `benchmark_status=not_executed`는 설정값 기반 baseline이며 실제 비교 결과가 아닙니다.

`POST /api/v1/ops-llm/interpret`는 `AIOPS_OPS_LLM_TOKEN`에 해당하는 bearer 토큰과 `{"intent":"구축형 LLM으로 응용 목록 보여줘"}`를 받습니다. `llm_type`을 직접 지정할 수도 있습니다. 후보 설정의 `llm_type`에는 `subscription`(구독형), `open_weights`(무료 공개 모델), `self_hosted`(구축형)를 사용합니다. 유형을 지정하면 설정 순서에서 첫 활성 후보를 호출합니다. 유형을 생략하면 `AIOPS_LLMOP_CANDIDATE_ID`의 활성 후보를 사용합니다. 각 후보에 OpenAI 호환 호출 주소·모델과 필요한 환경변수 참조를 설정합니다.

모델에는 배포 UUID 대신 `[deployment]` 표시자를 보내 작업 하나를 분류합니다. Go에서 원래 지시에 포함된 배포 ID를 연결합니다. VM 제어의 모델 레이블은 기존 `control_application`과 `control_action` 응답으로 변환합니다.

응답과 구조화 로그의 `run_id`, `llm_type`, `actual_model`, `latency_ms`, `status`로 호출 결과를 확인합니다. 검증된 작업 제안만 `proposal`로 반환하며, 제공자 오류나 출력 검증 실패는 HTTP `502`로 기록합니다.

### OpsLLM 실행 기록 조회

`X-AIMCMP-OpsLLM-Run-ID`에 `opsllm-`과 소문자 Base32 문자 26개를 붙인 값을 보낼 수 있습니다. 헤더를 생략하면 서버가 ID를 만듭니다. LLMop은 공급자를 호출하기 전에 ID를 예약하고, 결과를 `runs/ops-llm`에 기록합니다. 이 디렉터리는 `AIOPS_REPO_ROOT` 아래에 있으며 실행 계정에 쓰기 권한이 필요합니다. 재시작 후에도 같은 디렉터리를 사용합니다. 기록에는 요청 해시, 모델과 지연, 검증된 결과를 담습니다.

같은 `AIOPS_OPS_LLM_TOKEN`으로 `GET /api/v1/ops-llm/runs/{run_id}`를 호출합니다. 실행 결과가 저장되기 전이거나 그 전에 프로세스가 중단되면 상태는 `completion_unconfirmed`입니다. 같은 ID와 같은 정규화 요청으로 `POST`를 다시 보내면 공급자 호출 없이 저장된 결과를 반환합니다. 결과를 아직 확인할 수 없으면 `202`, 같은 ID에 다른 요청을 보내면 `409`입니다.

### 실행 비교

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
