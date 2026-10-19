import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createPlatformServer} from '../server/index.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {input,profile,recommendation,clients,catalog,snapshots} from './fixtures.mjs';
import {toFeedbackMessages} from '../server/geon-adapter.mjs';

function executed(){return {flow_id:'flow-recovery',trace_id:'trace-recovery',input:input(),application_profile:profile(),resource_recommendation:recommendation('flow-recovery'),selection_version:1,selected_candidate_id:'demo-cpu8',geon_flow_id:'flow-recovery-s1',state:'MOCK_RUNNING',history:[],candidates:{all:[{candidate_id:'demo-cpu8',selectable:true},{candidate_id:'demo-cpu2',selectable:true}]},geon:{correlation_id:'flow-recovery-s1',trace_id:'trace-recovery',profile_id:'profile-tiny',decision:{decision_id:'decision-1',action:'DEPLOY',selected_candidate_id:'demo-cpu8'},deployment_plan:{selected_candidate_id:'demo-cpu8'}},execution:{execution_id:'mock-old',status:'RUNNING',mode:'mock'}};}
function observation(){const now=new Date();return {source:'demo_input',started_at:new Date(now-60000).toISOString(),ended_at:now.toISOString(),metrics:{resource:{cpu_average_percent:85,memory_peak_mib:1024,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:80,throughput_rps:100,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};}
async function harness(t,overrides,seed=executed()){
  const directory=await mkdtemp(join(tmpdir(),'khu-recovery-')),store=createWorkflowStore(directory);await store.write(seed);
  const server=createPlatformServer({store,clients:clients(overrides),catalog,snapshots});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const call=async(action,body)=>{const response=await fetch(`http://127.0.0.1:${server.address().port}/api/v1/flows/${seed.flow_id}/`+action,body===undefined?{}:{method:'POST',body:JSON.stringify(body)});return {status:response.status,data:await response.json()};};return {store,directory,call};
}
test('selection after Mock archives the old execution and clears all active downstream state',async t=>{
  const seed=executed();seed.feedback_state='UNKNOWN';seed.feedback_pending={uncertain:true};seed.feedback={old:true};seed.feedback_history=[{old:true}];
  const h=await harness(t,{},seed);const result=await h.call('selection',{candidate_id:'demo-cpu2',expected_selection_version:1});
  assert.equal(result.status,200);const r=result.data;assert.equal(r.selection_version,2);assert.equal(r.state,'SELECTED');assert.equal(r.execution,null);assert.equal(r.geon,null);assert.equal(r.feedback_state,null);assert.equal(r.feedback_pending,null);assert.deepEqual(r.feedback_history,[]);assert.equal(r.selection_history[0].execution.execution_id,'mock-old');assert.equal(r.selection_history[0].feedback_state,'UNKNOWN');
});
test('timeout after accepted feedback can reconcile by GET without another POST',async t=>{
  let remote,posts=0;const seed=executed();const h=await harness(t,{deploymentStatus:async()=>{},feedback:async message=>{posts++;remote={...seed.geon,optimization_feedback:message,scaling_decision:{action:'SCALE_OUT'}};throw Error('timeout after acceptance');},flow:async()=>remote},seed);
  assert.equal((await h.call('feedback',{selection_version:1,observation:observation()})).status,502);
  const result=await h.call('feedback-reconciliation');assert.equal(result.status,200);assert.equal(result.data.feedback_state,'RECORDED');assert.equal(result.data.feedback.geon.scaling_decision.action,'SCALE_OUT');assert.equal(posts,1);
});
test('restart with pending feedback recovers only the same feedback identity',async t=>{
  const seed=executed(),o=observation(),messages=toFeedbackMessages({receipt:seed,observation:o});seed.feedback_state='PENDING';seed.feedback_pending={observation:o,messages,observation_hash:'pending-hash'};
  let remote={...seed.geon,optimization_feedback:structuredClone(messages.optimizationFeedback),scaling_decision:{action:'KEEP'}};
  remote.optimization_feedback.data.optimization_feedback.feedback_id='another-feedback';const h=await harness(t,{flow:async()=>remote},seed);
  const first=await h.call('feedback-reconciliation');assert.equal(first.status,200);assert.equal(first.data.feedback_state,'UNKNOWN');assert.ok(first.data.feedback_pending);
  remote.optimization_feedback=messages.optimizationFeedback;const restartedStore=createWorkflowStore(h.directory);assert.equal((await restartedStore.read(seed.flow_id)).feedback_state,'UNKNOWN');
  const recovered=await h.call('feedback-reconciliation');assert.equal(recovered.data.feedback_state,'RECORDED');assert.equal(recovered.data.feedback.observation_hash,'pending-hash');
});
test('late feedback cannot overwrite a changed selection',async t=>{
  let finish,received;const pending=new Promise(r=>finish=r),seed=executed();const h=await harness(t,{deploymentStatus:async()=>{},feedback:async message=>{received=message;await pending;return {...seed.geon,optimization_feedback:message};}},seed);
  const request=h.call('feedback',{selection_version:1,observation:observation()});while(!received)await new Promise(r=>setTimeout(r,5));
  await h.call('selection',{candidate_id:'demo-cpu2',expected_selection_version:1});finish();assert.equal((await request).status,409);const current=await h.store.read(seed.flow_id);assert.equal(current.state,'SELECTED');assert.equal(current.feedback_state,null);assert.equal(current.geon,null);
});
