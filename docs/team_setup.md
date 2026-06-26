# Team Validation Guide

This document summarizes the common Go-based validation path for the
submission/demo environment.

## Development Language Baseline

The common implementation language is Go. The following decision and validation
logic is executed through Go API/CLI paths:

- Ops LLM selection
- Agent registry listing and bounded-action validation
- CPU/GPU VM inference placement recommendation
- Inference deployment-plan generation
- Service-operations readiness pipeline

Non-core legacy paths, external orchestration experiments, post-processing
tools, and local cluster helper scripts are excluded from the submission/demo
package.

## Common Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

The command runs the same logic as these individual commands:

1. `select-ops-llm`
2. `list-agents`
3. `validate-agent-action`
4. `recommend-inference-placement`
5. `plan-inference-deployment`
6. `run-service-operations`

Results are saved under:

```text
runs/team-validation/<timestamp>/
```

`runs/` is local evidence and is not part of the submitted source package.

## Success Criteria

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

## Direct API Check

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

In another terminal:

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/openapi.yaml
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

## Troubleshooting

1. Confirm Go 1.25 or newer is installed.
2. Run validation from `go/service-control-api`.
3. Inspect failed step JSON files under `runs/team-validation/<timestamp>/`.
4. Confirm required config files exist:

```text
config/ops_llm_benchmark.json
config/agent_registry.json
config/inference_optimization.json
```
