# Simplified Agent Control Overview

## Goal

Reduce the geon Agent Control Overview to the minimum information needed to start and inspect the core DeploymentManifest experiment.

## Information Hierarchy

The default Overview contains only:

1. Compact service status for geon API, registered Agents, and the Qwen Planner.
2. Two primary workflow commands:
   - `Agent 확인` opens **Agents & Guard**.
   - `Manifest 생성` opens **Deployment Planner**.
3. The recent Manifest ControlRun list.
4. A collapsed `전체 실험 순서 보기` disclosure containing the complete five-step experiment guide.

## Removed From The Default Overview

- The separate `배포 판단 파이프라인` panel because it duplicates the experiment guide.
- The `Agent Snapshot` panel because the Agent count and **Agents & Guard** view already provide this information.
- The selected `ControlRun 단계` timeline because detailed run results are already available in **Deployment Planner**.
- The fourth `최근 Guard` metric because it is not required to begin the core experiment.

These removals affect only the Overview presentation. They do not remove APIs, Agent Registry data, Guard decisions, ControlRun records, Planner results, AppDeploy submission, Autonomy, or Feedback functionality.

## Collapsed Experiment Guide

The existing five workflow steps remain available inside a native collapsed disclosure:

1. Agent Registry check
2. DeploymentManifest generation
3. Optional AppDeploy submission
4. Optional post-deployment autonomy
5. Optional Feedback

Each step remains clickable and opens its corresponding view. Steps 1 and 2 remain visually identified as the core geon Manifest experiment; steps 3 through 5 remain optional.

## Responsive Behavior

- Desktop: three compact status items, two workflow commands, recent runs, then the collapsed guide.
- Mobile: status items and commands stack without horizontal overflow.
- The disclosure remains collapsed by default on all viewport sizes.

## Verification

- A web UI contract test verifies that the simplified Overview contains the two commands, recent ControlRuns, and the collapsed guide.
- The test verifies that the removed duplicate panels are no longer present on the Overview.
- Desktop and 390-pixel mobile browser checks verify layout, disclosure behavior, navigation, and absence of console errors.

