"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const contract = require("./static/llm_op_demo_contract.js");

const repositoryRoot = path.resolve(__dirname, "../../../..");

test("manual demo materializes eight unique scenarios and every workload success path", () => {
  assert.equal(contract.SCENARIOS.length, 8);
  assert.equal(new Set(contract.SCENARIOS.map((scenario) => scenario.id)).size, 8);

  const successServices = new Set(
    contract.SCENARIOS
      .filter((scenario) => scenario.expected_status === "HANDOFF_READY")
      .map((scenario) => scenario.service_id),
  );
  assert.deepEqual(
    [...successServices].sort(),
    Object.keys(contract.SERVICES).sort(),
  );

  const catalog = JSON.parse(fs.readFileSync(
    path.join(repositoryRoot, "examples/llm-op/user-input-scenarios.json"),
    "utf8",
  ));
  const catalogIDs = new Set(catalog.scenarios.map((scenario) => scenario.id));
  for (const scenario of contract.SCENARIOS) {
    if (scenario.catalog_id) {
      assert.equal(
        catalogIDs.has(scenario.catalog_id),
        true,
        scenario.id + " must reference an existing static scenario",
      );
    }
  }
});

test("browser system prompts stay byte-identical to the Go prompt constants", () => {
  const safeguardSource = fs.readFileSync(
    path.join(repositoryRoot, "go/service-control-api/internal/llmop/safeguard_harness.go"),
    "utf8",
  );
  const proposalSource = fs.readFileSync(
    path.join(repositoryRoot, "go/service-control-api/internal/llmop/planner.go"),
    "utf8",
  );

  function rawConstant(source, name) {
    const quote = String.fromCharCode(96);
    const marker = "const " + name + " = " + quote;
    const start = source.indexOf(marker);
    assert.notEqual(start, -1, "missing Go prompt constant " + name);
    const contentStart = start + marker.length;
    const end = source.indexOf(quote, contentStart);
    assert.notEqual(end, -1, "unterminated Go prompt constant " + name);
    return source.slice(contentStart, end);
  }

  assert.equal(
    contract.SYSTEM_PROMPTS.safeguard,
    rawConstant(safeguardSource, "naturalLanguageSafeguardSystemPrompt"),
  );
  assert.equal(
    contract.SYSTEM_PROMPTS.proposal,
    rawConstant(proposalSource, "proposalSystemPrompt"),
  );
  assert.match(
    safeguardSource,
    /const safeguardReviewUserPrefix = "Natural-language safeguard input: "/,
  );
  assert.match(
    proposalSource,
    /const userPromptPrefix = "LLM operation input: "/,
  );
});

