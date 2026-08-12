"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const test = require("node:test");
const { chromium } = require("playwright");

const staticDir = path.join(__dirname, "static");

const agentsPayload = {
  defaults: {
    ai_application_automation: "AIApplicationAutomationAgent",
    ai_application_operation_optimization: "OperationOptimizationAgent",
  },
  eligible_decision_agents: [
    {
      name: "AIApplicationAutomationAgent",
      source: "configuration",
      capabilities: ["ai_application_automation"],
      bounded_actions: ["generate_deployment_decision"],
      enabled: true,
    },
    {
      name: "RuntimeDeploymentAgent",
      source: "runtime",
      capabilities: ["ai_application_automation"],
      bounded_actions: ["generate_deployment_decision"],
      enabled: true,
    },
  ],
  eligible_operation_agents: [
    {
      name: "OperationOptimizationAgent",
      source: "configuration",
      capabilities: ["ai_application_operation_optimization"],
      bounded_actions: ["generate_scaling_decision"],
      enabled: true,
    },
  ],
  agents: [
    {
      name: "AIApplicationAutomationAgent",
      source: "configuration",
      role: "deployment_decision",
      capabilities: ["ai_application_automation"],
      bounded_actions: ["generate_deployment_decision"],
      enabled: true,
    },
    {
      name: "OperationOptimizationAgent",
      source: "configuration",
      role: "operation_optimization",
      capabilities: ["ai_application_operation_optimization"],
      bounded_actions: ["generate_scaling_decision"],
      enabled: true,
    },
    {
      name: "RuntimeDeploymentAgent",
      source: "runtime",
      role: "deployment_decision",
      capabilities: ["ai_application_automation"],
      bounded_actions: ["generate_deployment_decision"],
      enabled: true,
    },
  ],
};

function currentFlow({
  correlationID,
  updatedAt,
  agentName = "AIApplicationAutomationAgent",
  source = "configuration",
  action = "DEPLOY",
  candidateID = "mock-gpu-l4",
  scalingAction,
} = {}) {
  const decisionID = `decision-${correlationID}`;
  const flow = {
    correlation_id: correlationID,
    trace_id: `trace-${correlationID}`,
    automation_run_id: `run-${correlationID}`,
    profile_id: `profile-${correlationID}`,
    state: action === "DEPLOY" ? "DEPLOY_APPROVED" : "DEPLOY_REJECTED",
    requested_decision_agent: agentName,
    agent_authorization: {
      agent_name: agentName,
      capability: "ai_application_automation",
      action: "generate_deployment_decision",
      authorized: true,
      reason: "allowed by registry",
    },
    agent_execution: {
      agent_name: agentName,
      source,
      status: "completed",
      latency_ms: 18,
      request_guard: { status: "APPROVED", checks: [] },
      result_guard: { status: "APPROVED", checks: [] },
      decision: {
        action,
        reason: "requirements and recommendation are compatible",
        selected_candidate_id: candidateID,
      },
    },
    decision: {
      decision_id: decisionID,
      action,
      reason: "requirements and recommendation are compatible",
      selected_candidate_id: candidateID,
    },
    guard: { status: "APPROVED", checks: [] },
    desired_deployment_spec: {
      spec_version: "1.0",
      decision_id: decisionID,
      application: { app_id: "demo-app", app_version: "1.0.0" },
      target_runtime: "GPU_VM",
      desired_infrastructure: {
        node_count: 1,
        cpu_cores_per_node: 4,
        memory_mib_per_node: 8192,
        storage_gib_per_node: 20,
        accelerator: { type: "GPU", count: 1 },
      },
      inference_configuration: {
        runtime_engine: "VLLM",
        precision: "FP16",
        replicas: 1,
      },
      metadata: {
        correlation_id: correlationID,
        trace_id: `trace-${correlationID}`,
      },
    },
    updated_at: updatedAt,
  };

  if (scalingAction) {
    flow.requested_operation_agent = "OperationOptimizationAgent";
    flow.operation_agent_execution = {
      agent_name: "OperationOptimizationAgent",
      source: "configuration",
      status: "completed",
      request_guard: { status: "APPROVED", checks: [] },
      result_guard: { status: "APPROVED", checks: [] },
      scaling_guard: { status: "APPROVED", checks: [] },
    };
    flow.scaling_decision = {
      action: scalingAction,
      current_replicas: 1,
      desired_replicas: scalingAction === "SCALE_OUT" ? 2 : 1,
      reason: "approved policy projection",
      evidence: ["latency_p95_ms", "accelerator_average_percent"],
    };
  }

  return flow;
}

