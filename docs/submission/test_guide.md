# Test Guide

## 1. Purpose

This guide defines the functional test and validation procedure for the
1st-year Go-based service-control prototype. The tests validate prototype
behavior, not production performance or standardized LLM benchmark quality.

## 2. Go Guard Tests

```bash
cd go/aiops-guard
go test ./...
```

Validation item:

- Go guard bounded-action validation.

## 3. Service-Control API Tests

```bash
cd go/service-control-api
go test ./...
```

Validation items:

- API route behavior.
- Service-control model behavior.
- LLM policy selection logic.
- Agent registry validation.
- CPU/GPU placement and deployment-plan logic.

## 4. Team Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation
```

Validation items:

- Ops LLM selection policy prototype.
- Agent registry listing.
- Agent bounded-action validation.
- CPU/GPU VM placement recommendation.
- Kubernetes deployment-plan generation.
- Mock deployment dry-run and guard-readiness validation.
- Integrated service-operations readiness.

## 5. Expected Signals

Expected prototype-level output signals:

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

These signals confirm that the Go API/CLI validation flow is wired correctly.
They do not prove standardized LLM evaluation quality, production performance, live GPU
scheduling, or actual cloud provisioning.

## 6. Validation Evidence Files

When `team-validation` is executed with `--output-dir`, the following JSON files
can be preserved as validation evidence:

| File | Validation Meaning |
| --- | --- |
| `00_team_validation_summary.json` | Summary of all validation steps |
| `01_select_ops_llm.json` | Ops LLM policy selection |
| `02_list_agents.json` | Registered agent list |
| `03_validate_agent_action.json` | Agent bounded-action validation |
| `04_recommend_inference_placement.json` | CPU/GPU VM placement recommendation |
| `05_plan_inference_deployment.json` | Kubernetes deployment-plan generation |
| `06_run_service_operations.json` | Integrated service-operations readiness |

## 7. Preserving Failed Logs and Error Messages

If validation fails, preserve the full terminal output and the generated JSON
files under a dated directory, for example:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/validation-YYYYMMDD-HHMMSS
```

Recommended failed-log record:

| Item | What to Preserve |
| --- | --- |
| Command | Exact command that failed |
| Environment | OS, Go version, branch, latest commit |
| Error output | Full stderr/stdout text |
| JSON evidence | Generated JSON files, if any |
| Human note | Short explanation of the observed failure and next action |

## 8. Human Review Items

Human review should confirm:

- Tests were run from the correct Go module directories.
- README links and document links resolve correctly.
- DOCX submission copies exist when claimed.
- Prototype boundary statements are present.
- The repository does not claim production readiness.
- The repository does not claim final standardized LLM benchmark results.
