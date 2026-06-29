# Development/Test Validation Log

## 1. Purpose

This document records the validation commands, expected outputs, log
preservation method, human verification items, and current known limitations for
the 1st-year Go-based service-control prototype.

## 2. Validation Commands

Go guard tests:

```bash
cd go/aiops-guard && go test ./...
```

Service-control API tests:

```bash
cd go/service-control-api && go test ./...
```

Integrated team validation:

```bash
cd go/service-control-api && go run ./cmd/aiops-service-control team-validation
```

## 3. Expected Outputs

Expected prototype-level signals:

```text
selected_model = primary-ops-llm
selected_resource = gpu-vm-l4
valid = true
guard_backend = go
guard_validation.valid = true
```

Expected Go test behavior:

```text
go test ./... exits with status 0
```

## 4. Error Logging Policy

If a command fails, preserve:

- The exact command.
- The working directory.
- Full stdout and stderr.
- Go version.
- Git branch and latest commit.
- Generated JSON files, if any.
- Human note describing the suspected cause.

Error messages should not be paraphrased away. Keep the exact error text in the
record and add a short interpretation separately.

## 5. Human Verification Items

Human reviewers should verify:

- Test output was produced from the correct directories.
- README links point to existing repository files.
- OpenAPI YAML exists and is linked.
- Required Markdown deliverables exist.
- DOCX files exist before being described as generated.
- Prototype boundary statements are present.
- LLM policy values are described as manually defined prototype baselines.
- No production-ready claim is introduced.
- No final standardized LLM benchmark claim is introduced.

## 6. Current Known Limitations

- The default validation path uses mock mode.
- Actual GPU VM provisioning is outside the local default validation path.
- Live Kubernetes mutation is not performed by default.
- LLM policy values are manually defined prototype policy baselines.
- Final quantitative model reporting requires controlled per-model evaluation
  runs with fixed prompts, datasets, metrics, and scoring rules.

## 7. Latest Validation Record

Validation date: 2026-06-29

| Item | Result |
| --- | --- |
| Go guard tests | Executed in WSL Ubuntu-22.04 with `/usr/local/go/bin/go test ./...`; result: pass |
| Service-control API tests | Executed in WSL Ubuntu-22.04 with `/usr/local/go/bin/go test ./...`; result: pass |
| Team validation | Executed in WSL Ubuntu-22.04; result: `valid = true` |
| Team validation output directory | `runs/submission-validation-20260629-131247/` |
| DOCX conversion | Executed with PowerShell `pandoc`; four DOCX files generated and structurally reopened with `python-docx` |
| Link validation | Local Markdown links checked; result: pass |

## 8. Latest Command Evidence

Go guard tests:

```text
?    github.com/cloud-barista/ai-ops/go/aiops-guard/cmd/aiops-guard [no test files]
ok   github.com/cloud-barista/ai-ops/go/aiops-guard/internal/guard
```

Service-control API tests:

```text
?    kyunghee-aiops/service-control-api/cmd/aiops-service-control [no test files]
?    kyunghee-aiops/service-control-api/cmd/service-control-api [no test files]
ok   kyunghee-aiops/service-control-api/internal/api
```

Team validation summary:

```text
valid = true
select-ops-llm = true
list-agents = true
validate-agent-action = true
recommend-inference-placement = true
plan-inference-deployment = true
run-service-operations = true
```

DOCX structural validation:

```text
requirements_definition.docx = generated and reopened
01_LLM_Operation_Management_Design.docx = generated and reopened
02_Agent_Registration_Management_Prototype.docx = generated and reopened
03_AI_Application_Deployment_Control_Optimization_Strategy.docx = generated and reopened
```

Environment note:

```text
Windows PowerShell did not have go on PATH, so Go validation was executed
through WSL Ubuntu-22.04 using /usr/local/go/bin/go.
```

DOCX visual render note:

```text
DOCX files were generated and structurally validated. Visual render QA with
the local document renderer could not be completed because soffice/libreoffice
was not available in the current environment.
```
