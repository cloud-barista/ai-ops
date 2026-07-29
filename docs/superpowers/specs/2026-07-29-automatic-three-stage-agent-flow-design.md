# Automatic Three-Stage Agent Flow Design

Date: 2026-07-29

## 1. Purpose

The geon PoC shall accept one user request and automatically execute the
following three stages without requiring the user to manually create or submit
intermediate JSON messages:

1. Requirement Analyzer
2. Resource Recommender
3. AIApplicationAutomationAgent

The existing structured Common JSON v1.0 endpoints remain available as
advanced test interfaces. The new automatic flow becomes the primary web and
API workflow.

## 2. Scope

### In scope

- Accept either a natural-language request or a structured App Spec.
- Normalize both input modes into one internal request.
- Generate an `ApplicationProfile`.
- Compare the profile with a local mock resource catalog.
- Generate a `ResourceRecommendation`.
- Authorize `AIApplicationAutomationAgent` through Agent Registry.
- Decide `DEPLOY`, `REJECT`, or `RETRY`.
- Run deterministic Go Guard checks.
- Return a platform-neutral `DesiredDeploymentSpec`.
- Store every stage under one `run_id` and `correlation_id`.
- Show stage progress and generated evidence in the existing web UI.

### Out of scope

- Provisioning or deleting a cloud VM.
- Calling AppDeploy or changing the AppDeployer branch.
- Producing an Innogrid-specific Manifest.
- Executing scale, restart, rollback, or stop commands.
- Implementing a general multi-Agent workflow engine.

## 3. Primary User Flow

```text
One user request
  -> Requirement Analyzer
  -> ApplicationProfile
  -> Resource Recommender
  -> ResourceRecommendation
  -> Agent Registry authorization
  -> AIApplicationAutomationAgent
  -> DEPLOY / REJECT / RETRY
  -> Go Guard
  -> DesiredDeploymentSpec
```

The user presses one `Analyze and Decide` command. Backend orchestration owns
the entire sequence. The browser does not coordinate individual stage APIs.

## 4. Input Contract

### Natural-language input

```json
{
  "input_type": "natural_language",
  "request": "Deploy a Qwen inference service with 4 CPU cores, 16 GiB memory, one GPU, and 20 GiB storage.",
  "requested_by": "geon-web"
}
```

### Structured App Spec input

```json
{
  "input_type": "structured",
  "requested_by": "geon-web",
  "app_spec": {
    "app_id": "qwen-service",
    "app_version": "1.0.0",
    "workload_type": "LLM_INFERENCE",
    "cpu_cores": 4,
    "memory_mib": 16384,
    "storage_gib": 20,
    "accelerator_type": "GPU",
    "accelerator_count": 1,
    "accelerator_memory_mib": 16384,
    "replicas_min": 1,
    "replicas_max": 2
  }
}
```

Exactly one of `request` and `app_spec` is accepted according to
`input_type`.

## 5. Component Design

### 5.1 Requirement Analyzer

The analyzer exposes one internal interface:

```go
type RequirementAnalyzer interface {
    Analyze(context.Context, AutomationRunInput) (ApplicationProfile, AnalysisEvidence, error)
}
```

Structured input is deterministically mapped to `ApplicationProfile`.
Natural-language input uses the configured Qwen provider when available. A
bounded local rule analyzer supports reproducible Korean and English demo
requests containing explicit CPU, memory, GPU, storage, and replica values.
The response records which analyzer executed:

- `qwen`
- `local_rule`
- `structured`

The analyzer must not invent credentials, VM IDs, provider IDs, or shell
commands.

### 5.2 Resource Recommender

The recommender exposes:

```go
type ResourceRecommender interface {
    Recommend(context.Context, ApplicationProfile) (ResourceRecommendation, RecommendationEvidence, error)
}
```

The first PoC implementation reads a versioned local mock resource catalog.
It filters candidates by required CPU, memory, storage, accelerator count,
accelerator memory, and replica constraints. Feasible candidates are ranked by
resource fit, SLO headroom, cost efficiency, and availability.

When no candidate satisfies the profile, the recommender still returns valid
evidence. The automation Agent converts this condition to `RETRY`, requesting
another recommendation rather than fabricating a candidate.

The interface is deliberately platform-neutral so a future remote KHU or
Innogrid recommendation client can replace the mock implementation.

