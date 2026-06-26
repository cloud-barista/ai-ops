# aiops-guard

`aiops-guard` is the standalone Go safety gate for bounded service-control
actions.

It receives a structured action request, validates it against an explicit
policy, renders the allowed `kubectl` command, and executes only in the
selected mode.

## Why Go Is Used Here

- Final service-control actions should be deterministic and easy to audit.
- The guard is independent from LLM reasoning and external experiment tools.
- The JSON request/response contract can be checked by the service-control API,
  CI, or a reviewer without running a cluster mutation.

## Relationship To service-control-api

`go/service-control-api` performs LLM selection, agent registry validation,
CPU/GPU placement, deployment-plan generation, and readiness reporting.

`go/aiops-guard` validates bounded Kubernetes service-control actions through a
separate CLI contract. The service-control readiness response reports
`guard_validation` for the Go guard boundary. Full runtime invocation of this
standalone guard from the API module is a planned next integration step.

## Request Example

```json
{
  "mode": "mock",
  "namespace": "online-boutique",
  "deployment": "paymentservice",
  "action": "scale_out",
  "replicas": 3,
  "allowed_namespaces": ["online-boutique"],
  "allowed_deployments": ["paymentservice", "checkoutservice"],
  "min_replicas": 1,
  "max_replicas": 5
}
```

## Run

```bash
cd go/aiops-guard
go test ./...
```

```bash
cat <<'JSON' | go run ./cmd/aiops-guard --input -
{
  "mode": "mock",
  "namespace": "online-boutique",
  "deployment": "paymentservice",
  "action": "scale_out",
  "replicas": 3,
  "allowed_namespaces": ["online-boutique"],
  "allowed_deployments": ["paymentservice", "checkoutservice"],
  "min_replicas": 1,
  "max_replicas": 5
}
JSON
```

`mock` mode validates and renders a command without running `kubectl`.

`dry-run` mode appends `--dry-run=server` to mutating commands.

`real` mode runs the validated command with the current `KUBECONFIG`.
