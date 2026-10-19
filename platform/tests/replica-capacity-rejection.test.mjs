import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createWorkflowStore} from '../server/workflow-store.mjs';import {createReplicaExecutionRoutes} from '../server/replica-execution.mjs';import {operationErrorModel} from '../view-model.mjs';
async function fixture(t,{changed=false,diagnosed=true}={}){
 const directory=await mkdtemp(join(tmpdir(),'khu-replica-capacity-'));t.after(()=>rm(directory,{recursive:true,force:true}));const store=createWorkflowStore(directory);
 const m={application:{app_id:'app',app_version:'1'},desired_infrastructure:{node_count:1,cpu_cores_per_node:2,memory_mib_per_node:2048,storage_gib_per_node:20},inference_configuration:{replicas:1,max_batch_size:1}};
 const revision=(number,replicas)=>({revision:number,deployment_request:{data:{deployment_request:{deployment_manifest:{...m,inference_configuration:{...m.inference_configuration,replicas}}}}}});
 const f={correlation_id:'flow-test-s1',trace_id:'trace',profile_id:'profile',decision:{action:'DEPLOY',selected_candidate_id:'khu-local-pc'},deployment_plan:{selected_candidate_id:'khu-local-pc'},manifest_revisions:[revision(1,1),revision(2,2)],operation_agent_execution:{status:'completed',scaling_guard:{status:'APPROVED'}},scaling_decision:{desired_replicas:2}};
 const group={flow_id:'flow-test',group_id:'group-test',app_version_id:'app',target_profile_id:'khu-local-pc',operation_state:'IDLE',applied_revision:1,actual_replicas:1,replicas:[{status:'RUNNING',runtime_id:'local-process-123'}]};
 await store.write({flow_id:'flow-test',trace_id:'trace',selection_version:1,input:{execution_target:'local',replica_group_enabled:true},application_profile:{profile_id:'profile'},selected_candidate_id:'khu-local-pc',geon:{...f,guard:{status:'APPROVED'}},geon_flow_id:'flow-test-s1',feedback:{geon:f},appdeploy:{group_schema:1,group_id:group.group_id,app_version_id:'app',target_profile_id:'khu-local-pc'},replica_group:group,execution:{status:'RUNNING'},execution_mode:'local_process',history:[]});
 let reads=0;const clients={groupStatus:async()=>({...group,operation_state:changed&&reads++?'UNKNOWN':'IDLE'}),flow:async()=>f,groupApply:async()=>{throw Object.assign(Error('provider_unavailable'),diagnosed?{appdeploy_diagnostic:{operation:'deployment-groups',http_status:409,code:'RESOURCE_INSUFFICIENT'}}:{});}};
 const route=createReplicaExecutionRoutes(),apply=()=>route({request:{method:'POST'},path:'/api/v1/flows/flow-test/appdeploy/apply-revision',value:{selection_version:1,expected_applied_revision:1},store,clients});return {store,apply};
}
test('a confirmed capacity rejection preserves the running replica and reports the constraint',async t=>{
 const f=await fixture(t);await assert.rejects(f.apply(),e=>e.code==='replica_capacity_insufficient'&&e.status===409);
 const r=await f.store.read('flow-test');assert.equal(r.replica_group.operation_state,'IDLE');assert.equal(r.execution.status,'RUNNING');assert.equal(r.replica_group.actual_replicas,1);assert.equal(r.replica_apply_rejection.code,'RESOURCE_INSUFFICIENT');assert.equal(r.history.at(-1).event,'replica_change_rejected');
 const view=operationErrorModel('replica_capacity_insufficient',{operation:'execution'});assert.match(view.detail,/자원|메모리/);assert.match(view.detail,/유지/);
});
test('a resource error after a group mutation remains uncertain rather than claiming a harmless rejection',async t=>{
 const f=await fixture(t,{changed:true});await assert.rejects(f.apply(),e=>e.code==='group_unknown');assert.equal((await f.store.read('flow-test')).replica_group.operation_state,'UNKNOWN');
});
test('an unclassified lost response still requires reconciliation',async t=>{
 const f=await fixture(t,{diagnosed:false});await assert.rejects(f.apply(),e=>e.code==='group_unknown');
});
