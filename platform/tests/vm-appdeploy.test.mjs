import test from 'node:test';import assert from 'node:assert/strict';import * as vmApp from '../server/vm-appdeploy.mjs';
const {validateVmRegistration,prepareVmApp,vmSshSettleMs}=vmApp;
test('read-only bootstrap status retries a transient SSH reset and accepts the later READY proof',async()=>{
 assert.equal(typeof vmApp.pollVmBootstrapStatus,'function');
 let calls=0;const delays=[],retries=[];
 const proof=await vmApp.pollVmBootstrapStatus({client:{guestBootstrapStatus:async()=>{
  if(++calls===1)throw Object.assign(Error('provider_unavailable'),{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET'}});
  return {state:'READY'};
 }},context:{},deadlineAt:new Date(Date.now()+900000).toISOString(),requirePreparing:async()=>{},pause:async ms=>{delays.push(ms);},onRetry:async r=>retries.push(r)});
 assert.equal(proof.state,'READY');assert.equal(calls,2);assert.deepEqual(delays,[15000]);assert.equal(retries[0].attempt,1);
});
test('bootstrap status never retries authentication or unlimited SSH failures',async()=>{
 assert.equal(typeof vmApp.pollVmBootstrapStatus,'function');
 let calls=0;const auth=Object.assign(Error('provider_unavailable'),{diagnostic_code:'guest_ssh_authentication_failed'});
 await assert.rejects(vmApp.pollVmBootstrapStatus({client:{guestBootstrapStatus:async()=>{calls++;throw auth;}},context:{},deadlineAt:new Date(Date.now()+900000).toISOString(),requirePreparing:async()=>{},pause:async()=>{}}),/provider_unavailable/);
 assert.equal(calls,1);
 const reset=Object.assign(Error('provider_unavailable'),{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET'}});
 calls=0;await assert.rejects(vmApp.pollVmBootstrapStatus({client:{guestBootstrapStatus:async()=>{calls++;throw reset;}},context:{},deadlineAt:new Date(Date.now()+900000).toISOString(),requirePreparing:async()=>{},pause:async()=>{}}),/provider_unavailable/);
 assert.equal(calls,4);
});
test('SSH settling requires explicit 180 second opt-in',()=>{
 assert.equal(vmSshSettleMs(undefined),0);
 assert.equal(vmSshSettleMs('0'),0);
 assert.equal(vmSshSettleMs('180000'),180000);
 assert.throws(()=>vmSshSettleMs('180'),/invalid_ssh_settle_time/);
});
const run={state:'PREPARING',resource:{provider_instance_id:'i-0123456789abcdef0'},intent:{intent_sha256:'a'.repeat(64),candidate_id:'m6a.2xlarge'},artifact_sha256:'b'.repeat(64)};
const credential={private_key:'fixture-private',user:'ubuntu',host:'34.1.2.3',host_key_fingerprint:'SHA256:abcde'};
const proof={execution_mode:'ssh_guest',state:'READY',provider_instance_id:run.resource.provider_instance_id,intent_sha256:run.intent.intent_sha256,artifact_sha256:run.artifact_sha256,forward_finite:true,cpu_cores:8,memory_bytes:32*1073741824,storage_bytes:25*1073741824};
test('missing pinned host key is rejected before AppDeploy registration',()=>assert.throws(()=>validateVmRegistration({run,credential:{...credential,host_key_fingerprint:''},proof}),/host_key/));
test('dry run can never be classified as real VM app success',()=>assert.throws(()=>validateVmRegistration({run,credential,proof:{...proof,execution_mode:'dry-run'}}),/actual_guest/));
test('GPU forward requires CUDA model and input devices',()=>assert.throws(()=>validateVmRegistration({run:{...run,intent:{...run.intent,candidate_id:'g6.2xlarge'}},credential,proof:{...proof,gpu_count:1,cuda_available:true,model_device:'cpu',input_device:'cuda:0'}}),/cuda_forward/));
test('cleanup started during preparation can never be overwritten or start bootstrap',async()=>{
 let current={...run,state:'CLEANUP_PENDING',resource:{...run.resource,attachment_ids:{captured_at:'now'}}},started=false;
 const journal={read:async()=>current,update:async(_,f)=>current=f(current)};
 await assert.rejects(prepareVmApp({run:current,journal,client:{guestGuard:async()=>{},guestBootstrap:async()=>{started=true;}},artifact:{sha256:'b'.repeat(64)}}),/cleanup_in_progress/);
 assert.equal(current.state,'CLEANUP_PENDING');assert.equal(started,false);
});
test('bootstrap failure records its stage and safe reason before VM cleanup can erase the current error',async()=>{
 let current={...run,state:'CREATED',run_id:'run-test',deadline_at:new Date(Date.now()+900000).toISOString(),resource:{...run.resource,attachment_ids:{captured_at:'now'}}};
 const journal={read:async()=>current,update:async(_,f)=>current=f(current)};
 await assert.rejects(prepareVmApp({run:current,journal,client:{guestGuard:async()=>({armed:true}),guestBootstrap:async()=>{throw Error('password=DO_NOT_PRINT');}},artifact:{sha256:'b'.repeat(64),payload:{}}}));
 assert.equal(current.failure.stage,'BOOTSTRAP_START');assert.equal(current.failure.code,'cloud_app_prepare_failed');
 assert.equal(JSON.stringify(current.failure).includes('DO_NOT_PRINT'),false);
 current={...current,state:'CLEANED',error_code:null};assert.equal(current.failure.stage,'BOOTSTRAP_START');
});
test('AppDeploy registration failure records only operation and HTTP status',async()=>{
 let current={...run,state:'CREATED',run_id:'run-test',deadline_at:new Date(Date.now()+900000).toISOString(),resource:{...run.resource,attachment_ids:{captured_at:'now'}}};
 const journal={read:async()=>current,update:async(_,f)=>current=f(current)};
 const error=Object.assign(Error('provider_unavailable'),{appdeploy_diagnostic:{operation:'target-profiles',http_status:400,body:'private_key=DO_NOT_PRINT'}});
 await assert.rejects(prepareVmApp({run:current,journal,client:{guestGuard:async()=>{},guestBootstrap:async()=>{},guestBootstrapStatus:async()=>proof,credential:async()=>credential},clients:{registerVmApp:async()=>{throw error;}},receipt:{},artifact:{sha256:run.artifact_sha256,payload:{}}}),/provider_unavailable/);
 assert.equal(current.failure.stage,'APP_REGISTRATION');
 assert.deepEqual(current.failure.appdeploy_diagnostic,{operation:'target-profiles',http_status:400});
 assert.equal(JSON.stringify(current.failure).includes('DO_NOT_PRINT'),false);
});
test('SSH phase evidence remains in the durable failure after cleanup without raw remote output',async()=>{
 let current={...run,state:'CREATED',run_id:'run-test',deadline_at:new Date(Date.now()+900000).toISOString(),resource:{...run.resource,attachment_ids:{captured_at:'now'}}};
 const journal={read:async()=>current,update:async(_,f)=>current=f(current)};
 const error=Object.assign(Error('provider_unavailable'),{diagnostic_code:'guest_ssh_connection_failed',ssh_diagnostic:{phase:'TCP_CONNECTED',reason:'RESET',stderr:'private_key=DO_NOT_PRINT'}});
 await assert.rejects(prepareVmApp({run:current,journal,client:{guestGuard:async()=>({armed:true}),guestBootstrap:async()=>{throw error;}},artifact:{sha256:'b'.repeat(64),payload:{}}}));
 assert.deepEqual(current.failure.ssh_diagnostic,{phase:'TCP_CONNECTED',reason:'RESET'});
 current={...current,state:'CLEANED',error_code:null};assert.equal(current.failure.ssh_diagnostic.phase,'TCP_CONNECTED');assert.ok(!JSON.stringify(current.failure).includes('DO_NOT_PRINT'));
});
test('opt-in SSH settling waits before starting bootstrap on a newly created VM',async()=>{
 let current={...run,state:'CREATED',run_id:'run-settle',deadline_at:new Date(Date.now()+900000).toISOString(),resource:{...run.resource,attachment_ids:{captured_at:'now'}}};
 const journal={read:async()=>current,update:async(_,f)=>current=f(current)};
 const events=[];
 await assert.rejects(prepareVmApp({run:current,journal,client:{
  guestGuard:async()=>{events.push('guard');return {armed:true};},
  guestBootstrap:async()=>{events.push('bootstrap');throw Error('after_settle');},
 },artifact:{sha256:'b'.repeat(64),payload:{}},sshSettleMs:180000,pause:async ms=>{events.push(['settle',ms,current.preparation.stage]);}}),/after_settle/);
 assert.deepEqual(events,['guard',['settle',180000,'GUEST_SSH_SETTLE'],'bootstrap']);
});
test('cleanup during SSH settling prevents bootstrap from starting',async()=>{
 let current={...run,state:'CREATED',run_id:'run-settle-cleanup',deadline_at:new Date(Date.now()+900000).toISOString(),resource:{...run.resource,attachment_ids:{captured_at:'now'}}};
 const journal={read:async()=>current,update:async(_,f)=>current=f(current)};
 let started=false;
 await assert.rejects(prepareVmApp({run:current,journal,client:{guestGuard:async()=>({armed:true}),guestBootstrap:async()=>{started=true;}},
  artifact:{sha256:'b'.repeat(64),payload:{}},sshSettleMs:180000,pause:async()=>{current={...current,state:'CLEANUP_PENDING'};}}),/cleanup_in_progress/);
 assert.equal(started,false);
});
