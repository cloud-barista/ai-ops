# Agent Control Experiment Guide Design

## Goal

Make the geon Agent Control workflow understandable from the web UI without
changing geon's responsibility boundary. The Overview must show the experiment
sequence, required and optional stages, each stage's output, and direct
navigation to the corresponding view.

## Navigation Order

The sidebar and mobile navigation use the same research workflow order:

1. Overview
2. Agents & Guard
3. Deployment Planner
4. Post-deployment Autonomy
5. Feedback

The AppDeploy link remains an external top-bar action because AppDeploy is not a
geon view.

## Overview Experiment Guide

Add one full-width `EXPERIMENT GUIDE` panel above the existing system metrics.
It contains five ordered steps:

1. **Agent Registry check**
   - Verify the enabled Planner Agent, capability, and bounded actions.
   - Navigate to `Agents & Guard`.
2. **Generate Manifest**
   - Enter the natural-language request, `app_version_id`, Qwen candidate, and
     optional Target hint.
   - Navigate to `Deployment Planner`.
3. **Review Guard evidence**
   - Confirm Request Guard, Agent Registry, Qwen Planner, and Manifest Guard
     stages under one `run_id`.
   - The expected core result is `MANIFEST_APPROVED` plus a
     `DeploymentManifest`.
4. **Submit to AppDeploy (optional)**
   - Submit only an approved Manifest.
   - AppDeploy owns Target and Runtime Adapter selection and returns
     `deployment_id`, status, and logs.
   - Navigate to `Deployment Planner`.
5. **Post-deployment experiment (optional)**
   - Use only a `DEPLOYED` ControlRun for Autonomous Loop and correlate
     operational Action Proposal and Feedback by `run_id`.
   - Navigate to Post-deployment Autonomy or Feedback.

The guide explicitly marks steps 1-3 as the geon Manifest experiment, step 4 as
optional external deployment, and step 5 as optional post-deployment research.
It must not imply that Autonomous Loop is required to generate a Manifest.

## Function Summary

The guide includes a concise role summary:

| View | Responsibility | Primary output |
| --- | --- | --- |
| Overview | Service state, recent ControlRuns, ordered stage evidence | Run status and timeline |
| Agents & Guard | Agent profiles, capabilities, bounded Actions, Action Guard | approved/rejected authority evidence |
| Deployment Planner | Request Guard, Registry resolution, Qwen planning, Manifest Guard | validated `DeploymentManifest` |
| Post-deployment Autonomy | Optional SLO observation and guarded operational control | Autonomy events and Action Proposal |
| Feedback | Correlation-based execution result recording | Feedback record linked to `run_id` |

`DeploymentManifest` and operational `Action Proposal` remain separate
contracts.

## Interaction and Layout

- Every guide step is a button with an icon, order number, required/optional
  label, short description, and expected result.
- Clicking a step switches to the related geon view through the existing
  `data-view-target` mechanism.
- No new route, modal, onboarding wizard, or persistent dismissal state is
  introduced.
- Desktop uses a five-column ordered track.
- Tablet uses two columns and mobile uses one column.
- Text wraps naturally; no horizontal scrolling or fixed viewport-scaled type.
- Existing ControlRun, Planner, Registry, Autonomy, and Feedback behavior is
  unchanged.

## Testing

- Embedded UI contract tests verify the navigation order, guide controls,
  required/optional labels, and expected outcome terms.
- Existing web and API tests must remain green.
- Browser verification covers desktop and 390-pixel mobile viewports, direct
  guide navigation, no horizontal overflow, and no console errors.

## Scope Boundaries

- Do not modify the AppDeploy repository.
- Do not add external Agent endpoint invocation.
- Do not make Autonomous Loop a Manifest-generation stage.
- Do not modify or stage `config/inference_optimization.json`.
