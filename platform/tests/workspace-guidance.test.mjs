import test from 'node:test';
import assert from 'node:assert/strict';
import * as views from '../view-model.mjs';

function receipt() {
  return {flow_id:'flow-guide',input:{execution_target:'local'},application_profile:{app_id:'guide-app'},selected_candidate_id:'khu-local-pc',state:'DEPLOY_APPROVED',geon:{decision:{action:'DEPLOY'},guard:{status:'APPROVED'}}};
}
function running() {
  const r=receipt();
  r.execution={mode:'local_process',status:'RUNNING',actual_replicas:1};
  r.replica_group={operation_state:'IDLE',actual_replicas:1,applied_revision:1,replicas:[]};
  return r;
}
function approvedChange() {
  const r=running();
  r.feedback={geon:{scaling_decision:{action:'SCALE_OUT',current_replicas:1,desired_replicas:2},manifest_revisions:[{revision:2,desired_deployment_spec:{inference_configuration:{replicas:2}}}],operation_agent_execution:{scaling_guard:{status:'APPROVED'}}}};
  return r;
}
const guide=(r,options)=>{assert.equal(typeof views.workspaceGuidanceModel,'function');return views.workspaceGuidanceModel(r,options);};

test('a new local experiment explains the mechanism without claiming an existing execution',()=>{
  const v=guide(null,{page:'application',target:'local'});
  assert.equal(v.visible,true);assert.match(v.route,/이 PC/);
  assert.match(v.status,/입력/);assert.match(v.io.process,/요구사항/);assert.match(v.io.output,/추천/);
});
test('an approved deployment plan is explicitly still before execution',()=>{
  const v=guide(receipt(),{page:'planning'});
  assert.match(v.status,/승인.*실행 전/);assert.equal(v.next.href,'#deployment');
  assert.match(v.detail,/별도/);assert.doesNotMatch(v.status,/실행 중/);
});
test('only a guard-approved newer revision asks the user to apply a change',()=>{
  const r=approvedChange(),before=structuredClone(r),v=guide(r,{page:'operation'});
  assert.equal(v.next.href,'#replica-panel');assert.match(v.status,/변경 적용 대기/);
  assert.match(v.detail,/실제 1개/);assert.match(v.detail,/목표 2개/);
  assert.deepEqual(r,before);
  r.feedback.geon.operation_agent_execution.scaling_guard.status='REJECTED';
  assert.notEqual(guide(r,{page:'operation'}).next?.href,'#replica-panel');
});
test('already-applied and KEEP decisions do not request a duplicate apply',()=>{
  const r=approvedChange();r.replica_group.applied_revision=2;r.replica_group.actual_replicas=2;
  assert.notEqual(guide(r,{page:'operation'}).next?.href,'#replica-panel');
  r.feedback.geon.scaling_decision={action:'KEEP',current_replicas:2,desired_replicas:2};
  assert.match(guide(r,{page:'operation'}).detail,/종료/);
});
test('unknown execution, plan and feedback responses direct reconciliation instead of resubmission',()=>{
  for(const mutate of [r=>{r.execution.status='UNKNOWN';},r=>{r.state='PLAN_UNKNOWN';},r=>{r.state='PLAN_PENDING';},r=>{r.feedback_state='UNKNOWN';},r=>{r.feedback_state='PENDING';}]){
    const r=running();mutate(r);const v=guide(r,{page:'operation'});
    assert.match(v.status,/확인 필요/);assert.match(v.detail,/다시.*요청하지/);
    assert.notEqual(v.next?.href,'#replica-panel');
  }
});
test('an in-progress replica change does not advertise another measurement or apply',()=>{
  for(const [state,label] of [['APPLYING','변경 중'],['STOPPING','종료 중']]){
    const r=approvedChange();r.replica_group.operation_state=state;
    const v=guide(r,{page:'operation'});assert.match(v.status,new RegExp(label));assert.equal(v.next,null);
  }
  const pending=approvedChange();pending.replica_group.operation_state='PENDING';
  const v=guide(pending,{page:'operation'});assert.match(v.status,/변경 결과 확인 중/);assert.equal(v.next.href,'#deployment');
});
test('terminated executions display zero actual processes and keep historical goals separate',()=>{
  const r=approvedChange();r.execution.status='STOPPED';r.replica_group.operation_state='STOPPED';r.replica_group.actual_replicas=0;
  const v=guide(r,{page:'operation'});
  assert.match(v.status,/종료/);assert.match(v.detail,/실제 0개/);
  assert.equal(v.next.href,'#events');assert.doesNotMatch(v.status,/적용 대기/);
});
test('a stopped receipt with no confirmed count cannot invent zero or reuse a stale count',()=>{
  const r=running();r.execution.status='STOPPED';r.execution.actual_replicas=1;
  r.replica_group.operation_state='STOPPED';r.replica_group.actual_replicas=null;
  const v=guide(r,{page:'operation'});
  assert.match(v.detail,/실제 실행 수는 확인되지/);assert.doesNotMatch(v.detail,/실제 [01]개/);
});
test('monitoring a different saved flow cannot suggest applying that flow to the active experiment',()=>{
  const r=approvedChange(),saved={...structuredClone(r),flow_id:'flow-other'};
  const v=guide(r,{page:'operation',monitorReceipt:saved});
  assert.match(v.status,/다른 실행의 저장 기록/);assert.equal(v.next,null);
});
test('saved-only monitoring uses the saved execution route even without an active experiment',()=>{
  const saved=running();saved.input.execution_target='cloud';saved.flow_id='flow-saved-cloud';
  for(const active of [null,receipt()]){
    const v=guide(active,{page:'operation',target:'local',monitorReceipt:saved});
    assert.match(v.route,/AWS/);assert.match(v.status,/저장 기록/);assert.equal(v.next,null);
  }
});
test('failed applications are not labelled as running or ready for another measurement',()=>{
  for(const status of ['RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED','EXTERNAL_API_FAILED']){
    const r=running();r.execution.status=status;const v=guide(r,{page:'operation'});
    assert.match(v.status,/실패/);assert.doesNotMatch(v.status,/실행 중/);assert.equal(v.next.href,'#deployment');
  }
});
test('VM creation and cleanup states are distinguished before an application receipt exists',()=>{
  for(const [state,label] of [['CREATE_REQUESTED','생성 중'],['CREATED','준비 중'],['PREPARING','준비 중'],['CREATE_UNKNOWN','확인 필요'],['CLEANUP_PENDING','정리 확인 중']]){
    const r=receipt();r.input.execution_target='cloud';r.cloud={state};
    const v=guide(r,{page:'deployment'});assert.match(v.status,new RegExp(label));
    assert.doesNotMatch(v.status,/계획 승인.*실행 전/);
  }
  const r=running();r.input.execution_target='cloud';r.execution.status='STOPPED';r.cloud={state:'RUNNING',app:{group_id:'group-cloud'}};
  assert.match(guide(r,{page:'deployment'}).status,/VM 종료 확인 필요/);
});
test('edited inputs invalidate old successful steps in guidance',()=>{
  const v=guide(receipt(),{page:'infrastructure',inputDirty:true});
  assert.match(v.status,/다시 분석/);assert.equal(v.next.href,'#application');
});
test('a slow LLM request shows real elapsed time and no fabricated progress or timeout',()=>{
  const v=guide(receipt(),{page:'application',operation:'analysis',elapsedSeconds:64});
  assert.match(v.status,/64초/);assert.match(v.detail,/모델 준비/);assert.equal(v.next,null);
  assert.doesNotMatch(v.status,/%|완료/);
});
test('historical and cloud routes cannot be mistaken for a current local run',()=>{
  assert.match(guide(null,{page:'application',target:'historical'}).route,/과거.*모의/);
  const r=receipt();r.input.execution_target='cloud';const v=guide(r,{page:'deployment'});
  assert.match(v.route,/AWS/);assert.match(v.detail,/비용.*승인/);
});
test('overview and validation do not acquire an extra workflow control panel',()=>{
  for(const page of ['overview','validation','events'])assert.equal(guide(receipt(),{page}).visible,false);
});
test('error recovery distinguishes analysis retries from uncertain execution',()=>{
  assert.equal(typeof views.operationErrorModel,'function');
  const analysis=views.operationErrorModel('provider_unavailable',{operation:'analysis'});
  assert.match(analysis.title,/분석/);assert.match(analysis.detail,/직접 조건 지정/);
  const unknown=views.operationErrorModel('deployment_unknown',{operation:'execution'});
  assert.match(unknown.detail,/다시.*요청하지/);assert.equal(unknown.href,'#deployment');
  const stale=views.operationErrorModel('stale_selection',{operation:'plan'});
  assert.match(stale.detail,/현재.*선택/);assert.equal(stale.href,'#infrastructure');
});

test('applying a revision leads back to measurement for the changed execution',()=>{
 const r=approvedChange();r.replica_group={...r.replica_group,group_id:'group-loop',membership_epoch:2,applied_revision:2,actual_replicas:2};r.measurement={group_id:'group-loop',membership_epoch:1};
 const v=guide(r,{page:'operation'});assert.match(v.status,/재측정/);assert.equal(v.next.href,'#app-measure');assert.match(v.detail,/Revision 2/);
});
