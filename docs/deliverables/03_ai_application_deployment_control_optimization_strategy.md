# AI 응용 배포·제어 추론 최적화 전략 설계서

English title: AI Application Deployment and Control Optimization Strategy

## 1. Purpose

This document describes the AI application deployment and control optimization
strategy implemented in the 1st-year Go-based service-control prototype. The
purpose is to define how an AI workload can be matched with a CPU/GPU VM
candidate based on accelerator requirements, SLO constraints, capacity, and cost
policy.

The document is an official design deliverable source file. It should be
reviewed with the inference optimization configuration, Go service-control
implementation, API contract, and validation evidence.

## 2. Prototype Scope

The prototype scope includes:

- CPU/GPU VM-based AI application placement recommendation.
- AI workload and resource profile definition.
- Latency, throughput, cost, and capacity-based scoring.
- Kubernetes deployment-plan generation.
- Mock dry-run readiness validation.
- Future extension toward GPU/NPU and AI semiconductor infrastructure.

The current implementation is not a replacement for a real GPU scheduler or
cloud VM provisioning platform. It is a Go-based recommendation and planning
prototype. It demonstrates how deployment-control decisions can be structured
before connecting to live Kubernetes nodes, GPU telemetry, CB-Tumblebug VM
inventory, or AI semiconductor cluster managers.

## 3. Input Configuration

The placement policy is maintained in:

```text
config/inference_optimization.json
```

The configuration contains two main sections: resources and workloads. The
resources section describes CPU/GPU VM candidates, expected latency, throughput,
cost, available capacity, node selector, resource limits, and supported model
types. The workloads section describes AI application requirements such as model
type, accelerator need, estimated VRAM, latency SLO, minimum throughput, service
name, namespace, container image, and replica count.

## 4. Resource Candidates

| Resource | Accelerator | Intended Use |
| --- | --- | --- |
| `cpu-vm-standard` | CPU | Lightweight text classification and embedding workloads |
| `gpu-vm-l4` | GPU | Cost/performance-balanced LLM or vision inference |
| `gpu-vm-a100` | GPU | Higher-performance LLM or vision inference |

These resources are prototype resource profiles. They do not indicate that a
real cloud VM has been provisioned in the default validation path.

## 5. Workload Profiles

| Workload | Model Type | Accelerator Required | Purpose |
| --- | --- | --- | --- |
| `llm-chat-inference` | `llm` | `true` | LLM inference service requiring GPU acceleration |
| `text-classifier` | `text-classification` | `false` | Lightweight text classification service that can run on CPU |

## 6. Placement Decision Logic

The placement decision logic performs the following steps:

1. Check whether the workload requires an accelerator.
2. Check whether the resource supports the workload model type.
3. Check GPU memory requirements when GPU/NPU resources are used.
4. Check latency SLO.
5. Check minimum throughput requirement.
6. Check available capacity.
7. Rank eligible resources using a weighted score.

Ineligible resources are reported in `rejected_resources`, which makes the
decision auditable.

## 7. Scoring Strategy

```text
score =
  latency_weight * latency_score
+ throughput_weight * throughput_score
+ cost_weight * cost_score
+ capacity_weight * capacity_score
```

| Factor | Meaning |
| --- | --- |
| `latency_score` | Higher when expected latency satisfies the workload SLO |
| `throughput_score` | Higher when expected throughput satisfies the minimum requirement |
| `cost_score` | Higher when cost is lower among eligible candidates |
| `capacity_score` | Higher when more replicas are available |

Default weights:

| Factor | Weight |
| --- | --- |
| latency | 0.35 |
| throughput | 0.30 |
| cost | 0.20 |
| capacity | 0.15 |

## 8. Expected Prototype Results

| Workload | Selected Resource | Action | Reason |
| --- | --- | --- | --- |
| `llm-chat-inference` | `gpu-vm-l4` | `deploy_on_gpu_vm` | The workload requires GPU acceleration, and the L4 candidate satisfies the configured SLO and cost balance |
| `text-classifier` | `cpu-vm-standard` | `deploy_on_cpu_vm` | The workload can satisfy the configured SLO on CPU, making CPU placement more cost-efficient |

These are expected prototype-level outputs under the current JSON policy. They
are not production scheduling guarantees.

## 9. Kubernetes Deployment Plan

After selecting a resource, the Go service-control API generates a Kubernetes
deployment/control plan. The plan includes service name, container image, target
resource, target accelerator, namespace, deployment name, replica count, node
selector, resource requests, resource limits, control actions, monitoring
metrics, and SLO values.

If the selected resource is a GPU VM, the generated resource limit includes
`nvidia.com/gpu`. The node selector is derived from the selected resource
profile so that the deployment plan can later be mapped to accelerator-aware
Kubernetes scheduling.

The current prototype generates the deployment plan and mock dry-run readiness
output. It does not provision a real GPU VM or mutate a live Kubernetes cluster
by default.

## 10. Service-Control Integration

```text
LLM selection
-> Agent registry validation
-> CPU/GPU VM placement recommendation
-> Kubernetes deployment-plan generation
-> manifest dry-run
-> agent review
-> guard validation
-> service-operations readiness report
```

This flow is implemented in the Go service-control API and CLI. The integrated
output is intended to show how deployment/control decisions can be combined with
LLM policy selection and bounded-action validation.

## 11. CLI Validation

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control recommend-inference-placement \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

```bash
go run ./cmd/aiops-service-control plan-inference-deployment \
  --config ../../config/inference_optimization.json \
  --workload llm-chat-inference
```

Expected prototype-level signal:

```text
selected_resource = gpu-vm-l4
```

## 12. Design Boundary

This strategy is a 1st-year prototype design. It does not replace production
cloud schedulers, Kubernetes schedulers, GPU device plugins, or CB-Tumblebug
resource provisioning. Instead, it defines the decision structure needed before
integrating with real AI semiconductor infrastructure. Future work may add live
GPU metrics, NPU profiles, Kubernetes node status, multi-cloud VM inventory, and
closed-loop control.

## 13. Related Artifacts

| Artifact | Path |
| --- | --- |
| Inference optimization config | `config/inference_optimization.json` |
| Inference optimization guide | `docs/design/inference_optimization_guide.md` |
| AI application deployment strategy | `docs/design/ai_application_deployment_strategy.md` |
| Go service logic | `go/service-control-api/internal/api/service.go` |
| API/CLI models | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| OpenAPI contract | `docs/submission/openapi_service_control.yaml` |
| Functional/API guide | `docs/submission/functional_api_guide.md` |
