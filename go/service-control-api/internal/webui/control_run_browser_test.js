"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const test = require("node:test");
const { chromium } = require("playwright");

const staticDir = path.join(__dirname, "static");

function controlRun(runID, status, deploymentID, agentName) {
  return {
    run_id: runID,
    status,
    updated_at: "2026-07-28T00:00:00Z",
    request: { app_version_id: "app-001" },
    selected_agent: { name: agentName },
    generation: { actual_model: "qwen", guard_valid: true, latency_ms: 1 },
    deployment: deploymentID ? { deployment_id: deploymentID, target_profile_id: "target-001" } : {},
    manifest: { spec: { target_profile_id: "target-001" } },
    stages: [
      "user_request",
      "request_guard",
      "agent_registry",
      "agent_dispatch",
      "qwen_planner",
      "manifest_guard",
    ].map((name, index) => ({
      name,
      status: "approved",
      reason: "accepted",
      started_at: `2026-07-27T00:00:0${index}Z`,
    })),
    logs: [],
  };
}

async function browserPage({ appdeployConfigured = true, viewport } = {}) {
  const assets = Object.fromEntries(await Promise.all([
    "index.html",
    "app.css",
    "manifest_stages.js",
    "app.js",
  ].map(async (name) => [name, await fs.readFile(path.join(staticDir, name), "utf8")])));
  let runs = [
    controlRun("run-deployed", "DEPLOYED", "dep-001", "PlannerAgent"),
    controlRun("run-approved", "MANIFEST_APPROVED", "", "AIApplicationAutomationAgent"),
  ];
  const agents = [
    { name: "PlannerAgent", source: "internal", role: "planner", capabilities: ["plan"], bounded_actions: [], enabled: true },
    { name: "AIApplicationAutomationAgent", source: "internal", role: "planner", capabilities: ["deployment_manifest_planning"], bounded_actions: ["generate_deployment_manifest"], enabled: true },
  ];
  let feedback = [
    { correlation_id: "feedback-deployed", run_id: "run-deployed", executor: "AppDeployExecutorAgent", status: "succeeded", message: "deployment completed", received_at: "2026-07-28T00:01:00Z" },
    { correlation_id: "feedback-other", run_id: "run-approved", executor: "AppDeployExecutorAgent", status: "failed", message: "other run only", received_at: "2026-07-28T00:02:00Z" },
  ];
  let autonomyEvents = [
    { sequence: 1, run_id: "run-deployed", stage: "observe", status: "completed", reason: "healthy", timestamp: "2026-07-28T00:03:00Z" },
    { sequence: 2, run_id: "run-approved", stage: "observe", status: "failed", reason: "other run only", timestamp: "2026-07-28T00:04:00Z" },
  ];

  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.addInitScript(() => {
    localStorage.setItem("geon-agent-control-active-run-id", "missing-run");
    window.confirm = () => true;
    window.scrollTo = () => {};
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();
    if (url.hostname === "unpkg.com" && pathname === "/lucide@0.468.0/dist/umd/lucide.min.js") {
      await route.fulfill({ contentType: "text/javascript; charset=utf-8", body: "window.lucide = { createIcons() {} };" });
      return;
    }
    const asset = {
      "/": ["text/html; charset=utf-8", assets["index.html"]],
      "/assets/app.css": ["text/css; charset=utf-8", assets["app.css"]],
      "/assets/manifest_stages.js": ["text/javascript; charset=utf-8", assets["manifest_stages.js"]],
      "/assets/app.js": ["text/javascript; charset=utf-8", assets["app.js"]],
    }[pathname];
    if (asset) {
      await route.fulfill({ contentType: asset[0], body: asset[1] });
      return;
    }
    if (pathname === "/healthz") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }
    if (pathname === "/api/v1/agents") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ agents }) });
      return;
    }
    if (pathname === "/api/v1/control-runs" && method === "GET") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ runs }) });
      return;
    }
    if (pathname === "/api/v1/control-runs" && method === "DELETE") {
      const deletedCount = runs.length;
      runs = [];
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ deleted_count: deletedCount }) });
      return;
    }
    if (pathname === "/api/v1/autonomy/status") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({
        running: false,
        appdeploy_configured: appdeployConfigured,
        state: {},
        config: { mode: "monitor_only", slo: {} },
      }) });
      return;
    }
    if (pathname === "/api/v1/autonomy/events") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ events: autonomyEvents }) });
      return;
    }
    if (pathname === "/api/v1/automation/feedback" && method === "GET") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ feedback }) });
      return;
    }
    if (pathname === "/api/v1/agents/AIApplicationAutomationAgent/execute" && method === "POST") {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          valid: false,
          message: "Agent result was rejected",
          reason: "Agent result run_id does not match the dispatched run",
          run_id: "run-rejected",
          result_guard: { valid: false, status: "rejected", reason: "Agent result run_id does not match the dispatched run" },
        }),
      });
      return;
    }
    const match = pathname.match(/^\/api\/v1\/control-runs\/([^/]+)$/);
    if (match) {
      const runID = decodeURIComponent(match[1]);
      const run = runs.find((entry) => entry.run_id === runID);
      if (method === "GET" && run) {
        await route.fulfill({ contentType: "application/json", body: JSON.stringify(run) });
        return;
      }
      if (method === "DELETE" && run) {
        runs = runs.filter((entry) => entry.run_id !== runID);
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
        return;
      }
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ message: `unexpected ${method} ${pathname}` }) });
  });
  await page.goto("http://webui.test/");
  await page.waitForFunction(() => document.getElementById("refresh-button").disabled === false);
  return { browser, page, consoleErrors };
}

