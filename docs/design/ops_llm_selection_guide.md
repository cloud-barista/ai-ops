# Ops LLM Selection Guide

## Purpose

Ops LLM selection chooses the prototype runtime model label for service-control
reasoning under a policy such as `quality_first` or `cost_first`.

The Go implementation is in:

```text
go/service-control-api/internal/api/service.go
```

The policy data is in:

```text
config/ops_llm_benchmark.json
```

## Prototype Data Boundary

The current candidate scores are manually defined prototype policy values. They
exist to validate ranking logic, API wiring, and report generation. They are not
standardized benchmark results.

Final quantitative reporting must regenerate the values through controlled
per-model Ops evaluation runs with fixed prompts, datasets, metric collection,
and repeatable scoring rules.

## Candidate Roles

| Candidate | Role |
| --- | --- |
| `primary-ops-llm` | Primary Ops reasoning candidate |
| `low-cost-ops-llm` | Low-cost smoke-test and fallback candidate |
| `code-cross-check-agent` | Code and documentation cross-check candidate |

## Scoring

Each policy combines normalized metrics:

| Metric | Meaning |
| --- | --- |
| `accuracy` | Ops task correctness baseline |
| `metric_success` | Ability to use available operation metrics |
| `action_validity` | Rate of bounded and safe action proposals |
| `consistency` | Repeatability of decisions |
| `ttd` | Inverse time-to-decision score |
| `cost` | Inverse estimated cost score |
| `latency` | Inverse latency score |

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

Expected selection:

```text
selected_model = primary-ops-llm
```