function currentAutomationRun(input) {
  const flow = currentFlow({
    correlationID: "flow-stable-001",
    updatedAt: "2026-08-05T02:00:00Z",
    agentName: input.decision_agent,
    source: input.decision_agent === "RuntimeDeploymentAgent" ? "runtime" : "configuration",
  });
  return {
    run_id: "run-stable-001",
    correlation_id: flow.correlation_id,
    trace_id: flow.trace_id,
    status: "COMPLETED",
    input,
    requirement_analysis: {
      mode: "local_rule",
      evidence: { mode: "local_rule", assumptions: ["replicas defaulted to 1"] },
      application_profile: {
        profile_id: flow.profile_id,
        app_id: "demo-app",
        app_version: "1.0.0",
      },
    },
    resource_recommendation: {
      evidence: { mode: "mock_catalog", candidate_count: 4, feasible_count: 2 },
      resource_recommendation: {
        recommendation_id: "recommendation-stable-001",
        profile_id: flow.profile_id,
        status: "FOUND",
        selected_candidate_id: "mock-gpu-l4",
        candidates: [],
      },
    },
    flow,
    desired_deployment_spec: flow.desired_deployment_spec,
    deployment_submission: {
      adapter: "mock",
      status: "SIMULATED",
      simulated: true,
      request_id: "deploy-request-stable-001",
      submitted_at: "2026-08-05T02:00:00Z",
    },
    created_at: "2026-08-05T02:00:00Z",
    updated_at: "2026-08-05T02:00:00Z",
  };
}

