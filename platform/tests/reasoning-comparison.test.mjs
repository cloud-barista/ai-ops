import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createPlatformServer} from '../server/index.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {input,profile,catalog,snapshots} from './fixtures.mjs';

function record(){
  return {flow_id:'flow-reasoning',trace_id:'trace-reasoning',selection_version:1,state:'DEPLOY_APPROVED',input:input(),application_profile:profile(),hashes:{profile:'profile-hash',recommendation:'resource-hash'},selected_candidate_id:'cpu8',candidates:{all:[{candidate_id:'cpu8',selectable:true,name:'m6a.2xlarge'},{candidate_id:'cpu2',selectable:true,name:'m6a.large'}]},geon_flow_id:'flow-reasoning-s1',plan_sha256:'plan-hash',geon:{correlation_id:'flow-reasoning-s1',trace_id:'trace-reasoning',profile_id:'profile-tiny',decision:{action:'DEPLOY',selected_candidate_id:'cpu8'},deployment_plan:{selected_candidate_id:'cpu8'},guard:{status:'APPROVED'}},history:[]};
}
function comparison(overrides={}){
  const model={execution_status:'executed',candidate_id:'qwen3.5-profile-generator',actual_model:'qwen3.5:4b',provider:'local-openai-compatible',action:'DEPLOY',selected_candidate_id:'cpu8',reason:'Resource conditions are met.',confidence:.9,latency_ms:1400};
  return {correlation_id:'flow-reasoning-s1',candidate_id:model.candidate_id,created_at:new Date().toISOString(),rule_based:{mode:'rule_based',execution_status:'executed',action:'DEPLOY',selected_candidate_id:'cpu8',guard_status:'APPROVED',latency_ms:0},simple_inference:{...model,mode:'simple_inference',guard_status:'NOT_APPLIED'},validated_inference:{...model,mode:'validated_inference',guard_status:'APPROVED'},agreement:{action_match:true,candidate_match:true},...overrides};
}
async function harness(t,upstream){
  const directory=await mkdtemp(join(tmpdir(),'khu-reasoning-')),store=createWorkflowStore(directory);
  await store.write(record());
  const server=createPlatformServer({store,catalog,snapshots,clients:upstream});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const send=async(path,body)=>{const r=await fetch(`http://127.0.0.1:${server.address().port}/api/v1/flows/flow-reasoning`+path,body===undefined?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};};
  return {store,directory,send};
}

test('comparison is persisted with input binding without replacing the approved plan or execution',async t=>{
  const h=await harness(t,{reasoning:async(id,body)=>{
    assert.equal(id,'flow-reasoning-s1');assert.deepEqual(body,{candidate_id:'qwen3.5-profile-generator'});return comparison();
  }});
  const before=await h.store.read('flow-reasoning');
  const response=await h.send('/reasoning-comparison',{selection_version:1});assert.equal(response.status,200);
  const saved=await createWorkflowStore(h.directory).read('flow-reasoning');
  assert.equal(saved.reasoning_comparisons[0].status,'COMPLETED');
  assert.equal(saved.reasoning_comparisons[0].profile_sha256,'profile-hash');
  assert.equal(saved.reasoning_comparisons[0].selected_candidate_id,'cpu8');
  assert.deepEqual(saved.geon,before.geon);assert.equal(saved.plan_sha256,'plan-hash');assert.equal(saved.execution,undefined);
  const event=saved.history.find(e=>e.event==='reasoning_compared');
  assert.ok(event);assert.equal(saved.reasoning_comparisons[event.comparison_index].result.simple_inference.actual_model,'qwen3.5:4b');
});

test('missing plan, stale selection and caller-selected model are rejected before comparison',async t=>{
  const h=await harness(t,{reasoning:async()=>{throw Error('must not call provider');}});
  assert.equal((await h.send('/reasoning-comparison',{selection_version:4})).status,409);
  assert.equal((await h.send('/reasoning-comparison',{selection_version:1,candidate_id:'arbitrary'})).status,400);
  await h.store.update('flow-reasoning',r=>({...r,geon:null}));
  assert.equal((await h.send('/reasoning-comparison',{selection_version:1})).status,409);
  assert.equal((await h.store.read('flow-reasoning')).reasoning_comparisons,undefined);
});

test('concurrent comparison and selection change are blocked while a model call is pending',async t=>{
  let release,started;const gate=new Promise(r=>release=r),began=new Promise(r=>started=r);
  const h=await harness(t,{reasoning:async()=>{started();await gate;return comparison();}});
  const first=h.send('/reasoning-comparison',{selection_version:1});
  // A missing route must fail instead of leaving the test waiting for the provider.
  const beganOrResponse=await Promise.race([began.then(()=>true),first.then(()=>false)]);assert.equal(beganOrResponse,true);
  try{
    assert.equal((await h.send('/reasoning-comparison',{selection_version:1})).status,409);
    assert.equal((await h.send('/selection',{expected_selection_version:1,candidate_id:'cpu2'})).status,409);
  }finally{release();}
  assert.equal((await first).status,200);
  const r=await h.store.read('flow-reasoning');assert.equal(r.reasoning_comparisons.length,1);assert.equal(r.selection_version,1);
});

test('transport failure remains unknown and GET recovers the existing result without another model call',async t=>{
  let recovered;
  const h=await harness(t,{reasoning:async()=>{throw Error('password=private-details');},flow:async()=>({...record().geon,...(recovered?{reasoning_comparison:recovered}:{})})});
  const r=await h.send('/reasoning-comparison',{selection_version:1});assert.equal(r.status,502);assert.equal(r.data.error.code,'reasoning_result_unknown');
  assert.ok(!JSON.stringify(r.data).includes('private-details'));
  assert.equal((await h.store.read('flow-reasoning')).reasoning_comparisons[0].status,'UNKNOWN');
  assert.equal((await h.send('/reasoning-comparison',{selection_version:1})).status,409);
  recovered=comparison();const loaded=await h.send('/reasoning-comparison');assert.equal(loaded.status,200);
  assert.equal(loaded.data.reasoning_comparisons[0].status,'COMPLETED');assert.equal(loaded.data.reasoning_comparisons.length,1);
});

test('wrong flow or old model response is never accepted as the current comparison',async t=>{
  const h=await harness(t,{reasoning:async()=>comparison({correlation_id:'other-flow'}),flow:async()=>({...record().geon,reasoning_comparison:comparison({created_at:'2020-01-01T00:00:00Z'})})});
  assert.equal((await h.send('/reasoning-comparison',{selection_version:1})).status,502);
  assert.equal((await h.send('/reasoning-comparison')).data.reasoning_comparisons[0].status,'UNKNOWN');
});

test('provider unavailable response is recorded without a successful model judgement',async t=>{
  const unavailable={execution_status:'provider_unavailable',candidate_id:'qwen3.5-profile-generator',guard_status:'NOT_APPLIED',error:'provider_unavailable',latency_ms:0};
  const h=await harness(t,{reasoning:async()=>comparison({simple_inference:unavailable,validated_inference:{...unavailable,execution_status:'skipped'}})});
  const r=await h.send('/reasoning-comparison',{selection_version:1});assert.equal(r.status,200);
  assert.equal(r.data.reasoning_comparisons[0].status,'UNAVAILABLE');assert.equal(r.data.reasoning_comparisons[0].result.simple_inference.action,undefined);
});
