# Team Validation Guide

이 문서는 제출/시연 환경에서 공통으로 확인할 Go 기반 검증 절차를 정리한다.

## 개발 언어 기준

공통 구현 언어는 Go이다. 다음 의사결정 로직은 Go API/CLI 기준으로 실행하고
검증한다.

- Ops LLM 선정
- Agent registry 조회 및 bounded action 검증
- CPU/GPU VM inference placement 추천
- inference deployment plan 생성
- service operations readiness pipeline

비핵심 legacy 계층, 외부 프레임워크, 후처리 도구, 로컬 cluster 검증 스크립트는
제출/시연 패키지에서 제외했다.

## 공통 검증

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

검증 명령은 Go 코드 안에서 다음 순서로 동작한다.

1. `select-ops-llm`과 동일한 LLM 선정 로직을 실행한다.
2. `list-agents`와 동일한 Agent registry 조회 로직을 실행한다.
3. `validate-agent-action`과 동일한 bounded action 검증을 실행한다.
4. `recommend-inference-placement`와 동일한 CPU/GPU VM 배치 추천을 실행한다.
5. `plan-inference-deployment`와 동일한 deployment plan 생성을 실행한다.
6. `run-service-operations`와 동일한 통합 readiness pipeline을 실행한다.

결과는 로컬 실행 산출물로 아래 경로에 저장된다.

```text
runs/team-validation/<timestamp>/
```

`runs/`는 제출 소스 패키지에 포함하지 않는다.

## 성공 기준

```text
team-validation valid = true
selected_model = gpt-5.5
AIApplicationManagementAgent action 검증 통과
selected_resource = gpu-vm-l4
deployment_plan 생성
run-service-operations valid = true
run-service-operations guard_backend = go
```

## Go API 직접 실행

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

다른 터미널에서 확인한다.

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/openapi.yaml
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","namespace":"online-boutique","deployment":"paymentservice","mode":"mock","guard_backend":"go"}'
```

## 오류 처리 기준

1. Go 1.25 이상이 설치되어 있고 `go version`이 동작하는지 확인한다.
2. `go/service-control-api`에서 검증 명령을 실행했는지 확인한다.
3. 실패한 step JSON을 `runs/team-validation/<timestamp>/`에서 확인한다.
4. 필수 config 파일이 존재하는지 확인한다.

```text
config/ops_llm_benchmark.json
config/agent_registry.json
config/inference_optimization.json
```
