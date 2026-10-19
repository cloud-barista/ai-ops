import assert from 'node:assert/strict';
import test from 'node:test';
const pipeline=await import('../pipeline.mjs').catch(error=>{if(error.code==='ERR_MODULE_NOT_FOUND')return {};throw error;});
const analyzed=()=>({state:'SELECTED',flow_id:'flow-progress',selection_version:1,application_profile:{},resource_recommendation:{},candidates:{primary:[{}]}});
const approved=()=>({...analyzed(),geon:{decision:{action:'DEPLOY'},guard:{status:'APPROVED'}}});
const states=view=>view.stages.map(stage=>stage.state);

test('pipeline starts waiting and uses the connected modules instead of claiming cloud execution',()=>{
  assert.equal(typeof pipeline.pipelineViewModel,'function');const view=pipeline.pipelineViewModel(null);
  assert.deepEqual(view.stages.map(s=>s.id),['llmop','resource','geon','mock','feedback']);assert.ok(states(view).every(s=>s==='waiting'));
  assert.ok(!JSON.stringify(view).includes('VM 생성 완료'));
});
test('a fresh local flow shows the deployment stage before a receipt exists',()=>{
  const v=pipeline.pipelineViewModel(null,{target:'local'});
  assert.equal(v.stages[3].module,'배포 실행');assert.equal(v.stages[3].href,'#deployment');assert.equal(v.stages[3].state,'waiting');
});
test('only confirmed profile and recommendation advance their stages',()=>{
  assert.equal(typeof pipeline.pipelineViewModel,'function');const partial={...analyzed(),state:'RECOMMENDATION_PENDING',resource_recommendation:null};
  assert.deepEqual(states(pipeline.pipelineViewModel(partial)).slice(0,3),['done','active','waiting']);
  assert.deepEqual(states(pipeline.pipelineViewModel(analyzed())).slice(0,3),['done','done','waiting']);
  assert.deepEqual(states(pipeline.pipelineViewModel(approved(),{operation:'analysis'})),['active','waiting','waiting','waiting','waiting']);
});
test('unknown and rejected plans never complete the execution stage',()=>{
  assert.equal(typeof pipeline.pipelineViewModel,'function');assert.equal(pipeline.pipelineViewModel({...analyzed(),state:'PLAN_PENDING'}).stages[2].state,'active');
  const unknown=pipeline.pipelineViewModel({...analyzed(),state:'PLAN_UNKNOWN'});assert.equal(unknown.stages[2].state,'attention');assert.equal(unknown.stages[3].state,'blocked');
  const rejected=pipeline.pipelineViewModel({...approved(),geon:{decision:{action:'REJECT'},guard:{status:'REJECTED'}}});assert.equal(rejected.stages[2].state,'attention');assert.equal(rejected.stages[3].state,'blocked');
});
test('Mock RUNNING is a recorded acceptance, and feedback has its own pending state',()=>{
  assert.equal(typeof pipeline.pipelineViewModel,'function');const receipt={...approved(),execution:{mode:'mock',status:'RUNNING'},feedback_state:'UNKNOWN'};
  const view=pipeline.pipelineViewModel(receipt);assert.equal(view.stages[3].state,'done');assert.match(view.stages[3].detail,/RUNNING/);assert.ok(!view.stages[3].detail.includes('COMPLETED'));assert.equal(view.stages[4].state,'attention');
  receipt.feedback_state='RECORDED';receipt.feedback={geon:{scaling_decision:{action:'KEEP'}}};assert.equal(pipeline.pipelineViewModel(receipt).stages[4].state,'done');
  assert.equal(pipeline.pipelineViewModel(receipt,{inputDirty:true}).stages[0].state,'attention');assert.ok(pipeline.pipelineViewModel(receipt,{inputDirty:true}).stages.slice(1).every(s=>s.state==='waiting'));
});
test('event log uses saved response timestamps and does not invent missing history',()=>{
  assert.equal(typeof pipeline.pipelineEvents,'function');const history=[{event:'profile_received',at:'2026-10-01T00:00:01Z'},{event:'resource_received',at:'2026-10-01T00:00:02Z'},{event:'selected',candidate_id:'demo-cpu8',at:'2026-10-01T00:00:03Z'}];
  const events=pipeline.pipelineEvents({history});assert.equal(events.length,3);assert.equal(events[0].at,history[0].at);assert.equal(events[1].label,'인프라 추천 결과 확인');assert.match(events[2].label,/demo-cpu8/);assert.deepEqual(pipeline.pipelineEvents({history:[]}),[]);
});
test('actual runtime is shown as deployment execution and failed execution is not a completed step',()=>{
  const r={...approved(),input:{execution_target:'local'},execution:{mode:'local_process',status:'RUNNING'}};
  let v=pipeline.pipelineViewModel(r);assert.equal(v.stages[3].module,'배포 실행');assert.equal(v.stages[3].state,'done');assert.match(v.stages[3].detail,/실제/);
  r.execution.status='RUNTIME_FAILED';v=pipeline.pipelineViewModel(r);assert.equal(v.stages[3].state,'attention');
});

