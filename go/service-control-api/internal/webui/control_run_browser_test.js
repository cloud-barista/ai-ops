"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const test = require("node:test");
const { chromium } = require("playwright");
const { MANIFEST_STAGE_ORDER } = require("./static/manifest_stages.js");

const staticDir = path.join(__dirname, "static");

function controlRun(runID, status, deploymentID, agentName, overrides = {}) {
  const run = {
    run_id: runID,
    status,
    updated_at: "2026-07-28T00:00:00Z",
    request: { app_version_id: "app-001" },
    selected_agent: { name: agentName },
    generation: { actual_model: "qwen", guard_valid: true, latency_ms: 1 },
    deployment: deploymentID ? { deployment_id: deploymentID, target_profile_id: "target-001" } : {},
    manifest: {
      schema_version: "deployment.khu.ai/v1alpha1",
      kind: "DeploymentManifest",
      spec: { target_profile_id: "target-001" },
    },
    stages: [
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
  return { ...run, ...overrides };
}

function uploadedControlRun() {
  return controlRun("run-uploaded", "MANIFEST_APPROVED", "", "AIApplicationAutomationAgent", {
    request: {
      natural_language_request: "업로드한 앱을 CPU 환경에 배포해 주세요.",
      app_version_id: "appver-issued",
      candidate_id: "qwen3.5-ops-planner",
    },
    application: {
      package: {
        package_type: "script",
        artifact_uri: "file:///packages/demo.tar.gz",
        archive_name: "demo.tar.gz",
        checksum: "sha256:test-package",
      },
      registration: {
        app_id: "app-issued",
        app_version_id: "appver-issued",
        name: "demo-app",
        version: "1.0.0",
      },
    },
    partial_result: {
      artifact_uri: "file:///packages/demo.tar.gz",
      checksum: "sha256:test-package",
      app_id: "app-issued",
      app_version_id: "appver-issued",
    },
    stages: [
      "app_upload",
      "package_build",
      "app_registration",
      "request_guard",
      "agent_registry",
      "agent_dispatch",
      "qwen_planner",
      "manifest_guard",
    ].map((name, index) => ({
      name,
      status: "approved",
      reason: "accepted",
      started_at: `2026-07-27T00:00:${String(index).padStart(2, "0")}Z`,
    })),
  });
}

function requestRejectedControlRun() {
  return controlRun("run-request-rejected", "REQUEST_REJECTED", "", "AIApplicationAutomationAgent", {
    request: {
      natural_language_request: "허용되지 않은 범위의 앱을 배포해 주세요.",
      app_version_id: "appver-rejected",
      candidate_id: "qwen3.5-ops-planner",
    },
    stages: [{
      name: "request_guard",
      status: "rejected",
      reason: "request scope is not allowed",
      started_at: "2026-07-27T00:00:00Z",
    }],
  });
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
    controlRun("run-appdeploy-failed", "APPDEPLOY_FAILED", "", "AIApplicationAutomationAgent"),
    requestRejectedControlRun(),
  ];
  const requests = [];
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
    if (pathname === "/api/v1/control-runs/from-package" && method === "POST") {
      requests.push({
        method,
        pathname,
        headers: await request.allHeaders(),
        body: (await request.postDataBuffer())?.toString("utf8") || "",
      });
      const run = uploadedControlRun();
      runs = [run, ...runs.filter((entry) => entry.run_id !== run.run_id)];
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(run) });
      return;
    }
    if (pathname === "/api/v1/control-runs" && method === "POST") {
      const body = request.postDataJSON();
      requests.push({ method, pathname, headers: await request.allHeaders(), body });
      const run = controlRun("run-existing", "MANIFEST_APPROVED", "", "AIApplicationAutomationAgent", {
        request: body,
      });
      runs = [run, ...runs.filter((entry) => entry.run_id !== run.run_id)];
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(run) });
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
  return { browser, page, consoleErrors, requests };
}

