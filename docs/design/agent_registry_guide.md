# Agent Registry Guide

## Purpose

The agent registry defines the AI agents used by the service-control prototype,
including each agent role, responsibility boundary, allowed actions, and reward
signals. The registry is represented as JSON so that the Go API/CLI can inspect
and validate it deterministically.

Configuration file:

```text
config/agent_registry.json
```

## Registered Agents

| Agent | Role |
| --- | --- |
| `AIServiceHASupportAgent` | Detect service health, availability, and recovery needs |
| `AIApplicationManagementAgent` | Propose AI application deployment/control actions |
| `AISemiconductorInfraOpsAgent` | Validate CPU/GPU VM and infrastructure constraints |
| `CostOptimizationAgent` | Review cost and resource-efficiency implications |

## Bounded Actions

Each agent has an explicit `bounded_actions` list. The Go service-control layer
uses this list to reject actions that do not belong to a selected agent. This is
the prototype-level boundary before any service-control action can be treated
as ready.

## Go CLI

List registered agents:

```bash
cd go/service-control-api
go run ./cmd/aiops-service-control list-agents \
  --registry ../../config/agent_registry.json
```

Show a single agent:

```bash
go run ./cmd/aiops-service-control show-agent \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent
```

Validate an agent action:

```bash
go run ./cmd/aiops-service-control validate-agent-action \
  --registry ../../config/agent_registry.json \
  --agent AIApplicationManagementAgent \
  --action app_scale_deployment
```

## API Path

| Function | Path |
| --- | --- |
| Agent list | `GET /api/v1/agents` |
| Integrated readiness report | `POST /api/v1/service-operations/run` |
