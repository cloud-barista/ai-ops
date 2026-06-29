# aiops-guard

`aiops-guard`는 bounded service-control action을 검증하는 standalone Go safety gate입니다.

구조화된 action request를 입력받아 명시 policy와 비교하고, 허용된 `kubectl` command를 rendering하며, 선택된 mode에서만 실행합니다.

## 여기서 Go를 사용하는 이유

- 최종 service-control action은 deterministic하고 audit하기 쉬워야 합니다.
- guard는 LLM reasoning과 외부 experiment tool에서 독립적입니다.
- JSON request/response contract는 cluster mutation 없이 service-control API, CI, reviewer가 확인할 수 있습니다.

## service-control-api와의 관계

`go/service-control-api`는 LLM selection, agent registry validation, CPU/GPU placement, deployment-plan generation, readiness reporting을 수행합니다.

`go/aiops-guard`는 별도 CLI contract를 통해 bounded Kubernetes service-control action을 검증합니다. service-control readiness response는 Go guard boundary를 나타내기 위해 `guard_validation`을 보고합니다. 이 standalone guard를 API module에서 full runtime으로 호출하는 것은 다음 integration step입니다.

## Request 예시

```json
{
  "mode": "mock",
  "namespace": "aiops-demo",
  "deployment": "aiops-service",
  "action": "scale_out",
  "replicas": 3,
  "allowed_namespaces": ["aiops-demo"],
  "allowed_deployments": ["aiops-service", "aiops-worker"],
  "min_replicas": 1,
  "max_replicas": 5
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
  "namespace": "aiops-demo",
  "deployment": "aiops-service",
  "action": "scale_out",
  "replicas": 3,
  "allowed_namespaces": ["aiops-demo"],
  "allowed_deployments": ["aiops-service", "aiops-worker"],
  "min_replicas": 1,
  "max_replicas": 5
}
JSON
```

`mock` mode는 `kubectl`을 실행하지 않고 command를 validate/render합니다.

`dry-run` mode는 mutating command에 `--dry-run=server`를 추가합니다.

`real` mode는 현재 `KUBECONFIG`로 검증된 command를 실행합니다.
