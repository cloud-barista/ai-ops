# Simplified KHU AI Application Automation PoC Web Design

Date: 2026-07-29
Status: Approved direction
Target branch: `geon`

## 1. Purpose

`geon` is an independently runnable research PoC for AI application deployment
and scaling decisions. It is not a replacement for AppDeploy, CB-Tumblebug, or
an AI application management platform.

The PoC must make the Kyung Hee University research contribution explicit:

1. Receive and validate an `ApplicationProfile`.
2. Receive and validate a `ResourceRecommendation`.
3. Authorize the automation Agent through the Agent Registry.
4. Produce a deployment decision: `DEPLOY`, `REJECT`, or `RETRY`.
5. Produce a platform-neutral `DesiredDeploymentSpec` for approved requests.
6. Validate the decision and spec through Go Guard.
7. Evaluate post-deployment feedback and produce a scaling decision such as
   `NO_ACTION`, `SCALE_OUT`, or `SCALE_IN`.

## 2. Problem

The current web UI exposes the core Agent flow together with legacy ControlRun,
standalone Guard experiments, post-deployment policy tools, external Feedback
forms, and platform integration controls.

Although these features are useful as development evidence, exposing them as
equal top-level workflows makes the PoC look like a general-purpose platform.
It also obscures the primary research result: a validated deployment and
scaling decision mechanism.

## 3. Chosen Direction

Use a focused, modular decision-Agent PoC.

- Preserve the Go API and existing compatibility endpoints.
- Remove legacy and platform-oriented tools from the primary web navigation.
- Keep the AI application automation flow as the first screen.
- Make Registry and policy configuration supporting functions.
- Consolidate decision evidence, Guard results, inference comparison, and
  feedback into one experiment-results screen.
- Keep AppDeploy integration optional and outside the core user flow.

This direction was selected over:

- Keeping the current platform-like UI, which preserves breadth but remains
  difficult to explain.
- Removing the web entirely and exposing only API/CLI functions, which is
  technically clean but weak for demonstrations and experiment inspection.

## 4. Responsibility Boundary

### geon owns

- Input contract validation and correlation.
- Agent selection and authorization.
- Deployment feasibility decisions.
- Platform-neutral desired deployment requirements.
- Guard validation and repair/retry reasons.
- Post-deployment scaling decisions.
- Decision evidence and comparison results.

### geon does not own

- Application upload, packaging, registry, or version lifecycle.
- VM creation, deletion, or cloud credential management.
- Final CSP, region, or concrete VM provisioning.
- Platform-specific Manifest conversion.
- Actual deployment, restart, rollback, or scale execution.
- Production monitoring storage and log aggregation.
- General-purpose multi-Agent workflow execution.

## 5. Core Architecture

```text
ApplicationProfile
        +
ResourceRecommendation
        |
        v
Agent Registry authorization
        |
        v
AIApplicationAutomationAgent
        |
        +--> Deployment decision: DEPLOY | REJECT | RETRY
        |
        v
Go Guard validation and repair evidence
        |
        v
DesiredDeploymentSpec
        |
        +--> Optional external deployment executor
        |
        v
Status and Metrics feedback
        |
        v
Scaling decision: NO_ACTION | SCALE_OUT | SCALE_IN
```

The Agent Registry is a policy source, not the starting point of the user
workflow. Go Guard remains outside the Agent and independently validates the
Agent's authorization and output.

## 6. Web Information Architecture

The sidebar contains exactly three primary destinations.

### 6.1 Automation Agent

This is the default and primary screen.

Inputs:

- `ApplicationProfile`
- `ResourceRecommendation`

Processing status:

1. Input correlation and validation
2. Agent Registry authorization
3. Deployment decision
4. Go Guard validation
5. Desired deployment specification generation

Results:

- `DEPLOY`, `REJECT`, or `RETRY`
- Selected resource candidate
- Guard status and reason
- `DesiredDeploymentSpec`
- Correlation and trace identifiers

The screen uses one action button and one result area. It does not expose
AppDeploy controls, legacy ControlRun forms, or standalone Guard experiments.

