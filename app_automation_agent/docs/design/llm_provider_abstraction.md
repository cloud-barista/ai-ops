# LLM Provider Abstraction

## 목적

이 문서는 service-control layer의 Ops LLM benchmark 구조가 특정 LLM runtime에 종속되지 않음을 설명합니다. Go 기반 benchmark runner는 OpenAI-compatible chat completion endpoint를 공통 호출 인터페이스로 사용하며, 실제 provider는 candidate config로 교체합니다.

## Provider-Agnostic 원칙

service-control layer는 다음 값을 config로 받습니다.

| 필드 | 의미 |
| --- | --- |
| `provider` | provider 또는 통합 계층 이름 |
| `actual_model` | 실제 호출할 모델명 |
| `endpoint` | OpenAI-compatible `/v1/chat/completions` endpoint |
| `api_key_env` | API key를 읽을 환경 변수 이름 |
| `enabled` | benchmark 실행 대상 여부 |
| `json_mode` | provider가 지원할 때 JSON object 응답 형식을 요청할지 여부 |

Go 코드는 위 값을 사용하여 동일한 scenario set을 provider별 endpoint로 보냅니다. 따라서 Ollama, vLLM, LM Studio, OpenAI API, Azure OpenAI, 연구용 GPU 서버 endpoint, AI-MCMP 연계 endpoint를 같은 runner로 검증할 수 있습니다.

## Local Example Provider

Ollama는 local validation을 위한 example provider입니다. 로컬 PC 또는 VM에서 OpenAI-compatible endpoint를 빠르게 띄워 benchmark runner가 실제 모델 응답을 수집할 수 있는지 확인하는 용도입니다.

Ollama config 파일은 실행 결과가 아닙니다.

```text
config/ops_llm_eval_candidates.local_ollama.json
config/ops_llm_eval_candidates.local_multi_ollama.json
```

위 파일들은 candidate 설정일 뿐이며, 실제 평가 완료 여부는 `runs/.../evaluation_summary.json`의 `benchmark_status = executed`로만 판단합니다.

## Integration Provider

외부 플랫폼 또는 연구 환경에서는 연결 계층이 endpoint와 credential을 제공합니다. 이때 service-control layer는 다음 예시 config를 기반으로 값을 주입받습니다.

```text
config/ops_llm_eval_candidates.openai_compatible.example.json
```

통합 환경에서는 이 범용 예시를 복사한 뒤 `endpoint`, `actual_model`, `provider`, `api_key_env`를 대상 provider 값으로 교체하고, 실행 대상 candidate만 `enabled=true`로 설정합니다. 별도의 플랫폼별 예시는 두지 않아 같은 계약을 모든 OpenAI-compatible endpoint에 적용합니다.

## 실행 결과 판정

`benchmark_status`는 candidate config가 아니라 실행 결과에만 사용합니다.

| 위치 | 의미 |
| --- | --- |
| candidate config | 호출 대상 후보 정의 |
| `model_outputs.jsonl` | provider endpoint 응답 원본 |
| `evaluation_summary.json` | executed/dry-run/not-executed 판정과 점수 |

실제 LLM benchmark 완료를 주장하려면 다음 조건이 필요합니다.

- `--dry-run`을 사용하지 않음
- enabled candidate endpoint가 실제 응답함
- `evaluation_summary.json`에 `benchmark_status = executed` 기록
- `selected_actual_model`과 `selected_provider` 기록
- candidate별 `average_score` 산정

Endpoint가 준비되지 않은 경우 fake executed result를 만들지 않습니다. 자동화 Action 제안은 같은 client를 재사용하며 `decision_execution_status`로 benchmark 상태와 분리합니다.
