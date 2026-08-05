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
  agents: [],
};

function completedRun(input) {
  const selectedAgent = input.decision_agent || "AIApplicationAutomationAgent";
  const selectedSource = selectedAgent === "RuntimeDeploymentAgent" ? "runtime" : "configuration";
  const desiredDeploymentSpec = {
    spec_version: "1.0",
    decision_id: "decision-web-001",
    application: { app_id: "demo-app", app_version: "1.0.0" },
    target_runtime: "GPU_VM",
    desired_infrastructure: {
      node_count: 1,
      cpu_cores_per_node: 4,
      memory_mib_per_node: 16384,
      storage_gib_per_node: 50,
      accelerator: { type: "GPU", count: 1, memory_mib_min_per_device: 24576 },
    },
    inference_configuration: { runtime_engine: "VLLM", precision: "FP16", replicas: 1 },
    metadata: { correlation_id: "flow-web-001", trace_id: "trace-web-001" },
  };
  return {
    run_id: "run-web-001",
    correlation_id: "flow-web-001",
    trace_id: "trace-web-001",
    status: "COMPLETED",
    input,
    requirement_analysis: {
      mode: input.input_type === "structured" ? "structured" : "local_rule",
      evidence: {
        mode: input.input_type === "structured" ? "structured" : "local_rule",
        assumptions: input.input_type === "structured" ? [] : ["replicas defaulted to 1"],
      },
      application_profile: {
        profile_id: "profile-web-001",
        app_id: input.app_spec?.app_id || "demo-app",
        app_version: input.app_spec?.app_version || "1.0.0",
        requirements: {
          compute: { cpu_cores_min: 4, memory_mib_min: 8192, storage_gib_min: 20 },
          accelerator: { required: true, type: "GPU", count_min: 1 },
          deployment: { replicas_min: 1, replicas_max: 1 },
        },
      },
      model_recommendation: {
        selected_model: { model_id: "qwen", model_version: "local", source: "OLLAMA" },
        inference_configuration: { runtime_engine: "VLLM", precision: "FP16", replicas: 1 },
      },
    },
    resource_recommendation: {
      evidence: { catalog_version: "mock-v1", candidate_count: 4, feasible_count: 2, mode: "mock_catalog" },
      resource_recommendation: {
        recommendation_id: "recommendation-web-001",
        profile_id: "profile-web-001",
        status: "FOUND",
        selected_candidate_id: "mock-gpu-l4",
        candidates: [],
      },
    },
    flow: {
      correlation_id: "flow-web-001",
      trace_id: "trace-web-001",
      state: "DEPLOY_APPROVED",
      agent_authorization: {
        agent_name: selectedAgent,
        capability: "ai_application_automation",
        action: "generate_deployment_decision",
        authorized: true,
        reason: "allowed by registry",
      },
      agent_execution: {
        agent_name: selectedAgent,
        source: selectedSource,
        status: "completed",
        latency_ms: 17,
        request_guard: { status: "APPROVED", checks: [] },
        result_guard: { status: "APPROVED", checks: [] },
        decision: {
          action: "DEPLOY",
          reason: "requirements and recommendation are compatible",
          selected_candidate_id: "mock-gpu-l4",
        },
      },
      decision: {
        action: "DEPLOY",
        reason: "requirements and recommendation are compatible",
        selected_candidate_id: "mock-gpu-l4",
      },
      guard: { status: "APPROVED", checks: [] },
      desired_deployment_spec: desiredDeploymentSpec,
    },
    desired_deployment_spec: desiredDeploymentSpec,
    deployment_submission: {
      adapter: "mock",
      status: "SIMULATED",
      simulated: true,
      request_id: "deploy-request-web-001",
      submitted_at: "2026-07-29T00:00:01Z",
    },
    created_at: "2026-07-29T00:00:00Z",
    updated_at: "2026-07-29T00:00:01Z",
  };
}