### 6.2 Agent and Policy

This is a supporting management screen.

Functions:

- List Agent profiles and show their enabled status.
- Register and delete runtime Agent profiles through the existing API.
- Show capabilities and bounded actions.
- Show deployment and scaling policy values.
- Explain which capability and action the core workflow requires.

Standalone LLM Action generation and Guard testing are removed from this
screen. Authorization evidence is produced automatically by the core workflow.
No new Agent update or lifecycle-management API is added for this PoC.

### 6.3 Experiment Results

This screen consolidates research evidence.

Functions:

- List decision runs.
- Inspect input, authorization, decision, Guard result, and output.
- Record or inspect post-deployment status and metrics.
- Show the resulting scaling decision.
- Compare rule-only, Qwen-only, and Qwen-plus-Guard inference modes when
  comparison evidence exists.
- Delete individual or all locally stored experiment records.

External executor callbacks may remain as an API compatibility feature, but no
separate top-level Feedback form is exposed.

## 7. Removed or Consolidated Web Features

Remove from the primary web UI:

- Overview dashboard.
- Legacy ControlRun.
- Standalone Deployment Planner.
- Standalone Agents and Guard action proposal experiment.
- Autonomous Loop as a separate product feature.
- External Feedback input screen.
- Direct AppDeploy submission and deployment controls.

Consolidate:

- Guard evidence into Automation Agent results.
- Scaling evaluation into Experiment Results.
- Feedback into the selected experiment run.
- Guide text into short inline labels and a concise help disclosure.

The underlying compatibility APIs are retained unless a separate cleanup task
proves that an endpoint has no remaining consumer.

## 8. Data and State

Every core execution uses one `correlation_id` and one Flow record.

```json
{
  "correlation_id": "corr-...",
  "state": "DEPLOY_APPROVED",
  "application_profile": {},
  "resource_recommendation": {},
  "agent_authorization": {
    "agent_id": "AIApplicationAutomationAgent",
    "capability": "ai_application_automation",
    "bounded_action": "generate_deployment_decision",
    "status": "approved"
  },
  "decision": {
    "action": "DEPLOY",
    "reason": "requirements and recommended resource are compatible"
  },
  "guard": {
    "status": "APPROVED",
    "checks": []
  },
  "desired_deployment_spec": {},
  "scaling_decision": null
}
```

Post-deployment feedback updates the same Flow record instead of creating a
separate disconnected workflow.

## 9. Error Handling

- Invalid input returns `REJECT` with field-level validation evidence.
- Missing or stale recommendation returns `RETRY`.
- Unauthorized Agent capability or action returns `REJECT`.
- Guard rejection never produces an approved `DesiredDeploymentSpec`.
- LLM failure falls back only when an explicit deterministic policy is
  configured; the result records the actual inference mode.
- Missing external deployment services do not block local decision generation.

## 10. Verification

Backend:

- Unit tests for `DEPLOY`, `REJECT`, and `RETRY`.
- Unit tests for Agent Registry authorization.
- Unit tests for Guard rejection and approved desired specs.
- Unit tests for `NO_ACTION`, `SCALE_OUT`, and `SCALE_IN`.
- API contract tests for correlation and Flow evidence.

Web:

- Exactly three primary navigation items.
- Core workflow completes without visiting another screen.
- No AppDeploy server is required to generate a decision and desired spec.
- Registry changes affect the next core execution.
- Feedback updates the matching experiment run.
- Desktop and mobile layouts have no overlap or horizontal overflow.
- Browser console and network requests have no unexpected errors.

## 11. Acceptance Criteria

The simplified PoC is complete when:

1. A user can submit the two research inputs from the first screen.
2. The system automatically performs Registry authorization and Guard checks.
3. The result clearly returns `DEPLOY`, `REJECT`, or `RETRY`.
4. Approved runs include a platform-neutral `DesiredDeploymentSpec`.
5. Deployment feedback can produce and display a scaling decision.
6. The user can explain the entire PoC using the three web screens.
7. No actual VM or AppDeploy instance is required for the core local experiment.
8. Existing compatibility APIs continue to pass their tests.
