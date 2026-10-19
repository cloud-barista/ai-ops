import test from 'node:test';import assert from 'node:assert/strict';
import * as ui from '../cloud-ui.mjs';
function fixture(){
 let tick=0,polls=0;const calls=[],id='flow-record',run='run-owned',instance='i-0123456789abcdef0';
 const current={flow_id:id,input:{execution_target:'cloud'},cloud:{run_id:run,state:'RUNNING',intent:{flow_id:id},resource:{provider_instance_id:instance}},execution:{status:'RUNNING'}};
 const api=async(path,value)=>{calls.push({path,value});if(path==='/cloud/readiness')return {pending_cleanup:[{flow_id:id,run_id:run,instance_id:instance,state:'RUNNING'}]};if(path==='/flows/'+id)return structuredClone(current);if(path.endsWith('/cleanup'))return {...current,cloud:{...current.cloud,state:'CLEANUP_PENDING'}};polls++;return {...current,execution:{status:'STOPPED'},cloud:{...current.cloud,state:'CLEANED',cleanup:{confirmed:true}}};};
 return {api,calls,current,clock:()=>tick,pause:async()=>{tick+=1000;}};
}
test('cancelling recording reset preserves the running VM and current experiment',async()=>{
 const f=fixture();const result=await ui.prepareAwsRecording(f.api,{confirmCleanup:()=>false,pause:f.pause,clock:f.clock});
 assert.equal(result.ready,false);assert.equal(result.cancelled,true);assert.equal(f.calls.some(c=>c.path.endsWith('/cleanup')),false);
});
test('recording preparation waits for exact owned VM cleanup and never creates or runs a new experiment',async()=>{
 assert.equal(typeof ui.prepareAwsRecording,'function');const f=fixture();const r=await ui.prepareAwsRecording(f.api,{pause:f.pause,clock:f.clock});
 assert.equal(r.ready,true);assert.equal(r.previous_flow_id,'flow-record');assert.equal(f.calls.filter(x=>x.path.endsWith('/cleanup')).length,1);assert.equal(f.calls.some(x=>x.path==='/flows'||x.path.endsWith('/create')),false);
});
test('an empty recording environment is ready without executing any stage for the user',async()=>{
 assert.equal(typeof ui.prepareAwsRecording,'function');const calls=[];const r=await ui.prepareAwsRecording(async path=>{calls.push(path);return {pending_cleanup:[]};});assert.equal(r.ready,true);assert.deepEqual(calls,['/cloud/readiness']);
});
test('ambiguous, campaign and preparing resources cannot be cleared by the recording shortcut',async()=>{
 assert.equal(typeof ui.prepareAwsRecording,'function');
 for(const runs of [[{kind:'vm_count_campaign'}],[{flow_id:'flow-one'},{flow_id:'flow-two'}],[{flow_id:'flow-one',state:'PREPARING'}]]){
  let writes=0;await assert.rejects(ui.prepareAwsRecording(async(path,value)=>{if(value!==undefined)writes++;return {pending_cleanup:runs};}),/recording_scope_conflict/);assert.equal(writes,0);
 }
});
test('an uncertain cleanup reply is reconciled without submitting another deletion or creating a VM',async()=>{
 assert.equal(typeof ui.prepareAwsRecording,'function');const f=fixture(),base=f.api;
 const r=await ui.prepareAwsRecording(async(path,value)=>{if(path.endsWith('/cleanup')){f.calls.push({path,value});throw Error('network_unknown');}return base(path,value);},{clock:f.clock,pause:f.pause});assert.equal(r.ready,true);assert.equal(f.calls.filter(x=>x.path.endsWith('/cleanup')).length,1);
});
test('a changed VM identity is rejected before a cleanup request',async()=>{
 const f=fixture(),base=f.api;await assert.rejects(ui.prepareAwsRecording(async(path,value)=>{const r=await base(path,value);if(path==='/flows/flow-record')r.cloud.resource.provider_instance_id='i-fffffffffffffffff';return r;}),/recording_scope_conflict/);assert.equal(f.calls.some(c=>c.path.endsWith('/cleanup')),false);
});
test('unconfirmed cleanup times out without enabling a new recording execution',async()=>{
 const f=fixture(),base=f.api;await assert.rejects(ui.prepareAwsRecording(async(path,value)=>path.endsWith('/cloud/status')?structuredClone(f.current):base(path,value),{clock:f.clock,pause:f.pause,timeoutMs:2000}),/recording_reset_timeout/);assert.equal(f.calls.filter(x=>x.path.endsWith('/cleanup')).length,1);assert.equal(f.calls.some(c=>c.path.endsWith('/create')),false);
});
