import test from 'node:test';
import assert from 'node:assert/strict';
import * as panels from '../panels.mjs';
const fixture=()=>({flow_id:'flow-results',trace_id:'trace',selection_version:1,state:'DEPLOY_APPROVED',input:{execution_target:'local',application:{app_id:'my-app'},model:{model_id:'bert'}},application_profile:{profile_id:'profile',requirements:{compute:{cpu_cores_min:2,memory_mib_min:1024}}},analysis:{mode:'llm',actual_model:'qwen3.5:4b'},resource_recommendation:{profile_id:'profile'},candidates:{all:[{candidate_id:'khu-local-pc',name:'이 PC',selectable:true}]},selected_candidate_id:'khu-local-pc',geon:{profile_id:'profile',trace_id:'trace',correlation_id:'flow-results-s1',decision:{action:'DEPLOY',selected_candidate_id:'khu-local-pc'},guard:{status:'APPROVED'}},history:[]});
const running=()=>({...fixture(),execution:{status:'RUNNING',mode:'local_process',actual_replicas:1,applied_revision:1},replica_group:{group_id:'group-test',operation_state:'IDLE',actual_replicas:1,applied_revision:1,membership_epoch:1}});
const observation=(source='local_probe')=>({flow_id:'flow-results',group_id:'group-test',source,membership_epoch:1,sample_count:64,ended_at:'2026-10-19T00:00:00Z',metrics:{inference:{latency_p95_ms:25,error_rate_percent:0}}});
const result=(r,opts)=>{assert.equal(typeof panels.experimentResultModel,'function');return panels.experimentResultModel(r,opts);};

test('an empty result prompts the requirements step and never borrows historical validation',()=>{
 const v=result(null);assert.equal(v.empty,true);assert.equal(v.stages.length,5);assert.ok(v.stages.every(s=>s.state==='waiting'));assert.doesNotMatch(v.status,/통과|성공|완료/);
});
test('an approved plan remains explicitly before actual execution',()=>{
 const v=result(fixture());assert.match(v.status,/실행 전/);assert.equal(v.stages[2].state,'recorded');assert.equal(v.stages[3].state,'waiting');assert.equal(v.actualReplicas,null);
});
test('a stopped actual flow keeps its final count distinct from prior measurements and decisions',()=>{
 const r=running();r.execution={...r.execution,status:'STOPPED',actual_replicas:0,applied_revision:3};r.replica_group={...r.replica_group,operation_state:'STOPPED',actual_replicas:0,applied_revision:3,membership_epoch:4};r.measurement={...observation('prometheus'),membership_epoch:3};r.feedback={observation:r.measurement,geon:{scaling_decision:{action:'KEEP',current_replicas:1,desired_replicas:1},operation_agent_execution:{scaling_guard:{status:'APPROVED'}}}};
 r.history=[1,2,1,0].map((actual_replicas,i)=>({event:i===0?'group_running':i===3?'group_stopped':'revision_applied',group_id:'group-test',actual_replicas,revision:[1,2,3,3][i]}));
 const v=result(r);assert.equal(v.actualReplicas,0);assert.match(v.status,/종료 확인/);assert.equal(v.replicaHistory,'1 → 2 → 1 → 0');assert.match(v.stages[4].detail,/종료 전/);assert.match(v.stages[4].detail,/Prometheus/);assert.doesNotMatch(v.status,/검증 통과/);
});
test('a new approved revision is an unapplied goal, never an already running replica count',()=>{
 const r=running();r.measurement=observation();r.feedback={geon:{manifest_revisions:[{revision:2,desired_deployment_spec:{inference_configuration:{replicas:2}}}],scaling_decision:{action:'SCALE_OUT',current_replicas:1,desired_replicas:2},operation_agent_execution:{scaling_guard:{status:'APPROVED'}}}};
 const v=result(r);assert.match(v.status,/적용 대기/);assert.equal(v.actualReplicas,1);assert.equal(v.desiredReplicas,2);assert.equal(v.appliedRevision,1);
});
test('older epoch observations are displayed as preceding the applied change',()=>{
 const r=running();r.measurement=observation();r.replica_group.membership_epoch=2;r.replica_group.applied_revision=2;r.execution.applied_revision=2;
 const v=result(r);assert.match(v.status,/재측정/);assert.match(v.stages[4].detail,/변경 전/);
});
test('unknown execution and an unconfirmed stop cannot invent zero replicas or completion',()=>{
 for(const status of ['UNKNOWN','STOPPED']){const r=fixture();r.execution={status,mode:'local_process'};const v=result(r);assert.equal(v.actualReplicas,null);assert.match(v.stages[3].detail,/확인 필요/);assert.doesNotMatch(v.status,/종료 확인/);}
});
test('historical mock execution remains visibly separate from actual execution',()=>{
 const r={...fixture(),input:{...fixture().input,execution_target:'historical'},execution:{mode:'mock',status:'RUNNING'}};const v=result(r);assert.match(v.status,/모의/);assert.match(v.stages[3].detail,/실제.*아닙니다/);assert.equal(v.actualReplicas,null);
});
test('foreign flow observations and mismatched plan identity do not appear as confirmed current results',()=>{
 const r=running();r.measurement={...observation(),flow_id:'flow-other'};r.geon.profile_id='profile-other';const v=result(r);
 assert.equal(v.stages[2].state,'attention');assert.equal(v.stages[4].state,'attention');assert.match(v.status,/연결 확인/);
});
test('an analysis failure, a rejected plan and GPU bootstrap failure retain their actual failure stage',()=>{
 const rejected=fixture();rejected.geon.guard.status='REJECTED';assert.equal(result(rejected).stages[2].state,'attention');
 const stopped={...fixture(),state:'STOPPED',application_profile:null,geon:null};assert.match(result(stopped).status,/분석.*중단/);
 const cloud={...fixture(),input:{...fixture().input,execution_target:'cloud'},cloud:{state:'CLEANED',failure:{stage:'BOOTSTRAP_START'},cleanup:{confirmed:true}},execution:{mode:'cloud_vm',source:'provider_termination',status:'STOPPED',actual_replicas:0}};
 assert.match(result(cloud).status,/배포 준비 실패/);assert.equal(result(cloud).stages[3].state,'attention');
});
test('edited inputs and a new analysis never label the previous receipt as a new result',()=>{
 assert.match(result(running(),{inputDirty:true}).status,/조건 변경/);assert.match(result(running(),{operation:'analysis'}).status,/분석 중/);assert.equal(result(running(),{operation:'analysis'}).empty,true);
});
