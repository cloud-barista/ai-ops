# 로컬 검증 결과 요약 - 2026-07-03

본 문서는 `geon` 브랜치의 Go 기반 AI 서비스 제어·관리 자동화 프레임워크를 로컬 환경에서 실행한 대표 검증 결과입니다. 원본 실행 로그와 상세 JSON은 `runs/evidence-local-20260703-142217/`에 생성되며, Git에는 절대경로를 제거한 대표 산출물만 보존합니다.

## 검증 범위

| 항목 | 결과 | 근거 |
| --- | --- | --- |
| aiops-guard Go test | PASS | `go test ./...` |
| service-control-api Go test | PASS | `go test ./...` |
| 산출물 통합 검증 | PASS | `team-validation`, 6개 step |
| 로컬 시스템 검증 | PASS | `validate-system --target local` |
| API 통합 동작 검증 | PASS | healthz, agents, LLM select, placement, service operations |
| Ops LLM 평가 파이프라인 | PASS | `benchmark_status=dry_run`, `output_count=30` |

## 실행 명령

```bash
cd go/aiops-guard
go test ./...

cd ../service-control-api
go test ./...

go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/evidence-local-20260703-142217/team-validation

go run ./cmd/aiops-service-control validate-system \
  --target local \
  --output-dir ../../runs/evidence-local-20260703-142217/full-validation-local

go run ./cmd/aiops-service-control run-ops-llm-benchmark \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --candidates ../../config/ops_llm_eval_candidates.json \
  --output-dir ../../runs/evidence-local-20260703-142217/ops-llm-evaluation-dry-run \
  --dry-run

go run ./cmd/aiops-service-control evaluate-ops-llm-outputs \
  --scenarios ../../data/ops_llm_eval_scenarios.jsonl \
  --outputs ../../runs/evidence-local-20260703-142217/ops-llm-evaluation-dry-run/model_outputs.jsonl \
  --summary ../../runs/evidence-local-20260703-142217/ops-llm-evaluation-dry-run/evaluation_summary.json
```

## 대표 산출 JSON

| 산출물 | 설명 |
| --- | --- |
| [`artifacts/local_20260703_team_validation_summary.json`](artifacts/local_20260703_team_validation_summary.json) | LLM 선정, Agent registry, action 검증, CPU/GPU 배치, 배포·제어 계획 통합 검증 |
| [`artifacts/local_20260703_system_validation_summary.json`](artifacts/local_20260703_system_validation_summary.json) | 로컬 환경, Go test, team-validation을 묶은 시스템 검증 요약 |
| [`artifacts/local_20260703_ops_llm_evaluation_summary.json`](artifacts/local_20260703_ops_llm_evaluation_summary.json) | Ops LLM 평가 파이프라인 dry-run evaluator 결과 |
| [`artifacts/local_20260703_api_integration_validation_manifest.json`](artifacts/local_20260703_api_integration_validation_manifest.json) | API endpoint 통합 호출 검증 manifest |

## 해석

- 본 검증은 로컬 환경에서 Go 로직, CLI, API endpoint, 산출물 흐름이 정상 동작하는지 확인한 결과입니다.
- `team-validation`은 6개 step 모두 `valid=true`입니다.
- `validate-system --target local`은 로컬 환경 증적과 Go test, team-validation을 하나의 summary로 묶어 `valid=true`를 반환했습니다.
- Ops LLM 평가는 `benchmark_status=dry_run`입니다. 이는 실제 LLM 품질 평가가 아니라, 시나리오·후보·출력·evaluator 연결 구조 검증입니다.
- 실제 LLM endpoint를 연결한 경우에만 `benchmark_status=executed`와 `selected_actual_model`을 최종 품질 평가 결과로 기록합니다.
- VM 실험은 같은 `validate-system` 명령을 VM 내부에서 `--target vm`으로 실행하여 GPU/metadata evidence를 추가하는 방식입니다.
