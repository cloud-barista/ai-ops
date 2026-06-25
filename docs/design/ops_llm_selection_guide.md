# Ops LLM Selection Guide

## Purpose

Ops LLM selection chooses the runtime model for service-control reasoning under
a policy such as `quality_first` or `cost_first`.

The Go implementation is in:

```text
go/service-control-api/internal/api/service.go
```

The policy data is in:

```text
config/ops_llm_benchmark.json
```

## Candidate Roles

| Candidate | Role |
| --- | --- |
| `gpt-5.5` | primary Ops reasoning model |
| `gpt-4o-mini` | low-cost smoke-test and fallback model |
| `codex-cross-check-agent` | implementation and code-review cross-check agent |

## Scoring

Each policy combines normalized metrics:

| Metric | Meaning |
| --- | --- |
| `accuracy` | Ops task correctness baseline |
| `metric_success` | ability to use available operation metrics |
| `action_validity` | rate of bounded and safe action proposals |
| `consistency` | repeatability of decisions |
| `ttd` | inverse time-to-decision score |
| `cost` | inverse estimated cost score |
| `latency` | inverse latency score |

The current config is a prototype policy baseline. Final quantitative reporting
should regenerate the numbers from standardized per-model Ops runs, but the
submission/demo package only needs the policy wiring and deterministic ranking.

## Go CLI

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

Expected selection:

```text
selected_model = gpt-5.5
```
