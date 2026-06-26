# Evaluation Summary

## Scope

This document summarizes the current functional evaluation items for the
1st-year Go prototype. The evaluation demonstrates prototype behavior and
integration readiness. It is not a final production performance benchmark.

## Evaluation Items

| Item | Validation method | Current evidence |
| --- | --- | --- |
| LLM selection policy validation | `select-ops-llm` and `team-validation` | Selects `primary-ops-llm` under `quality_first` policy |
| Agent registry and bounded-action validation | `list-agents`, `show-agent`, `validate-agent-action` | Confirms registered agents and allowed action boundaries |
| CPU/GPU VM placement recommendation | `recommend-inference-placement` | Selects `gpu-vm-l4` for `llm-chat-inference` |
| Kubernetes deployment-plan generation | `plan-inference-deployment` | Produces namespace, deployment, node selector, resource limits, and control actions |
| Mock dry-run / guard validation | `run-service-operations` | Produces manifest dry-run output and `guard_validation.valid = true` |
| Go unit tests and CI validation | `go test ./...`, GitHub Actions | Tests cover both Go modules and the integrated CLI path |

## Limitations

- LLM policy scores are manually defined prototype baselines.
- The current package does not claim standardized LLM benchmark results.
- The default service-control path uses mock validation and does not mutate a live cluster.
- `aiops-guard` is implemented as a standalone module; full runtime invocation from `service-control-api` is a planned integration step.
- Actual GPU VM provisioning remains an AI-Infra or CB-Tumblebug boundary, not a replacement target for this prototype.