async function browserPage(viewport = { width: 1280, height: 900 }) {
  const assets = Object.fromEntries(await Promise.all([
    "index.html",
    "app.css",
    "app.js",
  ].map(async (name) => [name, await fs.readFile(path.join(staticDir, name), "utf8")])));
  const requests = [];
  const feedbackRequests = [];
  const flows = [];
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
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
      await route.fulfill({ contentType: "text/javascript", body: "window.lucide={createIcons(){}};" });
      return;
    }
    if (pathname === "/healthz") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }
    if (pathname === "/api/v1/agents") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(agentsPayload) });
      return;
    }
    if (pathname === "/api/v1/agent-control/flows") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ flows }) });
      return;
    }
    if (pathname === "/api/v1/agent-control/automation-runs" && request.method() === "POST") {
      const input = request.postDataJSON();
      requests.push(input);
      const run = completedRun(input);
      flows.splice(0, flows.length, run.flow);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(run),
      });
      return;
    }
    if (pathname === "/api/v1/agent-control/deployment-status" && request.method() === "POST") {
      const deploymentStatus = request.postDataJSON();
      flows[0] = {
        ...flows[0],
        deployment_status: deploymentStatus,
        feedback_summary: {
          deployment_id: "deployment-web-001",
          decision_id: "decision-web-001",
          deployment_state: "RUNNING",
          success: true,
          cause: "The application is running.",
        },
      };
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(flows[0]),
      });
      return;
    }
    if (pathname === "/api/v1/agent-control/optimization-feedback" && request.method() === "POST") {
      const feedback = request.postDataJSON();
      feedbackRequests.push({ url: request.url(), body: feedback });
      flows[0] = {
        ...flows[0],
        requested_operation_agent: url.searchParams.get("operation_agent"),
        optimization_feedback: feedback,
        feedback_summary: {
          deployment_id: "deployment-web-001",
          decision_id: "decision-web-001",
          deployment_state: "RUNNING",
          outcome: "SUCCEEDED",
          success: true,
          cause: "SLO latency target exceeded.",
          slo_violations: ["latency_p95_ms"],
        },
        operation_agent_execution: {
          agent_name: "OperationOptimizationAgent",
          source: "configuration",
          status: "completed",
          request_guard: { status: "APPROVED", checks: [] },
          result_guard: { status: "APPROVED", checks: [] },
          scaling_guard: { status: "APPROVED", checks: [] },
          decision: {
            action: "SCALE_OUT",
            current_replicas: 1,
            desired_replicas: 2,
            reason: "SLO latency target exceeded.",
            evidence: ["latency_p95_ms"],
          },
        },
        scaling_decision: {
          action: "SCALE_OUT",
          current_replicas: 1,
          desired_replicas: 2,
          reason: "SLO latency target exceeded.",
          evidence: ["latency_p95_ms"],
        },
      };
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(flows[0]),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ message: `unexpected ${request.method()} ${pathname}` }),
    });
  });
  await page.goto("http://automation.test/");
  return { browser, page, requests, feedbackRequests, consoleErrors };
}

test("experiment guide starts collapsed and can be opened without horizontal overflow", async () => {
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, consoleErrors } = await browserPage(viewport);
    try {
      const guide = page.locator("#experiment-guide");
      assert.equal(await guide.getAttribute("open"), null);
      assert.equal(await page.locator(".experiment-guide-steps").isVisible(), false);

      await page.locator("#experiment-guide > summary").click();

      assert.equal(await page.locator(".experiment-guide-steps").isVisible(), true);
      assert.equal(await page.locator(".experiment-guide-step").count(), 4);
      const layout = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      assert.equal(layout.scrollWidth <= layout.clientWidth, true, JSON.stringify({ viewport, layout }));
      assert.deepEqual(consoleErrors, []);
    } finally {
      await browser.close();
    }
  }
});

test("one natural-language input automatically completes all four stages", async () => {
  const { browser, page, requests, consoleErrors } = await browserPage();
  try {
    assert.equal(await page.locator("#automation-input-mode-natural").isChecked(), true);
    assert.equal(await page.locator("#automation-request").isVisible(), true);
    assert.equal(await page.locator("#automation-app-spec-json").isVisible(), false);
    await page.locator("#decision-agent-select").selectOption("RuntimeDeploymentAgent");

    await page.locator("#automation-request").fill(
      "GPU 1개, CPU 4코어, 메모리 8GiB로 AI 추론 서비스를 배포해 주세요.",
    );
    await page.locator("#automation-run-submit").click();
    await page.waitForFunction(() => (
      document.getElementById("agent-control-action").textContent === "DEPLOY"
    ));

    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0], {
      input_type: "natural_language",
      request: "GPU 1개, CPU 4코어, 메모리 8GiB로 AI 추론 서비스를 배포해 주세요.",
      requested_by: "geon-web",
      decision_agent: "RuntimeDeploymentAgent",
    });
    assert.deepEqual(
      await page.locator("#agent-control-stage-flow > li").evaluateAll((items) => (
        items.map((item) => [item.dataset.agentControlStage, item.classList.contains("is-complete")])
      )),
      [
        ["requirement", true],
        ["recommendation", true],
        ["decision", true],
        ["adapter", true],
      ],
    );
    assert.equal(await page.locator("#automation-analysis-mode").textContent(), "local_rule");
    assert.equal(await page.locator("#agent-control-candidate").textContent(), "mock-gpu-l4");
    assert.equal(await page.locator("#agent-control-authorization").textContent(), "승인");
    assert.equal(await page.locator("#agent-control-agent-name").textContent(), "RuntimeDeploymentAgent");
    assert.equal(await page.locator("#agent-control-agent-source").textContent(), "runtime");
    assert.equal(await page.locator("#agent-control-request-guard").textContent(), "APPROVED");
    assert.equal(await page.locator("#agent-control-result-guard").textContent(), "APPROVED");
    assert.equal(await page.locator("#agent-control-guard").textContent(), "APPROVED");
    assert.equal(await page.locator("#agent-control-adapter").textContent(), "mock");
    assert.equal(
      await page.locator("#agent-control-adapter-status").textContent(),
      "SIMULATED",
    );
    assert.match(await page.locator("#agent-control-result-json").textContent(), /desired_infrastructure/);
    assert.match(await page.locator("#agent-control-result-json").textContent(), /deployment_submission/);
    assert.match(await page.locator("#automation-application-profile-json").textContent(), /profile-web-001/);
    assert.match(await page.locator("#automation-resource-recommendation-json").textContent(), /mock-gpu-l4/);
    await page.locator('[data-view-target="results"]').click();
    assert.match(await page.locator("#experiment-agent-summary").textContent(), /RuntimeDeploymentAgent/);
    assert.match(await page.locator("#experiment-flow-json").textContent(), /agent_execution/);
    assert.deepEqual(consoleErrors, []);
  } finally {
    await browser.close();
  }
});

