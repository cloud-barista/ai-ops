# LLM_Op

`LLM_Op`은 자연어 또는 구조화된 App Spec을 받아 배포에 필요한 어플리케이션 요구사항을 분석하고, 플랫폼 중립적인 `ApplicationProfile`을 생성합니다. Qwen·Gemma·Llama 등 2~3개 LLM 후보의 벤치마크·랭킹과 상황별 선정 기능도 이 분석 과정의 보조 기능으로 제공합니다.

```text
자연어 요청 ─→ 입력 검증 ─→ 정책별 LLM 선정 ─→ LLM 분석/JSON 검증 ─→ ApplicationProfile
                                      │                 ├─ 실패 시 1회 재요청
                                      │                 └─ 계속 실패 시 bounded local rule
구조화 App Spec ───────────────────────┴─→ 결정적 검증/변환 ──────────────→ ApplicationProfile
```

이 저장소는 인프라 후보 추천, Agent 선택·실행, Manifest 생성, AppDeploy 호출, 배포 실행, 상태 polling, 운영 feedback 또는 scaling을 수행하지 않습니다. 생성된 `ApplicationProfile`은 `resource_ops`로 전달되고, Profile과 추천 결과는 이후 `deployment_agent`가 소비합니다.

```text
LLM_Op ApplicationProfile → resource_ops ResourceRecommendation
ApplicationProfile + ResourceRecommendation → deployment_agent DeploymentPlan
```

## 제공 기능

- 자연어 요청과 어플리케이션 메타데이터를 구조화된 JSON으로 변환
- 구조화 App Spec을 LLM 호출 없이 `ApplicationProfile`로 변환
- Qwen/Gemma/Llama 후보의 `quality_first`, `cost_first` 가중치 랭킹
- LLM 응답의 exact JSON shape, unknown/duplicate field, 누락 필드, 자원·replica 범위 검증
- LLM 또는 JSON 검증 실패 시 최대 2회 시도 후 제한된 규칙 기반 fallback
- 시나리오 dry-run, OpenAI-compatible 실제 호출, 결과 채점과 후보별 비교 요약

fallback 결과는 `analysis.mode=local_rule` 및 `fallback_reason`으로 표시되며 LLM 실행 결과로 보고되지 않습니다. 구조화 입력은 `analysis.mode=structured`, 정상 LLM 결과는 `analysis.mode=llm`입니다.

## HTTP API

| Method | Path | 역할 |
| --- | --- | --- |
| `POST` | `/api/v1/application-profiles` | 자연어 또는 App Spec을 분석해 `ApplicationProfile` 생성 |
| `POST` | `/api/v1/ops-llm/select` | 정책에 따라 Ops LLM 후보 랭킹과 선택 결과 반환 |
| `GET` | `/healthz` | 프로세스 상태와 역할 확인 |
| `GET` | `/openapi.yaml` | API 계약 조회 |

자연어 요청:

```json
{
  "input_type": "natural_language",
  "request": "GPU 1개, CPU 4코어, 메모리 8GiB가 필요한 온라인 LLM 추론 서비스",
  "selection_policy": "quality_first",
  "application": {
    "app_id": "chat-service",
    "app_version": "1.0.0",
    "artifact": {
      "type": "OCI_IMAGE",
      "uri": "registry.example.org/chat-service:1.0.0"
    }
  }
}
```

구조화 입력:

```json
{
  "input_type": "structured",
  "app_spec": {
    "app_id": "batch-embedder",
    "app_version": "1.0.0",
    "workload_type": "EMBEDDING",
    "request_pattern": "BATCH",
    "cpu_cores": 8,
    "memory_mib": 32768,
    "storage_gib": 50,
    "replicas_min": 1,
    "replicas_max": 2
  }
}
```

`input_type`은 생략할 수 있으며 `request`와 `app_spec` 중 정확히 하나로 추론합니다. caller가 `candidate_id`를 지정하면 정책 선정 대신 해당 후보를 고정합니다. LLM이 VM, target, provider, Agent, command, Manifest 또는 배포 action을 출력하면 모델 결과는 거부되고 재요청/fallback 절차로 넘어갑니다.