test('reasoning comparison is visible in the same GEON progress stage without changing execution state',()=>{
  const r={...approved(),execution:{mode:'mock',status:'RUNNING'},feedback:{geon:{scaling_decision:{action:'KEEP'}}}};
  const view=pipeline.pipelineViewModel(r,{operation:'reasoning'});
  assert.equal(view.stages[2].state,'active');assert.match(view.currentLabel,/비교/);
  assert.equal(view.stages[3].state,'done');assert.equal(r.execution.status,'RUNNING');
  r.reasoning_comparisons=[{selection_version:1,status:'UNKNOWN'}];
  assert.equal(pipeline.pipelineViewModel(r).stages[2].state,'attention');
  r.selection_version=2;assert.equal(pipeline.pipelineViewModel(r).stages[2].state,'done');
});

test('a model comparison failure is an attention event rather than a successful result',()=>{
  const view=pipeline.pipelineEvents({history:[{event:'reasoning_unavailable',at:'2026-10-02T10:00:00Z'}]});
  assert.equal(view[0].state,'attention');
});

test('a cleaned VM that failed bootstrap never completes the AppDeploy progress step, including legacy receipts',()=>{
 const r={...approved(),input:{execution_target:'cloud'},cloud:{state:'CLEANED',cleanup:{confirmed:true},failure:{stage:'BOOTSTRAP_START'}},execution:{mode:'cloud_vm',source:'provider_termination',status:'STOPPED',actual_replicas:0}};
 const v=pipeline.pipelineViewModel(r);assert.equal(v.stages[3].state,'attention');assert.equal(v.confirmedCount,3);assert.match(v.currentLabel,/배포 준비 실패/);assert.match(v.stages[3].detail,/정리 확인/);
 r.cloud.app={group_id:'group-actually-deployed'};r.execution.execution_id='group-actually-deployed';
 assert.equal(pipeline.pipelineViewModel(r).stages[3].state,'done');
});

test('the connected audit trail includes refresh, running replicas, applied revision and stop confirmations',()=>{
 const history=['resources_refreshed','group_requested','group_running','replica_apply_requested','revision_applied','group_stopped'].map((event,i)=>({event,at:new Date(Date.UTC(2026,9,19,0,0,i)).toISOString(),revision:2,actual_replicas:event==='group_stopped'?0:2}));
 const events=pipeline.pipelineEvents({history});
 assert.equal(events.length,6);assert.equal(events[1].state,'requested');assert.equal(events[3].state,'requested');
 assert.match(events[4].detail,/Revision 2/);assert.match(events[4].detail,/2개/);assert.match(events[5].detail,/0개/);
});