async function browserPage({
  viewport = { width: 1280, height: 900 },
  initialFlows = [],
} = {}) {
  const assets = Object.fromEntries(await Promise.all([
    "index.html",
    "app.css",
    "app.js",
  ].map(async (name) => [name, await fs.readFile(path.join(staticDir, name), "utf8")])));
  const flows = [...initialFlows];
  const requests = [];
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.addInitScript(() => {
    window.confirm = () => true;
    window.scrollTo = () => {};
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();
    const asset = {
      "/": ["text/html; charset=utf-8", assets["index.html"]],
      "/assets/app.css": ["text/css; charset=utf-8", assets["app.css"]],
      "/assets/app.js": ["text/javascript; charset=utf-8", assets["app.js"]],
    }[pathname];
    if (asset) {
      await route.fulfill({ contentType: asset[0], body: asset[1] });
      return;
    }
    if (url.hostname === "unpkg.com") {
      await route.fulfill({
        contentType: "text/javascript; charset=utf-8",
        body: "window.lucide={createIcons(){}};",
      });
      return;
    }
    if (pathname === "/healthz") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      });
      return;
    }
    if (pathname === "/api/v1/agents" && method === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(agentsPayload),
      });
      return;
    }
    if (pathname === "/api/v1/agent-control/flows" && method === "GET") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ flows }),
      });
      return;
    }
    if (pathname === "/api/v1/agent-control/automation-runs" && method === "POST") {
      const input = request.postDataJSON();
      requests.push({ method, pathname, body: input });
      const run = currentAutomationRun(input);
      const existing = flows.findIndex(
        (flow) => flow.correlation_id === run.flow.correlation_id,
      );
      if (existing >= 0) flows[existing] = run.flow;
      else flows.push(run.flow);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(run),
      });
      return;
    }
    if (pathname === "/api/v1/agent-control/deployment-status" && method === "POST") {
      const deploymentStatus = request.postDataJSON();
      const selectedIndex = flows.findIndex(
        (flow) => flow.correlation_id === deploymentStatus.correlation_id,
      );
      if (selectedIndex < 0) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ message: "flow not found" }),
        });
        return;
      }
      flows[selectedIndex] = {
        ...flows[selectedIndex],
        deployment_status: deploymentStatus,
      };
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(flows[selectedIndex]),
      });
      return;
    }
    if (pathname === "/api/v1/agent-control/optimization-feedback" && method === "POST") {
      const feedback = request.postDataJSON();
      const selectedIndex = flows.findIndex(
        (flow) => flow.correlation_id === feedback.correlation_id,
      );
      if (selectedIndex < 0) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ message: "flow not found" }),
        });
        return;
      }
      const operationAgent = url.searchParams.get("operation_agent") || "OperationOptimizationAgent";
      flows[selectedIndex] = {
        ...flows[selectedIndex],
        requested_operation_agent: operationAgent,
        optimization_feedback: feedback,
        operation_agent_execution: {
          agent_name: operationAgent,
          source: "configuration",
          status: "completed",
          request_guard: { status: "APPROVED", checks: [] },
          result_guard: { status: "APPROVED", checks: [] },
          scaling_guard: { status: "APPROVED", checks: [] },
        },
        scaling_decision: {
          action: "SCALE_OUT",
          current_replicas: 1,
          desired_replicas: 2,
          reason: "approved policy projection",
          evidence: ["latency_p95_ms"],
        },
      };
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(flows[selectedIndex]),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ message: `unexpected ${method} ${pathname}` }),
    });
  });
  await page.goto("http://control-run.test/");
  await page.waitForFunction((expectedFlowCount) => (
    document.getElementById("decision-agent-select").value === "AIApplicationAutomationAgent"
      && document.querySelectorAll("#experiment-flow-list .flow-list-item").length === expectedFlowCount
  ), initialFlows.length);
  return { browser, page, requests, consoleErrors };
}

async function layoutSnapshot(page, groups) {
  return page.evaluate((selectors) => {
    const visibleBoxes = (selector) => Array.from(document.querySelectorAll(selector))
      .filter((element) => element.checkVisibility())
      .map((element) => {
        const { left, right, top, bottom } = element.getBoundingClientRect();
        return { left, right, top, bottom };
      });
    const overlaps = (items) => items.some((item, index) => (
      items.slice(index + 1).some((other) => (
        item.left < other.right
          && item.right > other.left
          && item.top < other.bottom
          && item.bottom > other.top
      ))
    ));
    return {
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      groups: Object.fromEntries(Object.entries(selectors).map(([name, selector]) => {
        const boxes = visibleBoxes(selector);
        return [name, { count: boxes.length, overlaps: overlaps(boxes) }];
      })),
    };
  }, groups);
}

function assertLayout(snapshot, expectedGroupCounts) {
  assert.equal(
    snapshot.scrollWidth <= snapshot.clientWidth,
    true,
    JSON.stringify(snapshot),
  );
  for (const [name, minimumCount] of Object.entries(expectedGroupCounts)) {
    assert.equal(snapshot.groups[name].count >= minimumCount, true, JSON.stringify(snapshot));
    assert.equal(snapshot.groups[name].overlaps, false, JSON.stringify(snapshot));
  }
}