test("structured App Spec mode uses the same one-command endpoint without horizontal overflow", async () => {
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, requests, consoleErrors } = await browserPage(viewport);
    try {
      await page.locator("#automation-input-mode-structured").check();
      assert.equal(await page.locator("#automation-request").isVisible(), false);
      assert.equal(await page.locator("#automation-app-spec-json").isVisible(), true);
      const appSpec = {
        app_id: "structured-demo",
        app_version: "1.0.0",
        workload_type: "LLM_INFERENCE",
        cpu_cores: 4,
        memory_mib: 8192,
        storage_gib: 20,
        accelerator_type: "GPU",
        accelerator_count: 1,
        accelerator_memory_mib: 16384,
        replicas_min: 1,
        replicas_max: 1,
      };
      await page.locator("#automation-app-spec-json").fill(JSON.stringify(appSpec, null, 2));
      await page.locator("#automation-run-submit").click();
      await page.waitForFunction(() => (
        document.getElementById("automation-analysis-mode").textContent === "structured"
      ));

      assert.equal(requests.length, 1);
      assert.deepEqual(requests[0], {
        input_type: "structured",
        requested_by: "geon-web",
        decision_agent: "AIApplicationAutomationAgent",
        app_spec: appSpec,
      });
      const layout = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      assert.equal(layout.scrollWidth <= layout.clientWidth, true, JSON.stringify({ viewport, layout }));
      assert.deepEqual(consoleErrors, []);
    } finally {
      await browser.close();
    }
  }
});

test("optimization feedback selects an operation Agent and renders guarded scaling evidence", async () => {
  const { browser, page, feedbackRequests, consoleErrors } = await browserPage();
  try {
    await page.locator("#automation-run-submit").click();
    await page.waitForFunction(() => (
      document.getElementById("agent-control-action").textContent === "DEPLOY"
    ));
    await page.locator('[data-view-target="results"]').click();
    await page.locator("#load-agent-control-feedback-sample").click();

    assert.equal(
      await page.locator("#optimization-operation-agent-select").inputValue(),
      "OperationOptimizationAgent",
    );
    await page.locator("#deployment-status-form button[type=submit]").click();
    await page.waitForFunction(() => (
      document.getElementById("experiment-scaling-summary").textContent.includes("Feedback")
    ));
    assert.equal(await page.locator("#experiment-operation-evidence").isVisible(), false);
    assert.doesNotMatch(await page.locator("#experiment-scaling-summary").textContent(), /KEEP|SCALE_/);

    await page.locator("#optimization-feedback-form button[type=submit]").click();
    await page.waitForFunction(() => (
      document.getElementById("experiment-operation-scaling").textContent.includes("SCALE_OUT 1 -> 2")
    ));

    assert.equal(feedbackRequests.length, 1);
    assert.equal(
      new URL(feedbackRequests[0].url).searchParams.get("operation_agent"),
      "OperationOptimizationAgent",
    );
    assert.equal(feedbackRequests[0].body.operation_agent, undefined);
    assert.match(await page.locator("#experiment-operation-agent").textContent(), /OperationOptimizationAgent.*configuration.*completed/);
    assert.equal(await page.locator("#experiment-operation-request-guard").textContent(), "APPROVED");
    assert.equal(await page.locator("#experiment-operation-result-guard").textContent(), "APPROVED");
    assert.equal(await page.locator("#experiment-operation-scaling-guard").textContent(), "APPROVED");
    assert.equal(await page.locator("#experiment-operation-scaling").textContent(), "SCALE_OUT 1 -> 2");
    assert.equal(await page.locator("#experiment-operation-reason").textContent(), "SLO latency target exceeded.");
    assert.equal(await page.locator("#experiment-operation-evidence-list").textContent(), "latency_p95_ms");
    assert.match(await page.locator("#experiment-flow-json").textContent(), /operation_agent_execution/);
    assert.deepEqual(consoleErrors, []);
  } finally {
    await browser.close();
  }
});
