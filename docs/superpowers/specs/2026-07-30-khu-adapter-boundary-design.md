# KHU Core and Adapter Boundary Design

## 1. Goal

Keep the geon prototype independently executable while separating Kyung Hee
University's research logic from mock deployment behavior and future external
platform integration.

The KHU core remains responsible for:

- consuming an `ApplicationProfile` and a `ResourceRecommendation`;
- authorizing `AIApplicationAutomationAgent` through Agent Registry;
- producing `DEPLOY`, `REJECT`, or `RETRY`;
- validating the decision with Go Guard; and
- producing a platform-neutral `DesiredDeploymentSpec`.

Adapters remain responsible for exchanging data with systems outside the KHU
core. They must not move platform ownership into geon.

## 2. Approaches Considered

### 2.1 Documentation-only labels

Rename the current mock behavior in documentation without adding a code
boundary.

This is too weak because tests cannot prove that KHU decision logic remains
independent from deployment behavior.

### 2.2 Minimal typed adapter boundary

Add small Go interfaces around deployment handoff and post-deployment feedback.
Retain an in-process Mock implementation and reuse the existing Common JSON
v1.0 messages for future external integration.

This is the selected approach. It is sufficient for the first-year PoC and
does not introduce a general plugin framework.

### 2.3 General integration or plugin framework

Add dynamic adapter registration, remote discovery, credentials, retries, and
provider-specific clients.

This exceeds the current KHU scope and would duplicate platform work owned by
Innogrid and ETRI.

## 3. Architecture

```text
Natural language request or structured App Spec
                  |
                  v
        RequirementAnalyzer
                  |
                  v
        ApplicationProfile
                  |
                  +---------------------------+
                                              |
Resource information or local mock catalog    |
                  |                           |
                  v                           v
        ResourceRecommender ------> ResourceRecommendation
                                              |
                                              v
                              AIApplicationAutomationAgent
                                              |
                                Agent Registry + Go Guard
                                              |
                                              v
                                  DesiredDeploymentSpec
                                              |
                         +--------------------+--------------------+
                         |                                         |
                         v                                         v
             MockDeploymentAdapter                    HandoffDeploymentAdapter
             PoC result simulation                    Common JSON request output
                         |                                         |
                         +--------------------+--------------------+
                                              |
                                              v
                                  Deployment status and metrics
                                              |
                                              v
                                      Feedback Adapter
                                              |
                                              v
                               NO_ACTION / SCALE_OUT / SCALE_IN
```

The current local analyzer and catalog recommender remain valid Mock inputs.
Their existing interfaces continue to permit future local or remote
implementations without changing the automation Agent.

## 4. Adapter Contracts

### 4.1 Deployment Adapter

```go
type DeploymentAdapter interface {
    Submit(
        context.Context,
        DeploymentCreateRequestEnvelope,
    ) (DeploymentSubmission, error)
}
```

`DeploymentSubmission` records only handoff evidence:

- adapter type;
- submission status;
- external request ID when available;
- whether execution was simulated; and
- a sanitized failure code and message.

The adapter consumes the already validated
`DeploymentCreateRequestEnvelope`. It must not alter the decision,
`DesiredDeploymentSpec`, or Guard result.

### 4.2 Mock Deployment Adapter

The Mock implementation:

- accepts only Guard-approved deployment requests;
- records deterministic simulated submission evidence;
- does not create a VM or execute a shell command;
- does not claim real deployment success; and
- remains available without Innogrid, ETRI, AppDeploy, or cloud credentials.

### 4.3 Handoff Deployment Adapter

The handoff implementation is a boundary, not a full platform client.

For the current PoC it records the Common JSON
`deployment.create.request` as ready for external delivery. A later
implementation may send the same contract to an agreed Innogrid endpoint.
Manifest conversion and actual deployment execution remain outside geon.

### 4.4 Feedback Adapter

The current Common JSON receivers for `deployment.status.changed` and
`optimization.feedback.created` form the feedback boundary.

Mock feedback may be loaded for experiments. Future monitoring feedback may be
received through the same contracts. The KHU core uses the normalized feedback
to produce `NO_ACTION`, `SCALE_OUT`, or `SCALE_IN`, but never creates or deletes
a VM.

## 5. Runtime Selection

The adapter mode is selected by configuration:

```text
AIOPS_DEPLOYMENT_ADAPTER=mock
AIOPS_DEPLOYMENT_ADAPTER=handoff
```

`mock` is the default for a reproducible independent PoC.

`handoff` produces external-delivery evidence without assuming that an
external platform is online. A network-enabled adapter is deferred until the
joint interface is confirmed.

Unknown adapter values fail at startup with a sanitized configuration error.

## 6. Automation Run Evidence

An approved `AutomationRun` may include:

```json
{
  "desired_deployment_spec": {},
  "deployment_submission": {
    "adapter": "mock",
    "status": "SIMULATED",
    "simulated": true,
    "request_id": "deploy-request-..."
  }
}
```

`REJECT` and `RETRY` runs must not call a deployment adapter and must not
contain successful submission evidence.

The `run_id`, `correlation_id`, `trace_id`, decision ID, and request ID remain
linked across analysis, recommendation, decision, Guard, handoff, and
feedback.

## 7. Web Presentation

The primary web flow remains user-request first. It shows:

1. requirement analysis;
2. resource recommendation;
3. Agent Registry authorization;
4. Agent decision and Go Guard;
5. `DesiredDeploymentSpec`; and
6. Adapter handoff evidence.

The UI labels the result explicitly as either `Mock simulation` or `External
handoff ready`. It must not label a Mock submission as a real VM deployment.

## 8. Error Handling

- Invalid or missing Adapter configuration fails during service construction.
- Adapter failures do not rewrite the approved decision or Guard evidence.
- External error bodies and credentials are never returned to the web client.
- Duplicate `application.analysis.request` messages reuse the existing
  idempotency record and must not submit the deployment request twice.
- `REJECT` and `RETRY` stop before Adapter submission.

## 9. Tests

The implementation must prove:

- an approved run invokes the configured Adapter exactly once;
- duplicate protocol requests do not invoke it again;
- `REJECT` and `RETRY` never invoke it;
- Mock evidence is marked `SIMULATED`;
- handoff evidence is marked `READY`;
- Adapter failure is recorded with a sanitized error;
- the existing `DesiredDeploymentSpec` is unchanged by an Adapter;
- Common JSON correlation and trace identifiers are preserved;
- the web differentiates KHU Core, Mock Adapter, and external handoff; and
- all existing Go, OpenAPI, and web tests continue to pass.

## 10. Non-goals

This change does not implement:

- real VM creation or deletion;
- cloud credentials;
- final CSP, region, or VM selection;
- Innogrid Manifest conversion;
- App Registry, version, or lifecycle management;
- production monitoring storage;
- Kubernetes or container deployment;
- a dynamic plugin framework; or
- changes to the AppDeployer branch.