test("current navigation, Agent policy, and layout replace the removed legacy menu contract", async () => {
  const initialFlows = [
    currentFlow({
      correlationID: "flow-layout-001",
      updatedAt: "2026-08-05T01:00:00Z",
      scalingAction: "KEEP",
    }),
  ];
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, consoleErrors } = await browserPage({ viewport, initialFlows });
    try {
      assert.deepEqual(
        await page.locator(".primary-nav > [data-view-target]").evaluateAll((items) => (
          items.map((item) => ({
            target: item.dataset.viewTarget,
            label: item.textContent.trim(),
          }))
        )),
        [
          { target: "agent-control", label: "자동화 실행" },
          { target: "agents", label: "Agent 및 정책" },
          { target: "results", label: "실험 결과" },
        ],
      );
      assert.equal(await page.locator('[data-view-target="overview"]').count(), 0);
      assert.equal(await page.locator('[data-view-target="feedback"]').count(), 0);
      assert.equal(await page.locator('[data-view-target="autonomy"]').count(), 0);
      assert.equal(await page.locator("#input-mode-upload, #input-mode-existing").count(), 0);
      assert.equal(await page.locator("[data-execute-agent]").count(), 0);

      const automationLayout = await layoutSnapshot(page, {
        navItems: ".primary-nav > .nav-item",
        inputModes: ".input-mode-switch > label",
        decisionPicker: ".decision-agent-picker > *",
      });
      assertLayout(automationLayout, { navItems: 3, inputModes: 2, decisionPicker: 2 });

      await page.locator('[data-view-target="agents"]').click();
      assert.equal(await page.locator("#core-policy-summary .policy-row").count(), 2);
      assert.equal(await page.locator("#core-default-agent").textContent(), "AIApplicationAutomationAgent");
      assert.equal(await page.locator("#core-operation-agent").textContent(), "OperationOptimizationAgent");
      assert.equal(await page.locator("#agent-table-body > tr").count(), 3);
      assert.match(await page.locator("#agent-table-body").textContent(), /ai_application_automation/);
      assert.match(await page.locator("#agent-table-body").textContent(), /generate_scaling_decision/);
      assert.match(await page.locator("#agent-table-body").textContent(), /runtime/);
      const policyLayout = await layoutSnapshot(page, {
        navItems: ".primary-nav > .nav-item",
        policyRows: "#core-policy-summary .policy-row",
      });
      assertLayout(policyLayout, { navItems: 3, policyRows: 2 });

      await page.locator('[data-view-target="results"]').click();
      const resultsLayout = await layoutSnapshot(page, {
        navItems: ".primary-nav > .nav-item",
        flowControls: ".flow-list-item > *",
      });
      assertLayout(resultsLayout, { navItems: 3, flowControls: 2 });
      assert.deepEqual(consoleErrors, []);
    } finally {
      await browser.close();
    }
  }
});

test("replayed one-shot automation results project one stable ControlRun Flow", async () => {
  const { browser, page, requests, consoleErrors } = await browserPage();
  try {
    await page.locator("#automation-request").fill("Deploy the current GPU inference service.");
    await page.locator("#decision-agent-select").selectOption("RuntimeDeploymentAgent");

    const firstRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === "/api/v1/agent-control/automation-runs"
    ));
    await page.locator("#automation-run-submit").click();
    await firstRequest;
    await page.waitForFunction(() => (
      document.getElementById("agent-control-flow-id").textContent === "run-stable-001"
    ));

    await page.locator('[data-view-target="agent-control"]').click();
    const secondRequest = page.waitForRequest((request) => (
      new URL(request.url()).pathname === "/api/v1/agent-control/automation-runs"
    ));
    await page.locator("#automation-run-submit").click();
    await secondRequest;
    await page.waitForFunction(() => (
      document.querySelectorAll("#experiment-flow-list .flow-list-item").length === 1
    ));

    assert.equal(requests.length, 2);
    assert.deepEqual(requests[0], requests[1]);
    assert.deepEqual(requests[0], {
      method: "POST",
      pathname: "/api/v1/agent-control/automation-runs",
      body: {
        input_type: "natural_language",
        request: "Deploy the current GPU inference service.",
        requested_by: "geon-web",
        decision_agent: "RuntimeDeploymentAgent",
      },
    });
    assert.equal(await page.locator("#agent-control-agent-name").textContent(), "RuntimeDeploymentAgent");
    assert.equal(await page.locator("#agent-control-agent-source").textContent(), "runtime");
    assert.equal(await page.locator("#agent-control-action").textContent(), "DEPLOY");
    assert.equal(await page.locator("#agent-control-adapter-status").textContent(), "SIMULATED");

    await page.locator('[data-view-target="results"]').click();
    assert.equal(await page.locator("#experiment-flow-list .flow-list-item").count(), 1);
    assert.equal(
      await page.locator("#experiment-flow-list .flow-list-item.is-active .flow-select").getAttribute("data-flow-id"),
      "flow-stable-001",
    );
    const rawFlow = JSON.parse(await page.locator("#experiment-flow-json").textContent());
    assert.equal(rawFlow.correlation_id, "flow-stable-001");
    assert.equal(rawFlow.automation_run_id, "run-flow-stable-001");
    assert.deepEqual(consoleErrors, []);
  } finally {
    await browser.close();
  }
});

