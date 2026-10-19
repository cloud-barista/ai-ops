import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createPlatformServer} from '../server/index.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {input,profile,catalog,snapshots} from './fixtures.mjs';
import {historyEventDetails} from '../history.mjs';
import {pipelineEvents} from '../pipeline.mjs';
const ui=await import('../reasoning.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});

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
  const event=pipelineEvents(saved).find(e=>e.label.includes('판단 방식 비교'));
  assert.ok(event);assert.equal(historyEventDetails(saved,event.id).payload.result.simple_inference.actual_model,'qwen3.5:4b');
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

test('display separates raw rejection from guarded fallback and does not invent a rule latency',()=>{
  assert.equal(typeof ui.reasoningPanelModel,'function');
  const result=comparison();result.simple_inference.action='DEPLOY';result.simple_inference.selected_candidate_id='invented';
  result.validated_inference.guard_status='REJECTED';result.validated_inference.reason='Use the existing safe decision.';
  const r={...record(),reasoning_comparisons:[{status:'COMPLETED',selection_version:1,selected_candidate_id:'cpu8',result}]};
  const v=ui.reasoningPanelModel(r);assert.equal(v.rows.length,3);assert.equal(v.rows[0].duration,'별도 미측정');
  assert.equal(v.rows[1].candidate,'invented');assert.equal(v.rows[2].candidate,'m6a.2xlarge');assert.equal(v.rows[2].guard,'제안 거부 · 규칙 판단 적용');
  r.selection_version=2;assert.equal(ui.reasoningPanelModel(r).rows.length,0);
});

test('unexecuted model rows remain unavailable and historical comparison stays viewable',()=>{
  assert.equal(typeof ui.reasoningPanelModel,'function');
  const result=comparison({simple_inference:{execution_status:'provider_unavailable'},validated_inference:{execution_status:'skipped'}});
  const v=ui.reasoningPanelModel({...record(),reasoning_comparisons:[{status:'UNAVAILABLE',selection_version:1,result}]});
  assert.equal(v.rows[1].action,'응답 없음');assert.equal(v.rows[2].action,'비교 미실행');assert.equal(v.rows[1].duration,'—');
});

test('comparison carries the current app and selected resource into one readable context',()=>{
  const r={...record(),reasoning_comparisons:[{status:'COMPLETED',selection_version:1,selected_candidate_id:'cpu8',result:comparison()}]};
  const view=ui.reasoningPanelModel(r);
  assert.equal(view.context.app,r.application_profile.app_id);
  assert.equal(view.context.resource,'m6a.2xlarge');
  assert.ok(view.context.workload.includes('배치'));
  assert.equal(view.summaryValue,'세 판단 일치');
  assert.equal(view.nextHref,'#deployment');
  assert.ok(view.executionNote.includes('검증을 통과한 배포 계획'));
});

test('readable explanations preserve the actual model reason in detail instead of inventing a translation',()=>{
  const result=comparison();result.simple_inference.reason='A previously unseen English explanation.';
  const view=ui.reasoningPanelModel({...record(),reasoning_comparisons:[{status:'COMPLETED',selection_version:1,result}]});
  assert.equal(view.rows[1].originalReason,result.simple_inference.reason);
  assert.ok(!view.rows[1].reason.includes('previously unseen'));
  assert.ok(view.rows[1].reason.includes('AI 제안'));
  assert.ok(view.rows[2].reason.includes('검증'));
  assert.equal(view.rows[2].duration,'같은 AI 제안 검증');
});

test('overall result includes all three decisions and cannot call a guarded difference agreement',()=>{
  const result=comparison();result.validated_inference.action='REJECT';
  const view=ui.reasoningPanelModel({...record(),reasoning_comparisons:[{status:'COMPLETED',selection_version:1,result}]});
  assert.equal(view.summaryValue,'판단 차이 있음');
  result.validated_inference.guard_status='REJECTED';
  assert.equal(ui.reasoningPanelModel({...record(),reasoning_comparisons:[{status:'COMPLETED',selection_version:1,result}]}).summaryValue,'AI 제안 거부');
});

test('current overall result excludes old selection comparisons and distinguishes unfinished results',()=>{
  const r={...record(),reasoning_comparisons:[{status:'COMPLETED',selection_version:1,result:comparison()}]};
  r.selection_version=2;
  assert.equal(ui.reasoningPanelModel(r).summaryValue,'비교 전');
  r.reasoning_comparisons.push({status:'UNKNOWN',selection_version:2});
  assert.equal(ui.reasoningPanelModel(r).summaryValue,'결과 확인 대기');
  r.reasoning_comparisons[1].status='UNAVAILABLE';
  assert.equal(ui.reasoningPanelModel(r).summaryValue,'비교 미완료');
});
