import test from 'node:test';import assert from 'node:assert/strict';
const module=await import('../server/load-experiment.mjs').catch(()=>({}));
async function fixture({target='local',status='RUNNING',pending=false,slow=false}={}){
 assert.equal(typeof module.createLoadExperimentRoutes,'function');let r={flow_id:'flow-load',selection_version:1,input:{execution_target:target,model:{model_revision:'pinned'}},execution:{status},replica_group:{group_id:'group-load',operation_state:'IDLE'},feedback:{geon:{manifest_revisions:pending?[{revision:2}]:[]}},history:[]};
 const store={read:async()=>structuredClone(r),update:async(id,fn)=>{r=fn(structuredClone(r));return structuredClone(r);}};let calls=0,release;const barrier=new Promise(res=>release=res);
 const route=module.createLoadExperimentRoutes({maxRequests:32,maxSeconds:1,concurrency:2}),clients={groupInvoke:async()=>{calls++;if(slow)await barrier;return {result:{status_code:200,body:{flow_id:r.flow_id,model_revision:'pinned',model_inference:true}}};}};
 const call=(method='POST',value={selection_version:1,request_id:'11111111-2222-4333-8444-555555555555'},action='')=>route({request:{method},path:'/api/v1/flows/flow-load/load-test'+action,value,store,clients});
 return {call,store,calls:()=>calls,release};
}
test('bounded load job sends real requests and records completion without changing deployment',async()=>{
 const f=await fixture(),result=await f.call();assert.equal(result.status,202);for(let n=0;n<30&&(await f.store.read()).load_test.state==='RUNNING';n++)await new Promise(r=>setTimeout(r,10));const r=await f.store.read();assert.equal(r.load_test.state,'COMPLETED');assert.equal(r.load_test.completed_requests,32);assert.equal(r.load_test.errors,0);assert.equal(r.execution.status,'RUNNING');assert.equal(f.calls(),32);
});
test('a repeated click cannot start another concurrent or duplicate job',async()=>{
 const f=await fixture({slow:true});await f.call();await f.call();assert.ok(f.calls()<=2);await assert.rejects(f.call('POST',{selection_version:1,request_id:'22222222-2222-4333-8444-555555555555'}),e=>e.code==='load_test_running');await f.call('POST',{},'/stop');f.release();
});
test('cloud, stopped and stale selection inputs cannot receive local test traffic',async()=>{
 for(const options of [{target:'cloud'},{status:'STOPPED'}]){const f=await fixture(options);await assert.rejects(f.call(),e=>e.code==='running_local_group_required');assert.equal(f.calls(),0);}
 const f=await fixture();await assert.rejects(f.call('POST',{selection_version:2,request_id:'11111111-2222-4333-8444-555555555555'}),e=>e.code==='stale_selection');assert.equal(f.calls(),0);
});
test('browser cannot specify duration, concurrency or inference body',async()=>{
 const f=await fixture();await assert.rejects(f.call('POST',{selection_version:1,request_id:'11111111-2222-4333-8444-555555555555',concurrency:999}),e=>e.code==='invalid_load_request');assert.equal(f.calls(),0);
});