## LLM 비교와 선정

정적 랭킹 API 예시:

```bash
curl -sS -X POST http://127.0.0.1:8080/api/v1/ops-llm/select \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}'
```

CLI로 같은 결과 조회:

```bash
./build/ops-llm-benchmark select-ops-llm --policy quality_first
```

기본 `config/ops_llm_benchmark.json`의 수치는 정책 wiring 검증용 합성 baseline입니다. 응답의 `benchmark_status=not_executed`는 실제 모델 품질 측정 결과가 아니라는 뜻입니다.

실제 비교 절차:

```bash
# provider를 호출하지 않고 10개 시나리오와 prompt/output wiring 검증
./build/ops-llm-benchmark run-ops-llm-benchmark --dry-run \
  --output-dir runs/ops-llm-dry-run

# endpoint가 설정되고 enabled=true인 후보를 실제 호출
./build/ops-llm-benchmark run-ops-llm-benchmark \
  --candidates config/ops_llm_eval_candidates.local_multi_ollama.json \
  --output-dir runs/ops-llm-executed

# 모델별 평균 점수와 최상위 후보 산출
./build/ops-llm-benchmark evaluate-ops-llm-outputs \
  --outputs runs/ops-llm-executed/model_outputs.jsonl
```

실제 호출 결과만 `benchmark_status=executed`로 기록됩니다. 비교 시나리오는 `data/ops_llm_eval_scenarios.jsonl`, 후보 예시는 `config/ops_llm_eval_candidates*.json`에 있습니다.

## 실행과 설정

Go 1.25 이상이 필요합니다.

```bash
make build
./run-llmop.sh
```

기본 API 주소는 `127.0.0.1:8080`이며 실행 스크립트는 `18080`을 사용합니다. 기본 후보들은 외부 호출을 막기 위해 비활성화되어 있습니다. `config/application_profile_llm_candidates.json`에서 사용할 endpoint/model만 설정하고 `enabled=true`로 변경합니다. API key 값은 파일에 쓰지 말고 `api_key_env`가 가리키는 환경 변수에만 둡니다.

| 변수 | 기본값 |
| --- | --- |
| `AIOPS_LLM_CANDIDATES_PATH` | `config/application_profile_llm_candidates.json` |
| `AIOPS_OPS_LLM_BENCHMARK_PATH` | `config/ops_llm_benchmark.json` |
| `AIOPS_LLMOP_CANDIDATE_ID` | `qwen3.5-profile-generator` |
| `AIOPS_LLMOP_MAX_LLM_ATTEMPTS` | `2` (1~3) |
| `AIOPS_LLMOP_MAX_REQUEST_BYTES` | `131072` |
| `AIOPS_BIND_ADDRESS` | `127.0.0.1` |
| `PORT` | `8080` |

## 검증

```bash
make test
make vet
make lint
make swag
```

## 코드 경계

| 경로 | 책임 |
| --- | --- |
| `internal/applicationprofile/` | 자연어/structured 입력, LLM 재시도, local-rule fallback, strict Profile 검증 |
| `internal/llmselection/` | 정책 가중치 기반 후보 랭킹과 선정 |
| `internal/benchmark/` | 시나리오 실행, provider 결과 수집·채점·비교 |
| `internal/profileapi/` | Profile과 LLM 선정 HTTP 경계 |
| `internal/llmclient/` | OpenAI-compatible completion adapter |
| `cmd/llmop-api/` | HTTP API entrypoint |
| `cmd/ops-llm-benchmark/` | 선정·벤치마크·평가 CLI |

프로덕션 entrypoint의 의존 그래프에는 `agentcontrol`, `appdeploy`, `autonomy`, `controlrun`, `deploymentplanner`, `llmopbridge`, `plannerguard`, `webui`가 포함되지 않습니다.

## License

Apache License 2.0
