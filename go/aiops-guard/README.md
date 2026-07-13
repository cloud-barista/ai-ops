# aiops-guard

`aiops-guard`는 bounded service-control action을 검증하는 standalone Go safety gate입니다.

구조화된 action request를 입력받아 명시 policy와 비교하고, CPU/GPU VM 대상의 허용된 action plan만 생성합니다.

## 여기서 Go를 사용하는 이유

- 최종 service-control action은 deterministic하고 audit하기 쉬워야 합니다.
- guard는 LLM reasoning과 외부 experiment tool에서 독립적입니다.
- JSON request/response contract는 실제 인프라 변경 없이 service-control API, CI, reviewer가 확인할 수 있습니다.

## service-control-api와의 관계

`go/service-control-api`는 LLM selection, agent registry validation, CPU/GPU placement, deployment-plan generation, readiness reporting을 수행합니다.

`go/aiops-guard`는 별도 CLI contract를 통해 bounded VM service-control action을 검증합니다. service-control readiness response는 Go guard boundary를 나타내기 위해 `guard_validation`을 보고합니다. 실제 VM 제어 API 호출은 외부 AI-Infra 연계 범위입니다.

## Request 예시

```json
{
  "mode": "mock",
  "service": "aiops-service",
  "target_resource": "gpu-vm-l4",
  "action": "scale_out",
  "instances": 3,
  "allowed_services": ["aiops-service", "aiops-worker"],
  "allowed_resources": ["cpu-vm-standard", "gpu-vm-l4"],
  "min_instances": 1,
  "max_instances": 5
}
```

## 실행

```bash
cd go/aiops-guard
go test ./...
```

```bash
cat <<'JSON' | go run ./cmd/aiops-guard --input -
{
  "mode": "mock",
  "service": "aiops-service",
  "target_resource": "gpu-vm-l4",
  "action": "scale_out",
  "instances": 3,
  "allowed_services": ["aiops-service", "aiops-worker"],
  "allowed_resources": ["cpu-vm-standard", "gpu-vm-l4"],
  "min_instances": 1,
  "max_instances": 5
}
JSON
```

`mock` mode는 시연용 action plan을 생성합니다.

`validate` mode는 동일 정책을 사용해 VM 서비스, 대상 자원, instance 범위를 검증합니다. 두 mode 모두 실제 인프라를 변경하지 않습니다.
