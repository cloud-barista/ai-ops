import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createWorkflowStore} from '../server/workflow-store.mjs';
const mock=await import('../server/mock-adapter.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return{};throw e;});
const geon=await import('../server/geon-adapter.mjs');
const approved=()=>({flow_id:'flow-mock',trace_id:'trace-mock',selection_version:1,selected_candidate_id:'demo-cpu8',plan_sha256:'hash',geon:{decision:{decision_id:'decision-1',action:'DEPLOY',selected_candidate_id:'demo-cpu8'},guard:{status:'APPROVED'},agent_authorization:{authorized:true},agent_execution:{status:'completed',request_guard:{status:'APPROVED'},result_guard:{status:'APPROVED'}},deployment_plan:{selected_candidate_id:'demo-cpu8'}}});
test('concurrent Mock execution creates one durable RUNNING acceptance receipt',async()=>{
  assert.equal(typeof mock.createMockAdapter,'function');const dir=await mkdtemp(join(tmpdir(),'khu-mock-'));const store=createWorkflowStore(dir);await store.write(approved());const adapter=mock.createMockAdapter({store});
  const results=await Promise.all(Array.from({length:4},()=>adapter.execute({flow_id:'flow-mock',selection_version:1})));
  assert.equal(new Set(results.map(r=>r.execution_id)).size,1);assert.equal(results[0].status,'RUNNING');assert.equal(results[0].mode,'mock');
  assert.equal((await createWorkflowStore(dir).read('flow-mock')).execution.execution_id,results[0].execution_id);
  await assert.rejects(()=>adapter.execute({flow_id:'flow-mock',selection_version:2}));
});
test('rejected Guard cannot be executed',async()=>{
  assert.equal(typeof mock.createMockAdapter,'function');const dir=await mkdtemp(join(tmpdir(),'khu-mock-'));const store=createWorkflowStore(dir);const r=approved();r.geon.guard.status='REJECTED';await store.write(r);
  await assert.rejects(()=>mock.createMockAdapter({store}).execute({flow_id:r.flow_id,selection_version:1}));
});
test('feedback uses matching deployment identity and validated user observation',()=>{
  assert.equal(typeof geon.toFeedbackMessages,'function');const r={...approved(),execution:{execution_id:'mock-1',status:'RUNNING',mode:'mock'}};
  const observation={source:'demo_input',started_at:'2026-10-01T00:00:00Z',ended_at:'2026-10-01T00:01:00Z',metrics:{resource:{cpu_average_percent:85,memory_peak_mib:1024,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:40,throughput_rps:100,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
  const f=geon.toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-01T01:00:00Z'});assert.equal(f.deploymentStatus.data.deployment_status.state,'RUNNING');assert.equal(f.optimizationFeedback.data.optimization_feedback.deployment_id,'mock-1');
  assert.throws(()=>geon.toFeedbackMessages({receipt:r,observation:{...observation,ended_at:'2026-09-01T00:00:00Z'}}));
});

test('Mock feedback links the accepted VM request and preserves its acceptance time',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'khu-mock-causal-')),store=createWorkflowStore(dir),r=approved();
  r.geon.deployment_request={message_id:'request-cpu8-r1'};
  await store.write(r);
  await mock.createMockAdapter({store,now:()=> '2026-10-01T00:00:00Z'}).execute({flow_id:r.flow_id,selection_version:1});
  const saved=await store.read(r.flow_id);
  assert.equal(saved.execution.request_message_id,'request-cpu8-r1');
  const observation={source:'demo_input',started_at:'2026-10-01T00:00:10Z',ended_at:'2026-10-01T00:01:00Z',metrics:{resource:{cpu_average_percent:85,memory_peak_mib:1024,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:80,throughput_rps:100,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
  // A later proposed revision has not been accepted by this Mock execution.
  saved.geon.deployment_request={message_id:'request-cpu8-r2'};
  const f=geon.toFeedbackMessages({receipt:saved,observation,nowUtc:'2026-10-01T00:01:01Z'});
  assert.equal(f.deploymentStatus.causation_id,'request-cpu8-r1');
  assert.equal(f.optimizationFeedback.causation_id,f.deploymentStatus.message_id);
  assert.equal(f.deploymentStatus.data.deployment_status.updated_at,'2026-10-01T00:00:00Z');
  assert.throws(()=>geon.toFeedbackMessages({receipt:saved,observation:{...observation,started_at:'2026-09-30T23:59:00Z'}}),e=>e.code==='invalid_observation');
});

test('another Mock observation reuses its accepted status identity instead of conflicting at the same timestamp',()=>{
  const accepted={contract_version:'1.0',message_id:'accepted-mock-status',message_type:'deployment.status.changed',occurred_at:'2026-10-01T00:01:01Z',correlation_id:'flow-mock-s1',trace_id:'trace-mock',causation_id:'request-cpu8-r1',source:{system:'khu-research-poc',component:'integration'},target:{system:'khu-geon',component:'agent-control'},data:{deployment_status:{deployment_id:'mock-1',decision_id:'decision-1',state:'RUNNING',message:'Mock plan acceptance; no infrastructure executed',updated_at:'2026-10-01T00:00:00Z'}}};
  const r={...approved(),execution:{execution_id:'mock-1',mode:'mock',status:'RUNNING',occurred_at:'2026-10-01T00:00:00Z',request_message_id:'request-cpu8-r1'},feedback:{messages:{deploymentStatus:accepted}}};
  const observation={source:'demo_input',started_at:'2026-10-01T00:01:10Z',ended_at:'2026-10-01T00:02:00Z',metrics:{resource:{cpu_average_percent:85,memory_peak_mib:1024,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:80,throughput_rps:100,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
  const result=geon.toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-01T00:02:01Z'});
  assert.deepEqual(result.deploymentStatus,accepted);
  assert.equal(result.optimizationFeedback.causation_id,'accepted-mock-status');
  assert.notEqual(result.optimizationFeedback.message_id,'accepted-mock-status');
  assert.equal(result.optimizationFeedback.data.optimization_feedback.observation_window.started_at,'2026-10-01T00:01:10Z');
  r.feedback.messages.deploymentStatus={...accepted,trace_id:'other-trace'};
  assert.throws(()=>geon.toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-01T00:02:01Z'}),e=>e.code==='mock_ack_mismatch');
});
