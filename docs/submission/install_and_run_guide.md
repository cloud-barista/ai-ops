# Installation and Usage Guide

## 1. Purpose

This guide describes how to install and run the 1st-year Go-based
service-control functional prototype. The default validation path is local and
uses mock execution. A live Kubernetes cluster or actual GPU VM provisioning is
not required for the default validation path.

## 2. Go Version Requirement

- Go 1.25 or newer is recommended.
- Both Go modules use Go 1.25 because the service-control API dependency set is
  normalized by `go mod tidy` to `go 1.25.0`.

Check the Go version:

```bash
go version
```

## 3. Repository Setup

```bash
git checkout geon
git pull --ff-only origin geon
```

Download Go module dependencies:

```bash
cd go/service-control-api
go mod download

cd ../aiops-guard
go mod download
```

## 4. Run Team Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control team-validation \
  --output-dir ../../runs/my-first-validation
```

Expected integrated signal:

```text
valid = true
```

Generated validation evidence:

| File | Meaning |
| --- | --- |
| `runs/my-first-validation/01_select_ops_llm.json` | Ops LLM policy selection result |
| `runs/my-first-validation/02_list_agents.json` | Agent registry listing |
| `runs/my-first-validation/03_validate_agent_action.json` | Agent bounded-action validation |
| `runs/my-first-validation/04_recommend_inference_placement.json` | CPU/GPU VM placement recommendation |
| `runs/my-first-validation/05_plan_inference_deployment.json` | Deployment/control plan |
| `runs/my-first-validation/06_run_service_operations.json` | Integrated service-operations readiness |

## 5. Run CLI Commands

Ops LLM policy selection:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

Agent registry listing:

```bash
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

CPU/GPU placement recommendation:

```bash
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Deployment/control plan:

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Integrated service-operations readiness:

```bash
go run ./cmd/aiops-service-control run-service-operations \
  --llm-config ../../config/ops_llm_benchmark.json \
  --llm-policy quality_first \
  --inference-config ../../config/inference_optimization.json \
  --workload llm-chat-inference \
  --recovery-namespace aiops-demo \
  --recovery-deployment aiops-service \
  --mode mock \
  --guard-backend go
```

## 6. Run the API Server

Terminal 1:

```bash
cd go/service-control-api
go run ./cmd/service-control-api
```

Terminal 2:

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/openapi.yaml
```

Integrated API example:

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/service-operations/run \
  -H 'content-type: application/json' \
  -d '{"llm_policy":"quality_first","workload":"llm-chat-inference","recovery_namespace":"aiops-demo","recovery_deployment":"aiops-service","mode":"mock","guard_backend":"go"}'
```

## 7. Expected Results

Expected prototype-level signals:

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

The values above validate the policy and control-flow wiring of the prototype.
They are not final standardized LLM benchmark results.

## 8. Mock Mode

The default `mock` mode generates and validates the service-control readiness
structure without mutating a live cluster. In mock mode:

- Kubernetes deployment manifests are generated.
- Deployment dry-run output is simulated.
- Agent review and guard-readiness fields are produced.
- Actual GPU VM provisioning is not performed.
- Live Kubernetes mutation is not performed.

## 9. DOCX Conversion

The repository includes a Bash conversion script:

```bash
bash scripts/generate_docx_deliverables.sh
```

For Windows environments where Bash is not convenient, run Pandoc directly from
PowerShell:

```powershell
pandoc docs/submission/requirements_definition.md -o docs/submission/requirements_definition.docx
pandoc docs/deliverables/01_llm_operation_management_design.md -o docs/deliverables/docx/01_LLM_Operation_Management_Design.docx
pandoc docs/deliverables/02_agent_registration_management_prototype.md -o docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx
pandoc docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md -o docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx
```

If conversion tooling is unavailable, the Markdown source files remain the
authoritative deliverable sources and the DOCX files should be treated as
conversion targets.
