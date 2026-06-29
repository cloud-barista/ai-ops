# 팀 검증 가이드

이 문서는 제출/시연 환경에서 공통으로 사용하는 Go 기반 검증 경로를 요약합니다.

## 개발 언어 기준

공통 구현 언어는 Go입니다. 다음 판단 및 검증 로직은 Go API/CLI 경로로 실행됩니다.

- Ops LLM selection
- 에이전트 registry 목록 조회 및 bounded-action 검증
- CPU/GPU VM 추론 배치 추천
- Inference deployment-plan generation
- Service-operations readiness pipeline

핵심 범위 밖 legacy path, external orchestration experiment, post-processing tool, local cluster helper script는 제출/시연 package에서 제외합니다.

## 공통 검증

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

이 명령은 다음 개별 명령과 같은 로직을 순서대로 실행합니다.

1. `select-ops-llm`
2. `list-agents`
3. `validate-agent-action`
4. `recommend-inference-placement`
5. `plan-inference-deployment`
6. `run-service-operations`

결과는 다음 위치에 저장할 수 있습니다.

```text
runs/team-validation/<timestamp>/
```

`runs/`는 local evidence이며 제출 source package에는 포함하지 않습니다.

## 성공 기준

```text
team-validation valid = true
selected_model = primary-ops-llm
AIApplicationManagementAgent action validation passes
selected_resource = gpu-vm-l4
deployment_plan is generated
run-service-operations valid = true
run-service-operations guard_backend = go
guard_validation.valid = true
```

## 직접 API 확인

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

다른 터미널:

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/openapi.yaml
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

## 문제 해결

1. Go 1.25 이상이 설치되어 있는지 확인합니다.
2. `go/service-control-api`에서 검증을 실행합니다.
3. 실패한 step의 JSON 파일을 `runs/team-validation/<timestamp>/`에서 확인합니다.
4. 필수 설정 파일이 존재하는지 확인합니다.

```text
config/ops_llm_benchmark.json
config/agent_registry.json
config/inference_optimization.json
```