async function assertNoLayoutOverlap(page) {
  const overlaps = await page.evaluate(() => {
    const groups = [
      "#manifest-stage-flow > *",
      "#automatic-feedback-list > *",
      "#autonomy-timeline > *",
      ".form-actions > button",
    ];
    const intersect = (left, right) => left.left < right.right && right.left < left.right && left.top < right.bottom && right.top < left.bottom;
    return groups.flatMap((selector) => {
      const elements = [...document.querySelectorAll(selector)].filter((element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      });
      const collisions = [];
      for (let index = 0; index < elements.length; index += 1) {
        for (let next = index + 1; next < elements.length; next += 1) {
          if (intersect(elements[index].getBoundingClientRect(), elements[next].getBoundingClientRect())) {
            collisions.push(selector);
          }
        }
      }
      return collisions;
    });
  });
  assert.deepEqual(overlaps, []);
}

async function postDeploymentState(page) {
  return page.evaluate(() => ({
    activeRunID: state.activeRunID,
    rememberedRunID: localStorage.getItem("geon-agent-control-active-run-id"),
    actionRunID: document.querySelector("#action-form [name='run_id']").value,
    autonomyRunID: document.querySelector("#autonomy-form [name='run_id']").value,
    deploymentID: document.querySelector("#autonomy-form [name='deployment_id']").value,
    readiness: document.getElementById("post-deployment-readiness").dataset.status,
    plannerSelected: document.querySelector("#agent-table-body tr[data-selected-agent='true'] strong")?.textContent,
    selectedLabel: document.querySelector("#agent-table-body .selected-agent-label")?.textContent,
    disabled: Object.fromEntries([
      "autonomy-start",
      "autonomy-stop",
      "autonomy-run-cycle",
      "autonomy-emergency-stop",
      "autonomy-refresh",
      "clear-autonomy-events",
    ].map((id) => [id, document.getElementById(id).disabled])),
    saveDisabled: document.querySelector("#autonomy-form button[type='submit']").disabled,
  }));
}

