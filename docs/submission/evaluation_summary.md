# Evaluation Summary

## 1. Scope

This document summarizes the current functional evaluation items for the
1st-year Go-based service-control prototype. The evaluation demonstrates
prototype behavior and integration readiness. It is not a final production
performance benchmark and does not claim final standardized LLM benchmark
quality.

## 2. Evaluation Items

| Item | Validation Method | Current Evidence Type |
| --- | --- | --- |
| Ops LLM selection policy prototype | `select-ops-llm` and `team-validation` | Policy-based candidate ranking output |
| Agent registry and bounded-action validation | `list-agents`, `show-agent`, `validate-agent-action` | Registered agents and allowed action checks |
| CPU/GPU VM placement recommendation | `recommend-inference-placement` | Selected resource and rejected-resource explanation |
| Kubernetes deployment-plan generation | `plan-inference-deployment` | Generated namespace, deployment, node selector, resource limits, and control actions |
| Mock dry-run and guard validation | `run-service-operations` | Manifest dry-run output and `guard_validation.valid = true` |
| Go unit tests | `go test ./...` in each Go module | Module-level test pass/fail output |
| Integrated readiness | `team-validation` | JSON output files under `runs/<output-dir>/` |

## 3. Expected Prototype Signals

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

These signals confirm the current functional wiring of the prototype. They do
not prove production performance, actual cloud provisioning, or final model
quality.

## 4. Deliverable Relationship

| Evaluation Area | Related Deliverable |
| --- | --- |
| LLM policy selection | `docs/deliverables/01_llm_operation_management_design.md` |
| Agent registry validation | `docs/deliverables/02_agent_registration_management_prototype.md` |
| CPU/GPU placement and deployment-control plan | `docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md` |
| API behavior | `docs/submission/functional_api_guide.md` and `docs/submission/openapi_service_control.yaml` |
| Test procedure | `docs/submission/test_guide.md` |
| Development validation records | `docs/submission/development_validation_log.md` |

## 5. Benchmark Boundary

The current LLM policy scores are manually defined prototype baselines in
`config/ops_llm_benchmark.json`. They should be interpreted as functional
validation inputs for the Go selection flow. Final quantitative reporting would
require controlled per-model Ops evaluation runs, fixed prompts, fixed datasets,
repeatable metrics, and documented scoring rules.

## 6. Infrastructure Boundary

The default service-control path uses mock validation and does not mutate a live
cluster. Actual GPU VM provisioning remains an AI-Infra or CB-Tumblebug
integration boundary. The prototype generates placement recommendations and
deployment plans, but does not claim actual GPU VM creation in the local default
validation path.

## 7. Limitations

- LLM policy scores are manually defined prototype baselines.
- The current package does not claim standardized LLM benchmark results.
- The default service-control path uses mock validation.
- `aiops-guard` is implemented as a standalone module; full runtime invocation
  from `service-control-api` remains a planned integration step.
- Actual GPU VM provisioning and live cluster scheduling require external
  infrastructure and credentials.