test("results select the newest Flow and keep active evidence and Feedback samples synchronized", async () => {
  const older = currentFlow({
    correlationID: "flow-older-001",
    updatedAt: "2026-08-05T01:00:00Z",
    agentName: "AIApplicationAutomationAgent",
    source: "configuration",
    scalingAction: "KEEP",
  });
  const newer = currentFlow({
    correlationID: "flow-newer-002",
    updatedAt: "2026-08-05T03:00:00Z",
    agentName: "RuntimeDeploymentAgent",
    source: "runtime",
    scalingAction: "SCALE_OUT",
  });
  const { browser, page, consoleErrors } = await browserPage({
    initialFlows: [older, newer],
  });
  try {
    await page.locator('[data-view-target="results"]').click();
    assert.deepEqual(
      await page.locator("#experiment-flow-list .flow-select").evaluateAll((items) => (
        items.map((item) => item.dataset.flowId)
      )),
      ["flow-newer-002", "flow-older-001"],
    );
    assert.equal(
      await page.locator("#experiment-flow-list .flow-list-item.is-active .flow-select").getAttribute("data-flow-id"),
      "flow-newer-002",
    );
    assert.match(await page.locator("#experiment-agent-summary").textContent(), /RuntimeDeploymentAgent.*runtime.*completed/);
    assert.match(await page.locator("#experiment-scaling-summary").textContent(), /SCALE_OUT/);

    await page.locator('.flow-select[data-flow-id="flow-older-001"]').click();
    await page.waitForFunction(() => (
      document.getElementById("experiment-agent-summary").textContent.includes("AIApplicationAutomationAgent")
    ));
    assert.equal(
      await page.locator("#experiment-flow-list .flow-list-item.is-active .flow-select").getAttribute("data-flow-id"),
      "flow-older-001",
    );
    assert.match(await page.locator("#experiment-scaling-summary").textContent(), /KEEP/);
    const rawFlow = JSON.parse(await page.locator("#experiment-flow-json").textContent());
    assert.equal(rawFlow.correlation_id, "flow-older-001");
    assert.equal(rawFlow.requested_operation_agent, "OperationOptimizationAgent");

    await page.locator("#load-agent-control-feedback-sample").click();
    const statusSample = JSON.parse(await page.locator("#deployment-status-json").inputValue());
    const feedbackSample = JSON.parse(await page.locator("#optimization-feedback-json").inputValue());
    assert.equal(statusSample.correlation_id, "flow-older-001");
    assert.equal(statusSample.data.deployment_status.decision_id, "decision-flow-older-001");
    assert.equal(feedbackSample.correlation_id, "flow-older-001");
    assert.equal(feedbackSample.data.optimization_feedback.decision_id, "decision-flow-older-001");
    assert.deepEqual(consoleErrors, []);
  } finally {
    await browser.close();
  }
});