### 5.3 AIApplicationAutomationAgent

The existing Agent Control service remains authoritative for:

- Agent Registry authorization
- `DEPLOY`, `REJECT`, and `RETRY`
- correction requests
- Go Guard checks
- flow persistence

The orchestrator feeds the generated `ApplicationProfile` and
`ResourceRecommendation` into the existing service in process. It does not
duplicate the decision rules.

### 5.4 Desired Deployment Spec

`DesiredDeploymentSpec` becomes an explicit core model instead of a web alias
for `DeploymentManifest`.

It may contain:

- application identity and version
- target runtime class such as VM
- CPU, memory, storage, accelerator, and replica requirements
- inference runtime configuration
- policy hints that do not identify a real VM
- decision and profile trace identifiers

It must not contain:

- actual VM ID
- cloud provider credential or secret
- AppDeploy Target Profile ID
- Innogrid-specific fields
- executable infrastructure commands

Adapters may convert this spec into platform-specific requests later.

## 6. API Design

```text
POST /api/v1/agent-control/automation-runs
GET  /api/v1/agent-control/automation-runs/{run_id}
```

The POST response contains:

```json
{
  "run_id": "run-...",
  "correlation_id": "flow-...",
  "status": "COMPLETED",
  "stages": {
    "requirement_analysis": {},
    "resource_recommendation": {},
    "agent_authorization": {},
    "deployment_decision": {},
    "guard": {}
  },
  "desired_deployment_spec": {}
}
```

For domain outcomes, the endpoint returns a completed run containing
`DEPLOY`, `REJECT`, or `RETRY`. Malformed requests return HTTP 400. Provider
failure falls back to the local rule analyzer only when the request can be
parsed safely; otherwise the run ends with an explicit analysis error.

## 7. Web Design

The existing three primary views remain:

1. Automation Agent
2. Agent and Policy
3. Experiment Results

The Automation Agent view changes to:

- default input mode: natural language
- alternative input mode: structured App Spec
- one `Analyze and Decide` button
- one progress sequence:
  `Requirements -> Recommendation -> Agent Decision`
- summarized final decision and Desired Deployment Spec
- collapsible advanced evidence for generated `ApplicationProfile`,
  `ResourceRecommendation`, authorization, and Guard

The current two raw Common JSON editors remain only under an advanced testing
section.

Agent and Policy continues to manage authorization. Experiment Results reads
the same stored run and displays reasoning comparison and post-deployment
feedback without becoming part of the three-stage pre-deployment flow.

## 8. Traceability and State

One generated `run_id` owns one `correlation_id`. Every intermediate message,
decision, Guard result, and final spec uses these identifiers. Runs are stored
in the current in-memory PoC store and are cleared on process restart.

No browser-side ID copying is required.

## 9. Error Handling

- Invalid structured input: `REJECT` with field-level correction evidence.
- Natural language missing required values: explicit analysis error or
  deterministic default only when the default is documented in evidence.
- No feasible resource: `RETRY` with resource recommendation correction.
- Agent disabled or unauthorized: Agent authorization rejection.
- Guard failure: no Desired Deployment Spec.
- Qwen unavailable: local-rule fallback is labeled and never reported as Qwen
  execution.

API responses use stable error codes and do not expose raw internal errors.

## 10. Verification

Required automated cases:

- natural-language request completes all three stages
- structured App Spec completes all three stages
- GPU request selects a feasible GPU mock candidate
- CPU request does not select a GPU-only candidate unnecessarily
- invalid requirements result in `REJECT`
- no feasible candidate results in `RETRY`
- disabled Agent prevents decision generation
- Guard rejection suppresses Desired Deployment Spec
- Qwen-unavailable fallback is labeled `local_rule`
- all intermediate objects share the same correlation identifiers
- existing Common JSON endpoints remain compatible
- desktop and mobile web flows require one user command

Repository verification:

```bash
make test
make vet
```

## 11. Success Criteria

The feature is complete when a user can enter one natural-language request or
one structured App Spec, press one button, and observe Requirement Analyzer,
Resource Recommender, and AIApplicationAutomationAgent finish automatically
with a traceable `DEPLOY`, `REJECT`, or `RETRY` result and, when approved, a
platform-neutral `DesiredDeploymentSpec`.
