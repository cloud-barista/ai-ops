import test from 'node:test';import assert from 'node:assert/strict';
const module=await import('../server/cloud-rejection.mjs').catch(()=>({}));
import {createCloudExecution,activeCloudRun} from '../server/cloud-execution.mjs';
import {cloudPreparationLabel,cloudPreparationFailed} from '../cloud-state.mjs';
const run={run_id:'run-owned',intent:{infra_id:'khu-poc-owned',intent_sha256:'a'.repeat(64),candidate_id:'g6.2xlarge'},review:{image_id:'ami-owned'},approval:{consumed_at:'2026-10-16T09:20:30Z'}};
function fixture(){let events=[{eventID:'event-owned',eventTime:'2026-10-16T09:20:41Z',errorCode:'Server.InsufficientInstanceCapacity',requestParameters:{instanceType:'g6.2xlarge',instancesSet:{items:[{imageId:'ami-owned'}]},tagSpecificationSet:{items:[{tags:[{key:'Name',value:'tbowned12345678901234'}]}]}},responseElements:null}],instances=[];return {run,bridge:async()=>({...run.intent,provider_name:'tbowned12345678901234',reason:'INSUFFICIENT_INSTANCE_CAPACITY'}),provider:async(service,action)=>action==='lookup-events'?{Events:events.map(e=>({CloudTrailEvent:JSON.stringify(e)}))}:action==='describe-instances'?{Reservations:[{Instances:instances}]}:action==='describe-volumes'?{Volumes:[]}:{Addresses:[]},mutate(fn){fn(events,instances);}};}
test('only exact owned capacity-rejected launches with no provider resources can settle as not created',async()=>{
 assert.equal(typeof module.auditRejectedCreation,'function');let f=fixture();const proof=await module.auditRejectedCreation(f);assert.equal(proof.confirmed,true);assert.equal(proof.not_created,true);assert.deepEqual(proof.event_ids,['event-owned']);
 for(const change of [(e)=>{e[0].requestParameters.tagSpecificationSet.items[0].tags[0].value='another';},e=>{e[0].errorCode=undefined;e[0].responseElements={instancesSet:{items:[{instanceId:'i-owned'}]}};},(e,i)=>{i.push({InstanceId:'i-owned'});},e=>{e[0].requestParameters.instancesSet.items[0].imageId='ami-other';}]){f=fixture();f.mutate(change);await assert.rejects(module.auditRejectedCreation(f));}
});
test('a verified provider rejection clears only failed metadata and releases cleanup without a fake instance',async()=>{
 let value={...structuredClone(run),state:'CLEANUP_PENDING'},clears=0;const f=fixture(),proof=await module.auditRejectedCreation(f);
 const journal={read:async()=>value,update:async(id,fn)=>(value=fn(value))};
 const execution=createCloudExecution({journal,client:{rejectedCreation:async()=>proof,clearRejected:async()=>{clears++;return {metadata_deleted:true};},status:async()=>{throw Error('resource_missing');}}});
 const result=await execution.cleanup(run.run_id);assert.equal(result.state,'CREATE_REJECTED');assert.equal(result.cleanup.not_created,true);assert.equal(result.resource,undefined);assert.equal(activeCloudRun(result),false);assert.equal(clears,1);await execution.cleanup(run.run_id);assert.equal(clears,1);
 const receipt={input:{execution_target:'cloud'},cloud:result};assert.equal(cloudPreparationFailed(receipt),true);assert.equal(cloudPreparationLabel(receipt),'VM 생성 거절 · 생성 자원 없음 확인');
});
