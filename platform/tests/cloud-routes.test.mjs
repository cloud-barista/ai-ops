import test from 'node:test';import assert from 'node:assert/strict';import {createCloudRoutes,safeRuntimeDiagnosisFailure} from '../server/cloud-routes.mjs';
test('a closed VM execution cannot reuse its prior registry and approved delivery binding',async()=>{
 const previous={run_id:'run-closed',state:'CLEANED',cleanup:{confirmed:true},resource:{provider_instance_id:'i-0123456789abcdef0'}};
 const r={flow_id:'flow-test',input:{execution_target:'cloud'},cloud_run_id:previous.run_id};let quoted=false;
 const route=createCloudRoutes({services:{journal:{read:async()=>previous},execution:{review:async()=>{quoted=true;}},config:{}}});
 await assert.rejects(route({request:{method:'POST'},path:'/api/v1/flows/flow-test/cloud/review',value:{},store:{read:async()=>r},clients:{}}),error=>error.code==='cloud_fresh_experiment_required');
 assert.equal(quoted,false);
});
test('runtime diagnosis failure retains only allowlisted SSH evidence',()=>{
 const error=Object.assign(Error('private-key-and-host'),{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET',detail:'private-key-and-host'}});
 assert.deepEqual(safeRuntimeDiagnosisFailure(error),{failed:true,diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET'}});
 assert.deepEqual(safeRuntimeDiagnosisFailure(Error('private-key-and-host')),{failed:true});
});
test('polling cloud status is read-only and never launches a VM or bootstrap',async()=>{
 const seen=[],run={run_id:'run-test',intent:{flow_id:'flow-test'},state:'CREATED'};let r={flow_id:'flow-test',input:{execution_target:'cloud'},cloud_run_id:run.run_id};const route=createCloudRoutes({services:{journal:{read:async()=>run},execution:{status:async()=>{seen.push('status');return run;},create:()=>seen.push('create')},config:{enabled:true}}});
 await route({request:{method:'GET'},path:'/api/v1/flows/flow-test/cloud/status',store:{read:async()=>r,update:async(_,f)=>r=f(r)},clients:{}});assert.deepEqual(seen,['status']);
});
test('provider-confirmed cleanup closes the Flow without fabricating AppDeploy state',async()=>{
 const run={run_id:'run-test',journal_revision:4,intent:{flow_id:'flow-test'},state:'CLEANED',cleanup:{confirmed:true,app_terminated_by_vm:true}};let r={flow_id:'flow-test',input:{execution_target:'cloud'},cloud_run_id:run.run_id,execution:{mode:'cloud_vm',execution_id:'group-owned',status:'UNKNOWN',actual_replicas:1}};
 const route=createCloudRoutes({services:{journal:{read:async()=>run},execution:{status:async()=>run},config:{}}});await route({request:{method:'GET'},path:'/api/v1/flows/flow-test/cloud/status',store:{read:async()=>r,update:async(_,f)=>r=f(r)},clients:{}});
 assert.equal(r.execution.status,'STOPPED');assert.equal(r.execution.actual_replicas,0);assert.equal(r.execution.source,'provider_termination');assert.equal(r.replica_group,undefined);
});

test('cleanup after bootstrap failure never fabricates an application execution and repairs the old synthetic record',async()=>{
 const run={run_id:'run-failed',intent:{flow_id:'flow-test'},state:'CLEANED',failure:{stage:'BOOTSTRAP_START',code:'provider_unavailable'},cleanup:{confirmed:true}};
 for(const execution of [undefined,{mode:'cloud_vm',source:'provider_termination',status:'STOPPED',actual_replicas:0}]){
  let r={flow_id:'flow-test',input:{execution_target:'cloud'},cloud_run_id:run.run_id,execution};
  const route=createCloudRoutes({services:{journal:{read:async()=>run},execution:{status:async()=>run},config:{}}});
  await route({request:{method:'GET'},path:'/api/v1/flows/flow-test/cloud/status',store:{read:async()=>r,update:async(_,f)=>r=f(r)},clients:{}});
  assert.equal(r.execution,undefined);assert.equal(r.appdeploy,undefined);assert.equal(r.cloud.cleanup.confirmed,true);assert.equal(r.cloud.failure.stage,'BOOTSTRAP_START');
 }
});
test('connection readiness lists missing configuration without exposing credential paths or invoking cloud actions',async()=>{
 const route=createCloudRoutes({services:{config:{enabled:false,ssh_key:'DO_NOT_EXPOSE_PRIVATE_PATH',image_ids:{cpu:'configured-cpu'}},journal:{list:async()=>[]},client:{preflight:()=>{throw Error('must not connect');}},execution:{}}});
 const result=await route({request:{method:'GET'},path:'/api/v1/cloud/readiness'});
 assert.deepEqual(result.data.checks,{ssh_key:true,cpu_image:true,gpu_image:false,provider_audit:false,execution_enabled:false});
 assert.equal(JSON.stringify(result).includes('DO_NOT_EXPOSE_PRIVATE_PATH'),false);
});