test("ControlRun selection clears and gates Post-deployment controls through the production browser UI", async () => {
  const { browser, page } = await browserPage();
  try {
    assert.equal(await page.evaluate(() => localStorage.getItem("geon-agent-control-active-run-id")), "run-deployed");
    assert.deepEqual(await postDeploymentState(page), {
      activeRunID: "run-deployed",
      rememberedRunID: "run-deployed",
      actionRunID: "run-deployed",
      autonomyRunID: "run-deployed",
      deploymentID: "dep-001",
      readiness: "ready",
      plannerSelected: "PlannerAgent",
      selectedLabel: "selected by active Run",
      disabled: {
        "autonomy-start": false,
        "autonomy-stop": true,
        "autonomy-run-cycle": false,
        "autonomy-emergency-stop": false,
        "autonomy-refresh": false,
        "clear-autonomy-events": false,
      },
      saveDisabled: false,
    });

    await page.locator(".nav-item[data-view-target='autonomy']").click();
    await page.waitForFunction(() => document.querySelector("[data-view='autonomy']").hidden === false);

    await page.locator("[data-select-run='run-approved']").dispatchEvent("click");
    await page.waitForFunction(() => state.activeRunID === "run-approved");
    assert.deepEqual(await postDeploymentState(page), {
      activeRunID: "run-approved",
      rememberedRunID: "run-approved",
      actionRunID: "",
      autonomyRunID: "",
      deploymentID: "",
      readiness: "blocked",
      plannerSelected: "AIApplicationAutomationAgent",
      selectedLabel: "selected by active Run",
      disabled: {
        "autonomy-start": true,
        "autonomy-stop": true,
        "autonomy-run-cycle": true,
        "autonomy-emergency-stop": true,
        "autonomy-refresh": true,
        "clear-autonomy-events": true,
      },
      saveDisabled: true,
    });

    await page.locator("#autonomy-run-id").selectOption("run-deployed");
    await page.waitForFunction(() => state.activeRunID === "run-deployed");
    await page.locator("#autonomy-run-id").selectOption("");
    await page.waitForFunction(() => state.activeRunID === "");
    assert.deepEqual(await postDeploymentState(page), {
      activeRunID: "",
      rememberedRunID: null,
      actionRunID: "",
      autonomyRunID: "",
      deploymentID: "",
      readiness: "blocked",
      plannerSelected: undefined,
      selectedLabel: undefined,
      disabled: {
        "autonomy-start": true,
        "autonomy-stop": true,
        "autonomy-run-cycle": true,
        "autonomy-emergency-stop": true,
        "autonomy-refresh": true,
        "clear-autonomy-events": true,
      },
      saveDisabled: true,
    });

    await page.locator("#autonomy-run-id").selectOption("run-deployed");
    await page.waitForFunction(() => state.activeRunID === "run-deployed");
    await page.evaluate(() => {
      const select = document.getElementById("autonomy-run-id");
      select.append(new Option("Unknown", "missing-run"));
      select.value = "missing-run";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.waitForFunction(() => state.activeRunID === "");
    const unknownSelection = await postDeploymentState(page);
    assert.equal(unknownSelection.rememberedRunID, null);
    assert.equal(unknownSelection.actionRunID, "");
    assert.equal(unknownSelection.autonomyRunID, "");
    assert.equal(unknownSelection.deploymentID, "");
    assert.equal(unknownSelection.readiness, "blocked");
    assert.equal(unknownSelection.saveDisabled, true);
    assert.equal(Object.values(unknownSelection.disabled).every(Boolean), true);

    await page.locator("#autonomy-run-id").selectOption("run-deployed");
    await page.locator("[data-delete-run='run-deployed']").dispatchEvent("click");
    await page.waitForFunction(() => state.activeRunID === "run-approved");
    assert.equal((await postDeploymentState(page)).readiness, "blocked");

    await page.locator("#clear-history").dispatchEvent("click");
    await page.waitForFunction(() => state.activeRunID === "");
    const cleared = await postDeploymentState(page);
    assert.equal(cleared.readiness, "blocked");
    assert.equal(cleared.deploymentID, "");
    assert.equal(cleared.saveDisabled, true);
  } finally {
    await browser.close();
  }
});

test("Feedback projects only active ControlRun evidence through the production browser UI", async () => {
  const { browser, page } = await browserPage();
  try {
    await page.locator(".nav-item[data-view-target='feedback']").click();
    await page.waitForFunction(() => document.querySelector("[data-view='feedback']").hidden === false);
    await page.waitForFunction(() => {
      const projection = JSON.parse(document.getElementById("automatic-feedback-json").textContent);
      return projection.executor_feedback?.length === 1 && projection.autonomy_events?.length === 1;
    });

    const initial = await page.evaluate(() => ({
      selectedRunID: document.getElementById("feedback-run-id").value,
      summary: document.getElementById("automatic-feedback-summary").textContent,
      entries: [...document.querySelectorAll("#automatic-feedback-list strong")].map((entry) => entry.textContent),
      projection: JSON.parse(document.getElementById("automatic-feedback-json").textContent),
    }));
    assert.equal(initial.selectedRunID, "run-deployed");
    assert.match(initial.summary, /run-deployed/);
    assert.deepEqual(initial.entries, ["user_request", "request_guard", "agent_registry", "agent_dispatch", "qwen_planner", "manifest_guard", "execution_feedback", "observe"]);
    assert.equal(initial.projection.executor_feedback.length, 1);
    assert.equal(initial.projection.autonomy_events.length, 1);
    assert.equal(initial.projection.entries.every((entry) => entry.details.run_id === "run-deployed" || entry.source === "control_run"), true);

    await page.locator("#feedback-run-id").selectOption("run-approved");
    await page.waitForFunction(() => state.activeRunID === "run-approved");
    const selected = await page.evaluate(() => JSON.parse(document.getElementById("automatic-feedback-json").textContent));
    assert.equal(selected.run.run_id, "run-approved");
    assert.deepEqual(selected.executor_feedback.map((record) => record.correlation_id), ["feedback-other"]);
    assert.deepEqual(selected.autonomy_events.map((event) => event.sequence), [2]);
  } finally {
    await browser.close();
  }
});

test("Rejected Agent results display their ControlRun and Guard reason through the production browser UI", async () => {
  const { browser, page } = await browserPage();
  try {
    await page.locator(".nav-item[data-view-target='agents']").click();
    await page.waitForFunction(() => document.querySelector("[data-view='agents']").hidden === false);
    await page.locator("[data-execute-agent='AIApplicationAutomationAgent']").click();
    await page.locator("#agent-execution-dialog").waitFor({ state: "visible" });
    await page.locator("#agent-execution-form button[type='submit']").click();
    await page.waitForFunction(() => document.getElementById("agent-execution-status").textContent === "FAILED");

    const rejected = await page.evaluate(() => ({
      runID: document.getElementById("agent-execution-run-id").textContent,
      result: document.getElementById("agent-execution-result").textContent,
    }));
    assert.equal(rejected.runID, "run-rejected");
    assert.match(rejected.result, /Agent result run_id does not match the dispatched run/);
  } finally {
    await browser.close();
  }
});

test("AppDeploy-unavailable selected ControlRun retains Manifest stages and automatic Feedback on desktop and mobile", async () => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, consoleErrors } = await browserPage({ appdeployConfigured: false, viewport });
    try {
      await page.locator(".nav-item[data-view-target='overview']").click();
      await page.waitForFunction(() => document.querySelector("[data-view='overview']").hidden === false);
      await page.locator("[data-select-run='run-approved']").click();
      await page.waitForFunction(() => state.activeRunID === "run-approved");
      await page.locator(".nav-item[data-view-target='planner']").click();
      await page.waitForFunction(() => document.querySelector("[data-view='planner']").hidden === false);
      await page.waitForFunction(() => document.querySelectorAll("#manifest-stage-flow .manifest-stage").length === 6);

      const manifest = await page.evaluate(() => ({
        labels: [...document.querySelectorAll("#manifest-stage-flow .manifest-stage strong")].map((entry) => entry.textContent),
        deploymentID: document.querySelector("#autonomy-form [name='deployment_id']").value,
        postDeployment: document.getElementById("post-deployment-readiness").dataset.status,
        selectedAgent: document.querySelector("#agent-table-body tr[data-selected-agent='true'] strong")?.textContent,
      }));
      assert.equal(manifest.labels.length, 6);
      assert.equal(manifest.deploymentID, "");
      assert.equal(manifest.postDeployment, "blocked");
      assert.equal(manifest.selectedAgent, "AIApplicationAutomationAgent");

      await page.locator(".nav-item[data-view-target='autonomy']").click();
      await page.waitForFunction(() => document.querySelector("[data-view='autonomy']").hidden === false);
      await page.waitForFunction(() => document.getElementById("autonomy-connection").textContent === "AppDeploy not configured");
      assert.equal(await page.locator("#post-deployment-readiness").getAttribute("data-status"), "blocked");

      await page.locator(".nav-item[data-view-target='feedback']").click();
      await page.waitForFunction(() => document.querySelector("[data-view='feedback']").hidden === false);
      await page.waitForFunction(() => {
        const projection = JSON.parse(document.getElementById("automatic-feedback-json").textContent);
        return projection.run?.run_id === "run-approved" && projection.entries?.length >= 6 && projection.executor_feedback?.length === 1;
      });
      const feedbackProjection = await page.evaluate(() => JSON.parse(document.getElementById("automatic-feedback-json").textContent));
      assert.deepEqual(feedbackProjection.entries.slice(0, 6).map((entry) => entry.stage), [
        "user_request", "request_guard", "agent_registry", "agent_dispatch", "qwen_planner", "manifest_guard",
      ]);
      assert.equal(feedbackProjection.executor_feedback.length, 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true);
      await assertNoLayoutOverlap(page);
      assert.deepEqual(consoleErrors, []);
    } finally {
      await browser.close();
    }
  }
});
