# Agent Registration Management Prototype

에이전트 등록 관리 프로토타입

## 1. Purpose

This document describes the AI agent registration-management prototype
implemented in the 1st-year Go-based service-control framework. The purpose is
to define how AI agents are represented, how their allowed action boundaries are
registered, and how the Go service-control layer validates agent actions before
they are treated as acceptable service-control decisions.

The document is an official design deliverable source file. It should be
reviewed with the agent registry JSON file, Go service-control implementation,
and API/CLI validation outputs.

## 2. Prototype Scope

The prototype scope includes:

- Agent metadata registration.
- Agent role and responsibility definition.
- Bounded action list management.
- Reward-signal documentation for future evaluation.
- Go CLI/API validation for registered agents and actions.
- Integration with the integrated service-operations readiness report.

The current prototype does not implement full autonomous multi-agent
orchestration. It provides the registration and bounded-action validation
foundation required before autonomous service-control agents can safely operate.

## 3. Registry Configuration

The agent registry is maintained in:

```text
config/agent_registry.json
```

Each agent entry contains:

- `name`
- `korean_name`
- `role`
- `responsibilities`
- `bounded_actions`
- `reward_signals`
- `enabled`

The registry is treated as a reviewable configuration contract. Go logic reads
the registry and validates whether an agent exists and whether a requested
action belongs to the agent's allowed action set.

## 4. Registered Agents

| Agent | Main Responsibility |
| --- | --- |
| `AIServiceHASupportAgent` | Reviews service availability and recovery need |
| `AIApplicationManagementAgent` | Reviews AI application deployment and control actions |
| `AISemiconductorInfraOpsAgent` | Validates CPU/GPU VM and infrastructure constraints |
| `CostOptimizationAgent` | Reviews cost and resource-efficiency implications |

### AIServiceHASupportAgent

This agent is responsible for checking service availability signals and
deciding whether recovery-related action may be needed. In the current
prototype, it represents the HA support perspective rather than executing
recovery directly.

### AIApplicationManagementAgent

This agent is responsible for AI application deployment and control decisions.
It can propose application-level actions such as selecting an inference VM,
planning a deployment, scaling a deployment, or observing service metrics.

### AISemiconductorInfraOpsAgent

This agent represents the infrastructure and AI semiconductor resource
perspective. In the current 1st-year prototype, it focuses on CPU/GPU VM
constraints, including accelerator requirements, latency, throughput, memory,
and capacity. The structure can later be extended toward GPU/NPU cluster
orchestration.

### CostOptimizationAgent

This agent reviews resource usage and cost-related implications. It is used to
prevent unnecessary high-cost actions such as excessive GPU usage or unnecessary
scale-out.

## 5. Bounded Actions

Bounded actions define which actions each agent is allowed to approve or
propose. This is a safety-oriented design choice. Instead of allowing an LLM or
agent to freely generate arbitrary infrastructure commands, the prototype checks
whether the requested action belongs to the selected agent's allowed action
list.

For example, `AIApplicationManagementAgent` can validate application-level
actions such as `app_scale_deployment` or `app_plan_deployment`, while
`AISemiconductorInfraOpsAgent` is responsible for infrastructure
placement-related actions. If an action does not belong to the selected agent,
the Go validation function returns false.

## 6. Reward Signals

Reward signals are included as a design-level representation of what each agent
should optimize or avoid. In the current prototype, reward signals are not used
to train a reinforcement learning model. Instead, they document the intended
evaluation direction for each agent. For example, a service HA agent receives a
positive reward when recovery-need judgment matches the actual incident state,
and a cost optimization agent receives a penalty when unnecessary high-cost
resource usage is approved.

The reward signals are design artifacts for future evaluation and learning
extensions, not completed RL training results.

## 7. Go Implementation

| Component | Path |
| --- | --- |
| Agent registry config | `config/agent_registry.json` |
| Go service logic | `go/service-control-api/internal/api/service.go` |
| API/CLI models | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |

The Go implementation provides registry loading, agent lookup, action
validation, and integrated service-operation review output.

## 8. CLI Validation

List agents:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

Show one agent:

```bash
go run ./cmd/aiops-service-control show-agent \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent
```

Validate action:

```bash
go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent \
  --action app_scale_deployment
```

Expected signal:

```text
valid = true
```

## 9. API Integration

| Function | API Path |
| --- | --- |
| List registered agents | `GET /api/v1/agents` |
| Run integrated service operation readiness | `POST /api/v1/service-operations/run` |

The integrated service-operations response includes `agent_reviews`, where the
application, infrastructure, and cost review perspectives are reported.

## 10. Design Boundary

The current prototype does not claim full autonomous agent orchestration. It
provides the registration and bounded-action validation foundation required
before autonomous service-control agents can safely operate. Future work may
connect this registry to multi-agent planning, real operation metrics,
reinforcement learning feedback, and runtime policy governance.

## 11. Related Artifacts

| Artifact | Path |
| --- | --- |
| Agent registry config | `config/agent_registry.json` |
| Agent registry guide | `docs/design/agent_registry_guide.md` |
| Agent action/reward policy | `docs/design/agent_action_reward_policy.md` |
| Go service logic | `go/service-control-api/internal/api/service.go` |
| API/CLI models | `go/service-control-api/internal/api/models.go` |
| CLI entrypoint | `go/service-control-api/cmd/aiops-service-control/main.go` |
| Functional/API guide | `docs/submission/functional_api_guide.md` |
| Test guide | `docs/submission/test_guide.md` |
