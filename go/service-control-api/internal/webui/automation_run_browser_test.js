"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const test = require("node:test");
const { chromium } = require("playwright");

const staticDir = path.join(__dirname, "static");

function completedRun(input) {
  const desiredDeploymentSpec = {
    spec_version: "desired-deployment.khu.ai/v1alpha1",
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
        agent_name: "AIApplicationAutomationAgent",
        capability: "ai_application_automation",
        action: "generate_deployment_decision",
        authorized: true,
        reason: "allowed by registry",
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
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ agents: [] }) });
      return;
    }
    if (pathname === "/api/v1/agent-control/flows") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ flows: [] }) });
      return;
    }
    if (pathname === "/api/v1/agent-control/automation-runs" && request.method() === "POST") {
      const input = request.postDataJSON();
      requests.push(input);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(completedRun(input)),
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
  return { browser, page, requests, consoleErrors };
}

test("one natural-language input automatically completes all three stages", async () => {
  const { browser, page, requests, consoleErrors } = await browserPage();
  try {
    assert.equal(await page.locator("#automation-input-mode-natural").isChecked(), true);
    assert.equal(await page.locator("#automation-request").isVisible(), true);
    assert.equal(await page.locator("#automation-app-spec-json").isVisible(), false);

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
    });
    assert.deepEqual(
      await page.locator("#agent-control-stage-flow > li").evaluateAll((items) => (
        items.map((item) => [item.dataset.agentControlStage, item.classList.contains("is-complete")])
      )),
      [["requirement", true], ["recommendation", true], ["decision", true]],
    );
    assert.equal(await page.locator("#automation-analysis-mode").textContent(), "local_rule");
    assert.equal(await page.locator("#agent-control-candidate").textContent(), "mock-gpu-l4");
    assert.equal(await page.locator("#agent-control-authorization").textContent(), "승인");
    assert.equal(await page.locator("#agent-control-guard").textContent(), "APPROVED");
    assert.match(await page.locator("#agent-control-result-json").textContent(), /desired_infrastructure/);
    assert.match(await page.locator("#automation-application-profile-json").textContent(), /profile-web-001/);
    assert.match(await page.locator("#automation-resource-recommendation-json").textContent(), /mock-gpu-l4/);
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