async function assertNoLayoutOverlap(page) {
  const overlaps = await page.evaluate(() => {
    const groups = [
      "#manifest-stage-flow > *",
      "#automatic-feedback-list > *",
      "#autonomy-timeline > *",
      ".form-actions > button",
      ".input-mode-options > label",
      "#planner-form [data-input-mode-section]:not([hidden]) > .field",
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

async function assertNoHorizontalOverflow(page, viewport) {
  const horizontalLayout = await page.evaluate(() => {
    const clientWidth = document.documentElement.clientWidth;
    return {
      clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      overflowing: [...document.querySelectorAll("body *")]
        .filter((element) => {
          const style = window.getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return style.display !== "none" && rect.width > 0 && rect.right > clientWidth + 1;
        })
        .slice(0, 8)
        .map((element) => ({
          selector: element.id ? `#${element.id}` : element.className,
          right: Math.round(element.getBoundingClientRect().right),
          width: Math.round(element.getBoundingClientRect().width),
        })),
    };
  });
  assert.equal(
    horizontalLayout.scrollWidth <= horizontalLayout.clientWidth,
    true,
    JSON.stringify({ viewport, horizontalLayout }),
  );
}

async function assertApplicationWorkflowDOM(page) {
  const missing = await page.evaluate(() => [
    "input-mode-upload",
    "input-mode-existing",
    "application-source",
    "app-name",
    "application-package-result",
    "application-registration-result",
  ].filter((id) => !document.getElementById(id)));
  assert.deepEqual(missing, [], `missing application workflow DOM: ${missing.join(", ")}`);
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

test("upload mode is default and existing-App mode preserves the JSON ControlRun contract", async () => {
  const { browser, page, requests } = await browserPage();
  try {
    await assertApplicationWorkflowDOM(page);
    const initial = await page.evaluate(() => ({
      uploadChecked: document.getElementById("input-mode-upload").checked,
      existingChecked: document.getElementById("input-mode-existing").checked,
      uploadHidden: document.querySelector("[data-input-mode-section='upload']").hidden,
      existingHidden: document.querySelector("[data-input-mode-section='existing']").hidden,
      sourceRequired: document.getElementById("application-source").required,
      appVersionRequired: document.querySelector("[name='app_version_id']").required,
      appVersionDisabled: document.querySelector("[name='app_version_id']").disabled,
    }));
    assert.deepEqual(initial, {
      uploadChecked: true,
      existingChecked: false,
      uploadHidden: false,
      existingHidden: true,
      sourceRequired: true,
      appVersionRequired: false,
      appVersionDisabled: true,
    });

    await page.locator("#input-mode-existing").check();
    const existing = await page.evaluate(() => ({
      uploadHidden: document.querySelector("[data-input-mode-section='upload']").hidden,
      existingHidden: document.querySelector("[data-input-mode-section='existing']").hidden,
      sourceRequired: document.getElementById("application-source").required,
      sourceDisabled: document.getElementById("application-source").disabled,
      appVersionRequired: document.querySelector("[name='app_version_id']").required,
      appVersionDisabled: document.querySelector("[name='app_version_id']").disabled,
    }));
    assert.deepEqual(existing, {
      uploadHidden: true,
      existingHidden: false,
      sourceRequired: false,
      sourceDisabled: true,
      appVersionRequired: true,
      appVersionDisabled: false,
    });

    await page.locator("[name='app_version_id']").fill("appver-existing");
    await page.locator("#planner-form button[type='submit']").click();
    await page.waitForFunction(() => state.activeRunID === "run-existing");

    const request = requests.find((entry) => entry.pathname === "/api/v1/control-runs" && entry.method === "POST");
    assert.deepEqual(request.body, {
      natural_language_request: "Mock 환경에서 CPU 1, 메모리 1Gi, GPU 0, 스토리지 1Gi로 테스트 앱을 배포해 주세요.",
      app_version_id: "appver-existing",
      candidate_id: "qwen3.5-ops-planner",
      requested_by: "ai-ops-geon-planner",
      agent_name: "AIApplicationAutomationAgent",
    });
  } finally {
    await browser.close();
  }
});

test("upload mode sends browser-owned multipart data and renders issued application evidence", async () => {
  const { browser, page, requests } = await browserPage();
  try {
    await assertApplicationWorkflowDOM(page);
    await page.locator("#application-source").setInputFiles({
      name: "demo-app.zip",
      mimeType: "application/zip",
      buffer: Buffer.from("package-source"),
    });
    await page.locator("#app-name").fill("demo-app");
    await page.locator("#planner-form button[type='submit']").click();
    await page.waitForFunction(() => state.activeRunID === "run-uploaded");

    const request = requests.find((entry) => entry.pathname === "/api/v1/control-runs/from-package");
    assert.ok(request, "expected multipart ControlRun request");
    assert.match(request.headers["content-type"], /^multipart\/form-data; boundary=/);
    assert.doesNotMatch(request.headers["content-type"], /application\/json/);
    assert.match(request.body, /name="source"; filename="demo-app.zip"/);
    assert.match(request.body, /name="package_type"\r\n\r\nscript/);
    assert.match(request.body, /name="app_name"\r\n\r\ndemo-app/);
    assert.doesNotMatch(request.body, /name="app_version_id"/);

    const result = await page.evaluate(() => ({
      packageHidden: document.getElementById("application-package-result").hidden,
      registrationHidden: document.getElementById("application-registration-result").hidden,
      packageURI: document.getElementById("planner-package-uri").textContent,
      checksum: document.getElementById("planner-package-checksum").textContent,
      appID: document.getElementById("planner-app-id").textContent,
      appVersionID: document.getElementById("planner-app-version-id").textContent,
      rememberedAppVersionID: localStorage.getItem("geon-agent-control-app-version-id"),
      stageLabels: [...document.querySelectorAll("#manifest-stage-flow .manifest-stage strong")].map((entry) => entry.textContent),
      submitDisabled: document.getElementById("planner-submit").disabled,
    }));
    assert.deepEqual(result, {
      packageHidden: false,
      registrationHidden: false,
      packageURI: "file:///packages/demo.tar.gz",
      checksum: "sha256:test-package",
      appID: "app-issued",
      appVersionID: "appver-issued",
      rememberedAppVersionID: "appver-issued",
      stageLabels: MANIFEST_STAGE_ORDER.map((stage) => stage.label),
      submitDisabled: false,
    });
    assert.ok(result.stageLabels.indexOf("앱 등록") < result.stageLabels.indexOf("사용자 요청"));
    const userRequestProjection = await page.evaluate(() => {
      const stage = [...document.querySelectorAll("#manifest-stage-flow .manifest-stage")]
        .find((entry) => entry.querySelector("strong").textContent === "사용자 요청");
      return {
        backendStages: state.lastPlannerRun.stages.map((entry) => entry.name),
        status: stage.dataset.status,
        reason: stage.querySelector("small").textContent,
      };
    });
    assert.deepEqual(userRequestProjection, {
      backendStages: [
        "app_upload",
        "package_build",
        "app_registration",
        "request_guard",
        "agent_registry",
        "agent_dispatch",
        "qwen_planner",
        "manifest_guard",
      ],
      status: "approved",
      reason: "요청 접수 완료",
    });
    assert.doesNotMatch(
      await page.locator("[aria-labelledby='planner-result-title']").textContent(),
      /demo-app\.zip|package-source/,
    );
  } finally {
    await browser.close();
  }
});

test("existing-App stages are skipped and AppDeploy submit requires MANIFEST_APPROVED", async () => {
  const { browser, page } = await browserPage();
  try {
    assert.equal(await page.locator("#manifest-stage-flow .manifest-stage").count(), 9);
    assert.deepEqual(
      await page.locator("#manifest-stage-flow .manifest-stage").evaluateAll((entries) => (
        entries.slice(0, 3).map((entry) => entry.dataset.status)
      )),
      ["skipped", "skipped", "skipped"],
    );
    const approvedUserRequest = page.locator("#manifest-stage-flow .manifest-stage").nth(3);
    assert.equal(await approvedUserRequest.getAttribute("data-status"), "approved");
    assert.equal(await approvedUserRequest.locator("small").textContent(), "요청 접수 완료");
    assert.equal(await page.locator("#planner-submit").isDisabled(), true);

    await page.locator("[data-select-run='run-request-rejected']").dispatchEvent("click");
    await page.waitForFunction(() => state.activeRunID === "run-request-rejected");
    const rejectedProjection = await page.locator("#manifest-stage-flow .manifest-stage").evaluateAll((entries) => (
      entries.map((entry) => ({
        label: entry.querySelector("strong").textContent,
        status: entry.dataset.status,
        reason: entry.querySelector("small").textContent,
      }))
    ));
    assert.deepEqual(rejectedProjection.slice(0, 5), [
      { label: "앱 업로드", status: "skipped", reason: "기존 앱 사용" },
      { label: "패키지 생성", status: "skipped", reason: "기존 앱 사용" },
      { label: "앱 등록", status: "skipped", reason: "기존 앱 사용" },
      { label: "사용자 요청", status: "approved", reason: "요청 접수 완료" },
      { label: "Request Guard", status: "rejected", reason: "request scope is not allowed" },
    ]);

    await page.locator("[data-select-run='run-appdeploy-failed']").dispatchEvent("click");
    await page.waitForFunction(() => state.activeRunID === "run-appdeploy-failed");
    assert.equal(await page.locator("#planner-submit").isDisabled(), true);

    await page.locator("[data-select-run='run-approved']").dispatchEvent("click");
    await page.waitForFunction(() => state.activeRunID === "run-approved");
    assert.equal(await page.locator("#planner-submit").isEnabled(), true);
  } finally {
    await browser.close();
  }
});

test("application workflow controls do not overlap on desktop or mobile", async () => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, consoleErrors } = await browserPage({ viewport });
    try {
      await assertApplicationWorkflowDOM(page);
      await page.locator("#application-source").setInputFiles({
        name: "very-long-application-package-name-that-must-not-resize-controls.zip",
        mimeType: "application/zip",
        buffer: Buffer.from("package-source"),
      });
      await page.locator("#app-name").fill("responsive-demo");
      await page.locator("#planner-form button[type='submit']").click();
      await page.waitForFunction(() => (
        state.activeRunID === "run-uploaded" &&
        document.getElementById("application-registration-result").hidden === false
      ));
      assert.equal(await page.locator(".input-mode-options > label").count(), 2);
      await assertNoHorizontalOverflow(page, viewport);
      await assertNoLayoutOverlap(page);
      assert.deepEqual(consoleErrors, []);

      if (process.env.TASK5_SCREENSHOT_DIR) {
        await page.locator("#toast-region .toast").waitFor({ state: "detached", timeout: 6000 });
        await page.evaluate(() => {
          document.documentElement.scrollTop = 0;
          document.body.scrollTop = 0;
        });
        await fs.mkdir(process.env.TASK5_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({
          path: path.join(process.env.TASK5_SCREENSHOT_DIR, `task-5-${viewport.width}x${viewport.height}.png`),
          fullPage: true,
        });
      }
    } finally {
      await browser.close();
    }
  }
});

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
    assert.deepEqual(initial.entries, ["request_guard", "agent_registry", "agent_dispatch", "qwen_planner", "manifest_guard", "execution_feedback", "observe"]);
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
      await page.waitForFunction(() => document.querySelectorAll("#manifest-stage-flow .manifest-stage").length === 9);

      const manifest = await page.evaluate(() => ({
        labels: [...document.querySelectorAll("#manifest-stage-flow .manifest-stage strong")].map((entry) => entry.textContent),
        result: JSON.parse(document.getElementById("planner-json").textContent),
        deploymentID: document.querySelector("#autonomy-form [name='deployment_id']").value,
        postDeployment: document.getElementById("post-deployment-readiness").dataset.status,
        selectedAgent: document.querySelector("#agent-table-body tr[data-selected-agent='true'] strong")?.textContent,
      }));
      assert.deepEqual(manifest.labels, MANIFEST_STAGE_ORDER.map((stage) => stage.label));
      assert.equal(manifest.result.manifest.kind, "DeploymentManifest");
      assert.equal(manifest.result.manifest.spec.target_profile_id, "target-001");
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
        return projection.run?.run_id === "run-approved" && projection.entries?.length >= 5 && projection.executor_feedback?.length === 1;
      });
      const feedbackProjection = await page.evaluate(() => JSON.parse(document.getElementById("automatic-feedback-json").textContent));
      assert.deepEqual(feedbackProjection.entries.slice(0, 5).map((entry) => entry.stage), [
        "request_guard", "agent_registry", "agent_dispatch", "qwen_planner", "manifest_guard",
      ]);
      assert.equal(feedbackProjection.executor_feedback.length, 1);
      await assertNoHorizontalOverflow(page, viewport);
      await assertNoLayoutOverlap(page);
      assert.deepEqual(consoleErrors, []);
    } finally {
      await browser.close();
    }
  }
});
