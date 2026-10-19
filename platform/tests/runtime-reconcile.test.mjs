import test from 'node:test';import assert from 'node:assert/strict';import {stopOwnedRuntime,reconcileOwnedRuntime} from '../server/runtime-reconcile.mjs';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
test('injected watcher journal receives stop request without touching production watcher state',async()=>{
 const journalDirectory=await mkdtemp(join(tmpdir(),'khu-watcher-isolated-'));
 await mkdir(join(journalDirectory,'watcher.lock'));await writeFile(join(journalDirectory,'watcher.lock/owner.json'),JSON.stringify({nonce:'fixture-nonce'}));
 await stopOwnedRuntime({journalDirectory,discovery:async()=>{},store:{list:async()=>[]},clients:{groupList:async()=>[]},services:{journal:{list:async()=>[]}}});
 assert.deepEqual(JSON.parse(await readFile(join(journalDirectory,'watcher-stop.json'))),{nonce:'fixture-nonce'});
});
test('shutdown stops only receipt-bound groups and retains pending cleanup watcher',async()=>{
 let r={flow_id:'flow-owned',input:{execution_target:'local'},selected_candidate_id:'khu-local-pc',replica_group:{group_id:'group-owned',target_profile_id:'khu-local-pc'},execution:{status:'RUNNING'}},stopped=[];
 const owned={group_id:'group-owned',flow_id:r.flow_id,target_profile_id:'khu-local-pc',operation_state:'IDLE',actual_replicas:1},foreign={...owned,group_id:'group-foreign',flow_id:'flow-foreign'};
 const groups=[owned,foreign],result=await stopOwnedRuntime({discovery:async()=>{},store:{list:async()=>[r],update:async(_,f)=>r=f(r)},clients:{groupList:async()=>groups,groupStop:async id=>{stopped.push(id);Object.assign(owned,{operation_state:'STOPPED',actual_replicas:0});return owned;}},services:{journal:{list:async()=>[{run_id:'run-owned',state:'CLEANUP_PENDING'}]},execution:{cleanup:async()=>{}}}});
 assert.deepEqual(stopped,['group-owned']);assert.equal(result.retain_cleanup_watcher,true);assert.equal(r.execution.status,'STOPPED');
});
test('shutdown cancels an approved two-VM campaign and retains supervision until parent cleanup settles',async()=>{
 const actions=[],parent={run_id:'run-campaign-owned',state:'ACTIVE'};
 const result=await stopOwnedRuntime({discovery:async()=>{},store:{list:async()=>[]},clients:{groupList:async()=>[]},services:{journal:{list:async()=>[]},campaigns:{list:async()=>[parent],action:async(id,action)=>{actions.push([id,action]);parent.state='CLEANUP_PENDING';},reconcile:async()=>{}}}});
 assert.deepEqual(actions,[['run-campaign-owned','stop']]);assert.equal(result.retain_cleanup_watcher,true);assert.deepEqual(result.pending_cloud_runs,['run-campaign-owned']);
});
test('reopen reconciles unknown actual state without replaying Agent decisions or spawning apps',async()=>{
 let r={flow_id:'flow-test',replica_group:{group_id:'group-owned',target_profile_id:'khu-local-pc'},execution:{status:'RUNNING',actual_replicas:2}};
 const cached={...r.replica_group,flow_id:r.flow_id,operation_state:'IDLE',actual_replicas:2};let checked=false;
 await reconcileOwnedRuntime({discovery:async()=>{},store:{list:async()=>[r],update:async(_,f)=>r=f(r)},clients:{groupList:async()=>[cached],groupStatus:async()=>{checked=true;return {...cached,operation_state:'UNKNOWN',actual_replicas:0};}}});assert.equal(checked,true);assert.equal(r.execution.status,'UNKNOWN');assert.equal(r.execution.actual_replicas,0);
});
test('provider cleaned Flow can never be revived by stale AppDeploy membership',async()=>{
 let r={flow_id:'flow-test',cloud:{state:'CLEANED',cleanup:{confirmed:true}},replica_group:{group_id:'group-owned',target_profile_id:'khu-vm-test'},execution:{status:'STOPPED',actual_replicas:0,source:'provider_termination'}};
 await reconcileOwnedRuntime({discovery:async()=>{},store:{list:async()=>[r],update:async(_,f)=>r=f(r)},clients:{groupList:async()=>[{...r.replica_group,flow_id:r.flow_id,operation_state:'IDLE',actual_replicas:1}],groupStatus:async()=>{throw Error('must not revive provider-terminated VM');}}});assert.equal(r.execution.status,'STOPPED');assert.equal(r.execution.actual_replicas,0);
});
test('shutdown skips remote stop only for exact provider-terminated and attachment-clean cloud group',async()=>{
 const group={group_id:'group-cloud',flow_id:'flow-cloud',target_profile_id:'khu-vm-cloud',app_version_id:'appver-cloud',operation_state:'UNKNOWN',actual_replicas:0};
 const receipt={flow_id:'flow-cloud',cloud_run_id:'run-cloud',cloud:{run_id:'run-cloud',state:'CLEANED'},replica_group:{group_id:group.group_id,target_profile_id:group.target_profile_id},appdeploy:{target_profile_id:group.target_profile_id},execution:{status:'STOPPED',source:'provider_termination'}};
 const run={run_id:'run-cloud',intent:{flow_id:receipt.flow_id},state:'CLEANED',app:{group_id:group.group_id,target_profile_id:group.target_profile_id,app_version_id:group.app_version_id},resource:{provider_instance_id:'i-cloud'},cleanup:{confirmed:true,attachments:{verified:true,remaining:[]}},last_provider_status:{state:'Terminated',instance_id:'i-cloud'}};
 let stops=0;
 const services={journal:{list:async()=>[run]},execution:{cleanup:async()=>{}}};
 const clients={groupList:async()=>[group],groupStop:async()=>{stops++;throw Error('remote stop must not run');}};
 const store={list:async()=>[receipt],update:async()=>{}};
 const journalDirectory=await mkdtemp(join(tmpdir(),'khu-watcher-isolated-'));
 const result=await stopOwnedRuntime({journalDirectory,store,clients,services,discovery:async()=>{}});
 assert.deepEqual(result.unknown_groups,[]);assert.equal(stops,0);
 run.cleanup.attachments.remaining=[{type:'volume',id:'vol-live'}];
 const blocked=await stopOwnedRuntime({journalDirectory,store,clients,services,discovery:async()=>{}});
 assert.deepEqual(blocked.unknown_groups,['group-cloud']);assert.equal(stops,1);
});
