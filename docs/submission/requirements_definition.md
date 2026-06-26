# Requirements Definition

## Functional Requirements

| ID | Requirement | Status |
| --- | --- | --- |
| FR-01 | Provide Go-based Ops LLM selection API/CLI. | implemented |
| FR-02 | Provide Go-based agent registry listing and bounded-action validation. | implemented |
| FR-03 | Evaluate CPU/GPU VM candidates by SLO, throughput, cost, and capacity. | implemented |
| FR-04 | Generate a Kubernetes deployment/control plan for the selected VM candidate. | implemented |
| FR-05 | Generate an integrated readiness report combining LLM selection, placement, manifest dry-run, agent review, and guard-readiness validation. | implemented |

## Non-Functional Requirements

| ID | Requirement |
| --- | --- |
| NFR-01 | Keep the submission/demo path Go-centered. |
| NFR-02 | Exclude cluster-specific real execution from common validation. |
| NFR-03 | Exclude non-core legacy code, external experiment runners, and unrelated post-processing tools from the source package. |
| NFR-04 | Keep configuration as reproducible JSON contracts. |
| NFR-05 | Clearly mark LLM policy values as prototype baselines, not final standardized benchmarks. |

## Deliverables

| Deliverable | Location |
| --- | --- |
| LLM operation-management structure design | `docs/design/ops_llm_selection_guide.md`, `docs/design/research_task_integration_design.md` |
| Agent registration-management prototype | `config/agent_registry.json`, `go/service-control-api` |
| AI application deployment/control inference optimization strategy | `config/inference_optimization.json`, `docs/design/inference_optimization_guide.md`, `docs/design/ai_application_deployment_strategy.md` |
| Runnable API/CLI | `go/service-control-api` |
| Bounded action guard | `go/aiops-guard` |
| Functional evaluation summary | `docs/submission/evaluation_summary.md` |
