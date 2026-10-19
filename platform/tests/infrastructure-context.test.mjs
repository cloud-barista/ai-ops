import assert from 'node:assert/strict';
import test from 'node:test';
import {catalog, input, profile} from './fixtures.mjs';

const context = await import('../infrastructure-context.mjs').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {};
  throw error;
});
function record() {
  const candidates = catalog.entries.map(shape => ({candidate_id:shape.name, name:shape.name,
    shape, selectable:true, reason:'조건 충족', origin:'demo_input', price:{},
    measurement:shape.name === 'm6a.2xlarge' ? {batch_p95_ms:1.68, throughput:673.45,
      run_ids:['cpu8-run'], comparison:'historical_reference', cpu_core_limit:8} : null}));
  return {flow_id:'context-flow', selection_version:1, input:input(), application_profile:profile(),
    candidates:{all:candidates}, selected_candidate_id:'m6a.2xlarge'};
}
test('selected context binds hardware, workload and measurements to the chosen VM', () => {
  assert.equal(typeof context.infrastructureContextModel, 'function');
  const r = record(), before = structuredClone(r);
  const cpu = context.infrastructureContextModel(r);
  assert.equal(cpu.candidateId, 'm6a.2xlarge');
  assert.equal(cpu.capacity.cpu, 8);
  assert.equal(cpu.minimum.cpu, 2);
  assert.deepEqual(cpu.measurement.runIds, ['cpu8-run']);
  assert.equal(cpu.measurement.throughput, 673.45);
  assert.equal(cpu.workload.modelId, r.input.model.model_id);
  r.selected_candidate_id = 'g6.8xlarge';
  const gpu = context.infrastructureContextModel(r);
  assert.equal(gpu.capacity.cpu, 32);
  assert.equal(gpu.capacity.memoryMiB, 131072);
  assert.equal(gpu.capacity.gpuCount, 1);
  assert.equal(gpu.measurement, null);
  assert.equal(gpu.runtimeDevice, 'CPU'); // GPU hardware does not change the application's CPU request.
  assert.deepEqual({...r,selected_candidate_id:before.selected_candidate_id}, before);
});
test('GPU execution reflects one L4 on both GPU sizes and a stale plan is never reused', () => {
  assert.equal(typeof context.infrastructureContextModel, 'function');
  const r = record();
  r.application_profile.requirements.accelerator = {required:true,type:'GPU',count_min:1,memory_mib_min_per_device:16000};
  r.geon = {correlation_id:'context-flow-s1',decision:{action:'DEPLOY',selected_candidate_id:'g6.2xlarge'},
    guard:{status:'APPROVED'},deployment_plan:{selected_candidate_id:'g6.2xlarge'}};
  r.selected_candidate_id = 'g6.2xlarge';
  const small = context.infrastructureContextModel(r);
  assert.equal(small.runtimeDevice, 'GPU');
  assert.equal(small.planApproved, true);
  r.selected_candidate_id = 'g6.8xlarge';
  const large = context.infrastructureContextModel(r);
  assert.equal(large.capacity.gpuCount, small.capacity.gpuCount);
  assert.equal(large.planApproved, false);
  assert.equal(large.route, 'historical');
  assert.equal(large.executionMode, 'mock');
});
test('local and cloud paths keep their own execution mode and unknown selections stay empty', () => {
  assert.equal(typeof context.infrastructureContextModel, 'function');
  const r = record();
  r.input.execution_target = 'cloud';
  assert.equal(context.infrastructureContextModel(r).executionMode, 'cloud_vm');
  r.input.execution_target = 'local';
  r.candidates.all = [{candidate_id:'khu-local-pc',name:'이 PC · 로컬 실행',shape:{cpu_cores:12,memory_mib:32768,gpu_count:0},selectable:true}];
  r.selected_candidate_id = 'khu-local-pc';
  assert.equal(context.infrastructureContextModel(r).executionMode, 'local_process');
  assert.equal(context.infrastructureContextModel(r).measurement, null);
  r.selected_candidate_id = 'm6a.large';
  assert.equal(context.infrastructureContextModel(r), null);
});
