# LLM Operation Management Structure Design

LLM 운영 관리 구조 설계서

## 1. Purpose

This document describes the LLM operation-management structure implemented in
the 1st-year Go-based prototype. The purpose of this design is to define how an
operation-oriented LLM candidate is selected, how the selected candidate is used
in the service-control flow, and how deterministic Go logic validates the
surrounding operation pipeline.

The document is written as an official design deliverable source file. It should
be reviewed together with the Go implementation, JSON policy configuration, and
submission validation records.

## 2. 1st-Year Implementation Scope

The implementation scope includes:

- Ops analysis and optimal LLM selection policy structure.
- AI LLM operation-management structure design.
- Policy-based candidate ranking.
- Functional validation through Go CLI/API.
- Integration with agent registry, placement recommendation, and
  service-operations readiness report.

This document does not describe a complete production LLMOps platform. It
focuses on a policy-based LLM selection structure that can be implemented and
validated within the 1st-year research scope. The current implementation does
not replace a large-scale log analysis system or a real-time LLM serving
platform. Instead, it validates the selection policy, candidate ranking, and
service-control readiness reporting through Go API/CLI and JSON configuration.

## 3. Overall Design

```text
Ops policy/config
-> Ops LLM candidate ranking
-> selected runtime candidate
-> agent registry validation
-> CPU/GPU VM placement recommendation
-> Kubernetes deployment-plan generation
-> service-operations readiness report
```

The Go service-control layer keeps the LLM selection decision separate from
deterministic validation. The policy configuration provides candidate metrics
and policy weights. The Go logic ranks candidates, selects the runtime label,
and passes the selected candidate into the broader service-control readiness
flow.

## 4. Policy Configuration

The LLM policy configuration is maintained in:

```text
config/ops_llm_benchmark.json
```

Important interpretation rules:

- Current values are manually defined prototype policy baselines.
- They are not final standardized benchmark results.
- Final quantitative reporting must regenerate values through controlled
  per-model Ops evaluation runs.
- Fixed prompts, datasets, metric collection, and repeatable scoring rules are
  required for final evaluation.
- Candidate names are prototype policy labels, not verified provider benchmark
  claims.

The configuration contains policy weights such as `quality_first` and
`cost_first`, candidate role labels, and normalized score inputs used by the Go
ranking function.

## 5. Candidate Roles

| Candidate | Role |
| --- | --- |
| `primary-ops-llm` | Primary Ops reasoning candidate for service-control decisions |
| `low-cost-ops-llm` | Low-cost smoke-test and fallback candidate |
| `code-cross-check-agent` | Code and documentation cross-check candidate |

These names are role labels for prototype policy wiring. They do not claim that
a specific commercial or open-source LLM has been finally benchmarked.

## 6. Scoring Method

| Metric | Meaning |
| --- | --- |
| `accuracy` | Prototype correctness signal for operation judgment |
| `metric_success` | Ability to use available operation metrics |
| `action_validity` | Whether proposed actions remain inside bounded-control rules |
| `consistency` | Repeatability of decisions |
| `ttd` | Inverse time-to-decision score |
| `cost` | Inverse estimated operation cost score |
| `latency` | Inverse latency score |

Each policy combines normalized metric values using configured weights. For
example, the `quality_first` policy prioritizes operation correctness, metric
usage, action validity, and consistency, while the `cost_first` policy gives
more weight to cost and latency. The selected candidate is the one with the
highest weighted score under the requested policy.

## 7. Go Implementation

| Component | Path |
| --- | --- |
| Service logic | `go/service-control-api/internal/api/service.go` |
| Request/response models | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| Policy configuration | `config/ops_llm_benchmark.json` |

The implementation loads the JSON policy file, validates the requested policy,
normalizes score inputs, computes weighted scores, and returns the selected
candidate and ranked alternatives.

## 8. CLI Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control select-ops-llm \
  --config ../../config/ops_llm_benchmark.json \
  --policy quality_first
```

Expected prototype-level signal:

```text
selected_model = primary-ops-llm
```

This output confirms that the policy selection flow is wired correctly. It does
not represent a final standardized LLM performance result.

## 9. API Validation

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/ops-llm/select \
  -H 'content-type: application/json' \
  -d '{"policy":"quality_first"}'
```

The API returns the selected candidate, score, ranked candidate list, rationale,
and validity flag.

## 10. Design Boundary

This design does not claim standardized LLM evaluation completion. It defines a
prototype policy-selection structure that can later be connected to standardized
evaluation data. The current implementation is useful for demonstrating how LLM
candidate selection can be integrated into a broader AIOps service-control
framework, but production deployment would require real operation traces,
repeatable test scenarios, provider-specific model evaluation, monitoring, and
governance.

## 11. Related Artifacts

| Artifact | Path |
| --- | --- |
| LLM policy config | `config/ops_llm_benchmark.json` |
| Go service logic | `go/service-control-api/internal/api/service.go` |
| API/CLI models | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| Supporting selection guide | `docs/design/ops_llm_selection_guide.md` |
| Cross-validation design note | `docs/design/go_and_llm_cross_validation.md` |
| Functional/API guide | `docs/submission/functional_api_guide.md` |
| Test guide | `docs/submission/test_guide.md` |
