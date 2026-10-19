import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {profile} from './fixtures.mjs';
const module=await import('../server/local-execution.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
function receipt(){return {flow_id:'flow-local-test',trace_id:'trace-test',state:'DEPLOY_APPROVED',input:{execution_target:'local'},selection_version:1,selected_candidate_id:'khu-local-pc',geon_flow_id:'flow-local-test-s1',application_profile:profile(),geon:{decision:{action:'DEPLOY'},guard:{status:'APPROVED'},deployment_plan:{selected_candidate_id:'khu-local-pc',desired_infrastructure:{node_count:1},inference_configuration:{replicas:1}}},history:[]};}
async function setup(t,overrides={}){
  assert.equal(typeof module.createLocalExecutionRoutes,'function','AppDeploy execution route is missing');
  const dir=await mkdtemp(join(tmpdir(),'khu-local-'));const store=createWorkflowStore(dir);await store.write(receipt());
  let submissions=0;let status='RUNNING';
  const clients={registerLocalApp:async()=>({app_version_id:'appver-test'}),localTargets:async()=>[{target_profile_id:'khu-local-pc',csp:'local',runtime:{runtime_type:'local',operating_mode:'local_process'}}],localDeployments:async()=>[],appPrepare:async()=>({status:'PREPARED'}),appSubmit:async()=>{submissions++;return {status:'SUBMITTED',deployment:{deployment_id:'dep-test',app_version_id:'appver-test',target_profile_id:'khu-local-pc',status}};},appStatus:async()=>({status:'SUBMITTED',deployment:{deployment_id:'dep-test',target_profile_id:'khu-local-pc',status}}),appLogs:async()=>({items:[{message:'actual worker started'}]}),appStop:async()=>{status='STOPPED';return {deployment_id:'dep-test',target_profile_id:'khu-local-pc',status};},...overrides};
  const route=module.createLocalExecutionRoutes();
  const call=(action,value={selection_version:1},method='POST')=>route({request:{method},path:'/api/v1/flows/flow-local-test/appdeploy/'+action,value,store,clients});
  return {store,call,get submissions(){return submissions;}};
}
test('real AppDeploy receipt binds the chosen local target and survives reload',async t=>{
  const h=await setup(t);await h.call('prepare');await h.call('submit');const r=await h.store.read('flow-local-test');
  assert.equal(r.execution.mode,'local_process');assert.equal(r.execution.execution_id,'dep-test');assert.equal(r.execution.target_profile_id,r.selected_candidate_id);assert.equal(h.submissions,1);
  await h.call('submit');assert.equal(h.submissions,1);await h.call('status',undefined,'GET');await h.call('stop');assert.equal((await h.store.read('flow-local-test')).execution.status,'STOPPED');
});
test('historical AWS selection cannot execute on the local PC',async t=>{
  const h=await setup(t);await h.store.update('flow-local-test',r=>({...r,selected_candidate_id:'demo-m6a-large'}));
  await assert.rejects(()=>h.call('prepare'),e=>e.code==='local_target_required');assert.equal(h.submissions,0);
});
test('ambiguous submission is saved and never retried by another click',async t=>{
  let posts=0;const h=await setup(t,{appSubmit:async()=>{posts++;throw Error('timeout');}});await h.call('prepare');
  await h.call('submit');await assert.rejects(()=>h.call('submit'),e=>e.code==='deployment_unknown');assert.equal(posts,1);assert.equal((await h.store.read('flow-local-test')).appdeploy.status,'UNKNOWN');
});
test('additional targets and active local workers block submission',async t=>{
  const h=await setup(t,{localTargets:async()=>[{target_profile_id:'unrelated-node'}]});await assert.rejects(()=>h.call('prepare'),e=>e.code==='local_target_required');
  const active=await setup(t,{localDeployments:async()=>[{status:'RUNNING'}]});await active.call('prepare');await assert.rejects(()=>active.call('submit'),e=>e.code==='local_worker_busy');assert.equal(active.submissions,0);
});
test('measured feedback identifies a real deployment and refuses example observations',async()=>{
  const {toFeedbackMessages}=await import('../server/geon-adapter.mjs');const r=receipt();r.geon.decision.decision_id='decision-local';r.execution={mode:'local_process',execution_id:'dep-real',target_profile_id:'khu-local-pc',status:'RUNNING'};
  const observation={source:'local_probe',started_at:'2026-10-01T00:00:00Z',ended_at:'2026-10-01T00:00:01Z',metrics:{resource:{cpu_average_percent:1,memory_peak_mib:32,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:2,throughput_rps:64,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
  const result=toFeedbackMessages({receipt:r,observation,nowUtc:'2026-10-01T00:00:02Z'});assert.equal(result.optimizationFeedback.data.optimization_feedback.deployment_id,'dep-real');assert.match(result.deploymentStatus.data.deployment_status.message,/Actual/);
  assert.throws(()=>toFeedbackMessages({receipt:r,observation:{...observation,source:'demo_input'}}),e=>e.code==='measured_feedback_required');
});

test('Prometheus feedback uses server-collected samples and preserves the explicit inference probe',async t=>{
  const observation={source:'prometheus',flow_id:'flow-local-test',runtime_id:'local-process-123',started_at:'2026-10-01T00:00:00Z',ended_at:'2026-10-01T00:00:10Z',metrics:{resource:{cpu_average_percent:1,memory_peak_mib:260,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:5,throughput_rps:6.4,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
  let calls=0;
  const h=await setup(t,{prometheusObservation:async()=>{calls++;return observation;},feedback:async message=>({correlation_id:'flow-local-test-s1',trace_id:'trace-test',profile_id:'profile-tiny',scaling_decision:{action:'KEEP'},optimization_feedback:message})});
  await h.store.update('flow-local-test',r=>({...r,geon:{...r.geon,decision:{...r.geon.decision,decision_id:'decision-local'}},measurement:{source:'local_probe',sample_count:64}}));
  await h.call('prepare');await h.call('submit');
  const result=await h.call('prometheus-feedback',{selection_version:1,observation:{source:'demo_input'}});
  assert.equal(result.data.feedback.observation.source,'prometheus');assert.equal(result.data.prometheus_measurement.source,'prometheus');assert.equal(result.data.measurement.source,'local_probe');assert.equal(result.data.feedback_state,'RECORDED');assert.equal(calls,1);
  await h.call('stop');await assert.rejects(()=>h.call('prometheus-feedback'),e=>e.code==='running_worker_required');assert.equal(calls,1);
});

test('changing the operation selection during a measurement never dispatches a stale agent',async t=>{
 const observation={source:'local_probe',started_at:'2026-10-01T00:00:00Z',ended_at:'2026-10-01T00:00:10Z',metrics:{resource:{cpu_average_percent:1,memory_peak_mib:260,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:5,throughput_rps:6.4,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
 const h=await setup(t,{measureLocalWorker:async()=>{await h.store.update('flow-local-test',r=>({...r,agents:{operation:{name:'NewOperation'}}}));return observation;},feedback:async()=>assert.fail('stale agent must not be dispatched')});
 await h.store.update('flow-local-test',r=>({...r,geon:{...r.geon,decision:{...r.geon.decision,decision_id:'decision-local'}}}));await h.call('prepare');await h.call('submit');
 await assert.rejects(h.call('measure'),e=>e.code==='agent_selection_stale');assert.notEqual((await h.store.read('flow-local-test')).feedback_state,'PENDING');
});
