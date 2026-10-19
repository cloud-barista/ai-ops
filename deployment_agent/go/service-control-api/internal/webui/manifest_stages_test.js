"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  BLOCKED_STAGE_REASON,
  PENDING_STAGE_REASON,
  buildManifestStageViewModel,
} = require("./static/manifest_stages.js");

function stageFor(stages, key) {
  return stages.find((stage) => stage.key === key);
}

test("buildManifestStageViewModel preserves selected ControlRun stage evidence", () => {
  const cases = [
    {
      name: "preserves an approved user request and its reason",
      run: {
        stages: [
          { name: "user_request", status: "approved", reason: "request accepted exactly" },
        ],
      },
      key: "user_request",
      status: "approved",
      reason: "request accepted exactly",
    },
    {
      name: "preserves a rejected guard stage and its reason",
      run: {
        stages: [
          { name: "request_guard", status: "rejected", reason: "request guard rejected exactly" },
        ],
      },
      key: "request_guard",
      status: "rejected",
      reason: "request guard rejected exactly",
    },
    {
      name: "preserves a pending stage and its reason",
      run: {
        stages: [
          { name: "agent_registry", status: "pending", reason: "registry evidence pending exactly" },
        ],
      },
      key: "agent_registry",
      status: "pending",
      reason: "registry evidence pending exactly",
    },
    {
      name: "marks missing stages pending without fabricating approval",
      run: { stages: [] },
      key: "user_request",
      status: "pending",
      reason: PENDING_STAGE_REASON,
    },
    {
      name: "blocks later missing stages after an actual rejection",
      run: {
        stages: [
          { name: "request_guard", status: "rejected", reason: "request guard rejected exactly" },
        ],
      },
      key: "qwen_planner",
      status: "blocked",
      reason: BLOCKED_STAGE_REASON,
    },
  ];

  for (const scenario of cases) {
    const stage = stageFor(buildManifestStageViewModel(scenario.run), scenario.key);
    assert.deepEqual(
      { status: stage.status, reason: stage.reason },
      { status: scenario.status, reason: scenario.reason },
      scenario.name,
    );
  }
});
