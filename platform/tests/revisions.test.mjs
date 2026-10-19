import assert from 'node:assert/strict';
import test from 'node:test';

const revisions = await import('../view-model.mjs').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {};
  throw error;
});
function model(receipt, options) {
  assert.equal(typeof revisions.revisionComparisonModel, 'function', 'Revision comparison is not implemented');
  return revisions.revisionComparisonModel(receipt, options);
}
function revision(number, replicas, operation = 'DEPLOY') {
  return {
    revision: number, phase: number === 1 ? 'INITIAL' : 'OPTIMIZED', trigger_action: operation,
    created_at: `2026-10-05T00:0${number}:00Z`,
    desired_deployment_spec: {
      spec_version: '1.0', decision_id: 'decision-flow-test-s1', operation,
      application: {app_id: 'bert-poc', app_version: '1.0', artifact: {type: 'SCRIPT', uri: 'demo://app'}},
      target_runtime: 'VM',
      desired_infrastructure: {node_count: replicas, cpu_cores_per_node: 2, memory_mib_per_node: 1024,
        storage_gib_per_node: 20, accelerator: {}, placement: {required_labels: {spec_id: 'aws+m6a.large'}, anti_affinity: true}},
      inference_configuration: {runtime_engine: 'PYTORCH', precision: 'FP32', max_batch_size: 1, replicas},
      metadata: {profile_id: 'profile-test'}
    },
    deployment_request: {data: {deployment_request: {operation}}}
  };
}
function snapshot(items, changes = {}) {
  return {correlation_id: 'flow-test-s1', trace_id: 'trace-test',
    decision: {selected_candidate_id: 'cpu2'}, manifest_revisions: items, ...changes};
}
function receipt() {
  const first = revision(1, 1), out = revision(2, 2, 'SCALE_OUT'), inside = revision(3, 1, 'SCALE_IN');
  return {flow_id: 'flow-test', trace_id: 'trace-test', selection_version: 1, selected_candidate_id: 'cpu2',
    geon_flow_id: 'flow-test-s1', candidates: {all: [{candidate_id: 'cpu2', name: 'm6a.large'}, {candidate_id: 'gpu', name: 'g6.2xlarge'}]},
    geon: snapshot([first]), feedback_history: [
      {geon: snapshot([first, out])}, {geon: snapshot([first, out, inside])},
      {geon: snapshot([first, out, inside])}
    ], feedback: {geon: snapshot([first, out, inside])}};
}

test('repeated feedback snapshots yield one ordered revision timeline', () => {
  const view = model(receipt());
  assert.deepEqual(view.group.revisions.map(item => [item.revision, item.replicas]), [[1, 1], [2, 2], [3, 1]]);
  assert.equal(view.group.candidateName, 'm6a.large');
  assert.deepEqual([view.from.revision, view.to.revision], [1, 3]);
});

test('a new VM selection stays empty while earlier selection revisions remain explicitly selectable', () => {
  const previous = receipt();
  const next = {...previous, selection_version: 2, selected_candidate_id: 'gpu', geon_flow_id: null,
    geon: null, feedback: null, feedback_history: [], selection_history: [previous]};
  const view = model(next);
  assert.equal(view.group.selectionVersion, 2);
  assert.equal(view.group.revisions.length, 0);
  assert.equal(view.changes.length, 0);
  const archive = model(next, {selectionVersion: 1, fromRevision: 1, toRevision: 2});
  assert.equal(archive.group.isCurrent, false);
  assert.equal(archive.group.candidateName, 'm6a.large');
  assert.deepEqual([archive.from.replicas, archive.to.replicas], [1, 2]);
});

test('revision snapshots from another Flow or candidate cannot contaminate the current comparison', () => {
  const record = receipt();
  record.feedback_history = [
    {geon: snapshot([revision(9, 9)], {correlation_id: 'other-flow-s1'})},
    {geon: snapshot([revision(8, 8)], {trace_id: 'other-trace'})},
    {geon: snapshot([revision(7, 7)], {decision: {selected_candidate_id: 'gpu'}})}
  ];
  record.feedback = null;
  assert.deepEqual(model(record).group.revisions.map(item => item.revision), [1]);
});

test('comparison reports actual resource changes and distinguishes unset from zero', () => {
  const record = receipt(), first = revision(1, 1), out = revision(2, 2, 'SCALE_OUT');
  out.desired_deployment_spec.desired_infrastructure.accelerator.count = 0;
  out.desired_deployment_spec.desired_infrastructure.memory_mib_per_node = 2048;
  record.feedback_history = [];
  record.feedback = {geon: snapshot([first, out])};
  const view = model(record);
  assert.deepEqual(view.changes.map(item => [item.path, item.before, item.after]), [
    ['desired_infrastructure.accelerator.count', undefined, 0],
    ['desired_infrastructure.memory_mib_per_node', 1024, 2048],
    ['desired_infrastructure.node_count', 1, 2],
    ['inference_configuration.replicas', 1, 2],
    ['operation', 'DEPLOY', 'SCALE_OUT']
  ]);
  assert.equal(view.changes.find(item => item.path === 'inference_configuration.replicas').label, '어플리케이션 복제본');
});

test('reordered object keys and generated decision IDs do not appear as execution setting changes', () => {
  const record = receipt(), first = revision(1, 1), second = revision(2, 1);
  second.desired_deployment_spec.application = {artifact: {uri: 'demo://app', type: 'SCRIPT'}, app_version: '1.0', app_id: 'bert-poc'};
  second.desired_deployment_spec.decision_id = 'regenerated-decision';
  record.feedback_history = [];
  record.feedback = {geon: snapshot([first, second])};
  assert.deepEqual(model(record).changes, []);
});

test('array settings and null values are compared without hiding a removed or added field', () => {
  const record = receipt(), first = revision(1, 1), second = revision(2, 1);
  first.desired_deployment_spec.runtime = {args: ['--batch', '1'], environment: {OPTION: null}};
  second.desired_deployment_spec.runtime = {args: ['--batch', '8'], environment: {}};
  record.geon = snapshot([first]);
  record.feedback_history = [];
  record.feedback = {geon: snapshot([first, second])};
  assert.deepEqual(model(record).changes.map(item => [item.path, item.before, item.after]), [
    ['runtime.args', ['--batch', '1'], ['--batch', '8']], ['runtime.environment.OPTION', null, undefined]
  ]);
});

test('missing revisions and stale saved selectors never fabricate an optimized plan', () => {
  assert.equal(model(null).group, null);
  const record = receipt(); record.feedback_history = []; record.feedback = null;
  const view = model(record, {selectionVersion: 99, fromRevision: 8, toRevision: 9});
  assert.deepEqual([view.group.selectionVersion, view.from.revision, view.to], [1, 1, null]);
  assert.equal(view.canCompare, false);
  assert.equal(view.changes.length, 0);
});

test('application identifiers are displayed literally even when they match a translated action', () => {
  const record = receipt(), first = revision(1, 1), second = revision(2, 1);
  second.desired_deployment_spec.application.app_id = 'DEPLOY';
  record.feedback_history = [];
  record.feedback = {geon: snapshot([first, second])};
  assert.equal(model(record).changes[0].afterLabel, 'DEPLOY');
});