test("stage prompts share bounded context but use distinct output contracts", () => {
  const scenario = contract.getScenario("direct-fresh-gpu-success");
  const safeguard = contract.buildBoundedInput(scenario, "safeguard");
  const proposal = contract.buildBoundedInput(scenario, "proposal");

  assert.equal(safeguard.user_request, proposal.user_request);
  assert.deepEqual(safeguard.request_scope, proposal.request_scope);
  assert.deepEqual(safeguard.operation_context, proposal.operation_context);
  assert.notDeepEqual(safeguard.required_output, proposal.required_output);
  assert.equal(JSON.stringify(safeguard).includes("appver-llm-inference-v1"), false);
  assert.equal(JSON.stringify(safeguard).includes("target-gpu-ready"), false);
  assert.equal(JSON.stringify(safeguard).includes("demo-secret"), false);
  assert.match(
    contract.buildCombinedPrompt(scenario, "safeguard"),
    /^\[SYSTEM\][\s\S]+\[USER\][\s\S]+\[OUTPUT\]/,
  );
  assert.match(
    contract.buildPromptParts(scenario, "safeguard").user,
    /^Natural-language safeguard input: \{"operation_context":/,
    "Go encoding/json sorts map keys lexicographically",
  );
  assert.equal(
    contract.stableJSONStringify({ z: 1, a: { y: 2, b: 3 } }),
    '{"a":{"b":3,"y":2},"z":1}',
  );
});

test("strict JSON parser rejects ambiguity before browser validation", () => {
  assert.throws(
    () => contract.parseStrictJSONObject('{"decision":"allow_request","decision":"reject_request"}'),
    /중복 JSON key/,
  );
  assert.throws(
    () => contract.parseStrictJSONObject('{"decision":"allow_request"} trailing'),
    /추가 내용/,
  );
  assert.throws(
    () => contract.parseStrictJSONObject('["allow_request"]'),
    /최상위 값/,
  );
  const caseVariant = contract.validateSafeguardResponse(
    '{"Decision":"allow_request","reason_code":"CASE_VARIANT","reason":"Not canonical.","confidence":0.9}',
  );
  assert.equal(caseVariant.ok, false);
  assert.match(caseVariant.errors.join(" "), /허용되지 않은 key/);
});

test("every stored fixture follows its documented stop or handoff path", () => {
  for (const scenario of contract.SCENARIOS) {
    if (scenario.preflight.status === "rejected") {
      assert.equal(scenario.expected_status, "REQUEST_REJECTED", scenario.id);
      assert.equal(scenario.safeguard_fixture, null, scenario.id);
      assert.equal(scenario.proposal_fixture, null, scenario.id);
      continue;
    }

    const safeguard = contract.validateSafeguardResponse(scenario.safeguard_fixture);
    assert.equal(safeguard.ok, true, scenario.id + ": " + safeguard.errors.join(" "));
    const safeguardOutcome = contract.evaluateSafeguard(safeguard.value);
    if (safeguardOutcome.terminal) {
      assert.equal(safeguardOutcome.status, scenario.expected_status, scenario.id);
      assert.equal(scenario.proposal_fixture, null, scenario.id);
      continue;
    }

    const proposal = contract.validateProposalResponse(scenario.proposal_fixture, scenario);
    if (!proposal.ok) {
      assert.equal(scenario.expected_status, "MANIFEST_REJECTED", scenario.id);
      assert.match(proposal.errors.join(" "), /중복 JSON key/);
      continue;
    }

    const proposalOutcome = contract.evaluateProposal(proposal.value, scenario);
    assert.equal(proposalOutcome.status, scenario.expected_status, scenario.id);
    if (proposalOutcome.status === "HANDOFF_READY") {
      assert.equal(proposalOutcome.handoff.submission_mode, "not_submitted");
      assert.equal(proposalOutcome.handoff.appdeploy_calls, 0);
      assert.equal(proposalOutcome.handoff.prepared_request.manifest.kind, "DeploymentManifest");
      assert.equal("command" in proposalOutcome.handoff.prepared_request.manifest.spec, false);
      assert.equal("target_profile_id" in proposalOutcome.handoff.prepared_request.manifest.spec, false);
      assert.equal("runtime" in proposalOutcome.handoff.prepared_request.manifest.spec, false);
    }
  }
});

test("manual output evidence never claims provider attestation", () => {
  assert.equal(contract.MODEL_BINDING.evidence_model, "manual-output-not-provider-attested");
  assert.equal(contract.MODEL_BINDING.model_selection_enabled, false);
  assert.equal(contract.MODEL_BINDING.selection_mode, "caller_pinned");
});

test("browser proposal mirror preserves the Go parser's optional empty assumptions field", () => {
  const scenario = contract.getScenario("direct-cpu-explicit-without-snapshot");
  const proposal = JSON.parse(scenario.proposal_fixture);
  delete proposal.assumptions;
  const validation = contract.validateProposalResponse(JSON.stringify(proposal), scenario);
  assert.equal(validation.ok, true, validation.errors.join(" "));
});

test("standalone page exposes the complete gated workflow without an API client", () => {
  const staticDir = path.join(__dirname, "static");
  const html = fs.readFileSync(path.join(staticDir, "llm_op_demo.html"), "utf8");
  const stylesheet = fs.readFileSync(path.join(staticDir, "llm_op_demo.css"), "utf8");
  const application = fs.readFileSync(path.join(staticDir, "llm_op_demo.js"), "utf8");

  for (const id of [
    "scenario-list",
    "start-scenario",
    "preflight-panel",
    "safeguard-prompt",
    "safeguard-response",
    "validate-safeguard",
    "proposal-prompt",
    "proposal-response",
    "validate-proposal",
    "final-result-json",
  ]) {
    assert.match(html, new RegExp('id="' + id + '"'));
  }
  assert.match(html, /href="\.\/llm_op_demo\.css"/);
  assert.match(html, /src="\.\/llm_op_demo_contract\.js"/);
  assert.match(html, /src="\.\/llm_op_demo\.js"/);
  assert.match(stylesheet, /@media \(max-width: 760px\)/);
  assert.doesNotMatch(application, /\bfetch\s*\(/);
  assert.doesNotMatch(application, /\.innerHTML\b/);
  assert.doesNotMatch(application, /\beval\s*\(/);

  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, "HTML IDs must be unique");
});

test("Go embed registration includes every standalone demo asset", () => {
  const source = fs.readFileSync(path.join(__dirname, "webui.go"), "utf8");
  for (const asset of [
    "static/llm_op_demo.html",
    "static/llm_op_demo.css",
    "static/llm_op_demo_contract.js",
    "static/llm_op_demo.js",
  ]) {
    assert.match(source, new RegExp(asset.replaceAll(".", "\\.")));
  }
  assert.match(source, /server\.GET\("\/llm-op-demo"/);
  assert.match(source, /server\.GET\("\/llm-op-demo\/"/);
});

test("published LLM output schemas are valid JSON objects", () => {
  for (const name of [
    "safeguard-review.v1alpha1.schema.json",
    "manifest-proposal.v1alpha1.schema.json",
  ]) {
    const schema = JSON.parse(fs.readFileSync(
      path.join(repositoryRoot, "schemas/llm-op", name),
      "utf8",
    ));
    assert.equal(schema.$schema, "https://json-schema.org/draft/2020-12/schema");
    assert.equal(schema.type, "object");
    assert.equal(schema.additionalProperties, false);
  }
});
