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
    stages: [{ name: "user_request", status: "approved", reason: "accepted", started_at: "2026-07-28T00:00:00Z" }],
    logs: [],
  };
}

async function browserPage() {
  const assets = Object.fromEntries(await Promise.all([
    "index.html",
    "app.css",
    "manifest_stages.js",
    "app.js",
  ].map(async (name) => [name, await fs.readFile(path.join(staticDir, name), "utf8")])));
  let runs = [
    controlRun("run-deployed", "DEPLOYED", "dep-001", "PlannerAgent"),
    controlRun("run-approved", "MANIFEST_APPROVED", "", "SafetyAgent"),
  ];
  const agents = [
    { name: "PlannerAgent", source: "internal", role: "planner", capabilities: ["plan"], bounded_actions: [], enabled: true },
    { name: "SafetyAgent", source: "internal", role: "guard", capabilities: ["validate"], bounded_actions: [], enabled: true },
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
  const page = await browser.newPage();
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
        appdeploy_configured: true,
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
  return { browser, page };
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
      plannerSelected: "SafetyAgent",
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
    assert.deepEqual(initial.entries, ["user_request", "execution_feedback", "observe"]);
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
