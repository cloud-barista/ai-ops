import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createCloudJournal} from '../server/cloud-journal.mjs';
import {createCloudExecution} from '../server/cloud-execution.mjs';

async function fixture(t,overrides={}){
 const dir=await mkdtemp(join(tmpdir(),'khu-cloud-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const journal=createCloudJournal(dir);const calls=[];
 const client={review:async()=>({hourly_usd:'0.3456',source:'fixture',checked_at:new Date().toISOString(),image_id:'ami-fixture'}),preflight:async()=>({ready:true}),create:async intent=>{calls.push('create');return {infra_id:intent.infra_id,provider_instance_id:'i-0123456789abcdef0',node_id:'app-1',spider_name:'spider-owned',connection_name:'aws-us-west-2',intent_sha256:intent.intent_sha256};},status:async()=>({state:'Running'}),terminate:async()=>{calls.push('terminate');return {};},providerStatus:async()=>({state:'Terminated',instance_id:'i-0123456789abcdef0'}),billableAttachments:async()=>({verified:true,remaining:[]}),...overrides};
 const x=createCloudExecution({journal,client,enabled:true,watcher:async()=>({healthy:true}),now:()=>new Date(),stopApp:async()=>{calls.push('stop-app');}});
 const intent={flow_id:'flow-test',selection_version:1,spec_sha256:'a'.repeat(64),candidate_id:'m6a.2xlarge',region:'us-west-2',max_lifetime_seconds:1800};
 async function ready(){let r=await x.review(intent);r=await x.approve(r.run_id,{cost_review_id:r.review.cost_review_id,intent_sha256:r.intent.intent_sha256});return x.preflight(r.run_id);}
 return {x,journal,client,calls,ready,intent};
}
test('create timeout never reposts and retains owned cleanup identity',async t=>{
 const f=await fixture(t,{create:async()=>{f.calls.push('create');throw Error('response lost');}});let r=await f.ready();await assert.rejects(f.x.create(r.run_id),/create_unknown/);r=await f.journal.read(r.run_id);assert.equal(r.state,'CREATE_UNKNOWN');assert.ok(r.intent.infra_id);await assert.rejects(f.x.create(r.run_id),/reconcile/);assert.equal(f.calls.length,1);
});

test('status polling during creation preserves the first attachment audit and does not strand an already running VM',async t=>{
 let release,entered;const held=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);let resource;
 const f=await fixture(t,{create:async intent=>{resource={infra_id:intent.infra_id,provider_instance_id:'i-0123456789abcdef0',node_id:'app-1',spider_name:'spider-owned',connection_name:'aws-us-west-2',intent_sha256:intent.intent_sha256};entered();await held;return {...resource,attachment_ids:{volumes:['vol-0123456789abcdef0'],elastic_ips:[],captured_at:'later-create-audit'}};},status:async()=>({state:'Running',resource:{...resource,attachment_ids:{volumes:['vol-0123456789abcdef0'],elastic_ips:[],captured_at:'first-status-audit'}}}),providerStatus:async()=>({state:'running',instance_id:'i-0123456789abcdef0'})});
 const r=await f.ready(),creation=f.x.create(r.run_id);await waiting;
 await f.x.status(r.run_id);release();const created=await creation;
 assert.equal(created.state,'CREATED');assert.equal(created.resource.attachment_ids.captured_at,'first-status-audit');
 assert.equal(created.resource.provider_instance_id,'i-0123456789abcdef0');
});
test('TB Terminating but provider Running is not cleanup confirmation',async t=>{
 const f=await fixture(t,{providerStatus:async()=>({state:'Running',instance_id:'i-0123456789abcdef0'})});const r=await f.ready();await f.x.create(r.run_id);const c=await f.x.cleanup(r.run_id);assert.equal(c.state,'CLEANUP_PENDING');assert.equal(c.cleanup.confirmed,false);assert.deepEqual(f.calls,['create','stop-app','terminate']);
});
test('operator confirmation recovers only a settled owned creation and never repeats create',async t=>{
 const f=await fixture(t);let r=await f.ready();r=await f.x.create(r.run_id);
 await f.journal.update(r.run_id,x=>({...x,state:'CREATE_REQUESTED',approval:{...x.approval,consumed_at:new Date(Date.now()-360000).toISOString()}}));
 f.client.status=async()=>({state:'Running:1 (R:1/1)',resource:r.resource});
 f.client.providerStatus=async()=>({state:'running',instance_id:r.resource.provider_instance_id});
 const recovered=await f.x.confirmCreated(r.run_id);
 assert.equal(recovered.state,'CREATED');assert.equal(recovered.events.at(-1).event,'creation_confirmation_recovered');
 assert.deepEqual(f.calls,['create']);await assert.rejects(f.x.confirmCreated(r.run_id),/creation_recovery_unavailable/);
});
test('concurrent attachment capture still rejects a changed volume identity',async t=>{
 let release,entered;const held=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);let resource;
 const f=await fixture(t,{create:async intent=>{resource={infra_id:intent.infra_id,provider_instance_id:'i-0123456789abcdef0',node_id:'app-1',spider_name:'spider-owned',connection_name:'aws-us-west-2',intent_sha256:intent.intent_sha256};entered();await held;return {...resource,attachment_ids:{volumes:['vol-fffffffffffffffff'],elastic_ips:[],captured_at:'later'}};},status:async()=>({state:'Running',resource:{...resource,attachment_ids:{volumes:['vol-0123456789abcdef0'],elastic_ips:[],captured_at:'first'}}}),providerStatus:async()=>({state:'running',instance_id:'i-0123456789abcdef0'})});
 const r=await f.ready(),creation=f.x.create(r.run_id),rejected=assert.rejects(creation,/cloud_attachment_identity_mismatch/);await waiting;await f.x.status(r.run_id);release();await rejected;
 assert.deepEqual((await f.journal.read(r.run_id)).resource.attachment_ids.volumes,['vol-0123456789abcdef0']);
});
test('operator confirmation rejects a recent, foreign, stopped or already bootstrapped creation',async t=>{
 const f=await fixture(t);let r=await f.ready();r=await f.x.create(r.run_id);
 f.client.status=async()=>({state:'Running',resource:r.resource});f.client.providerStatus=async()=>({state:'running',instance_id:r.resource.provider_instance_id});
 await f.journal.update(r.run_id,x=>({...x,state:'CREATE_REQUESTED'}));await assert.rejects(f.x.confirmCreated(r.run_id),/creation_recovery_unavailable/);
 await f.journal.update(r.run_id,x=>({...x,approval:{...x.approval,consumed_at:new Date(Date.now()-360000).toISOString()}}));
 f.client.providerStatus=async()=>({state:'running',instance_id:'i-fffffffffffffffff'});await assert.rejects(f.x.confirmCreated(r.run_id),/cloud_ownership_mismatch/);
 f.client.providerStatus=async()=>({state:'stopped',instance_id:r.resource.provider_instance_id});await assert.rejects(f.x.confirmCreated(r.run_id),/cloud_vm_not_running/);
 await f.journal.update(r.run_id,x=>({...x,preparation:{stage:'BOOTSTRAP_START'}}));await assert.rejects(f.x.confirmCreated(r.run_id),/creation_recovery_unavailable/);
 assert.equal((await f.journal.read(r.run_id)).state,'CREATE_REQUESTED');assert.deepEqual(f.calls,['create']);
});
test('provider unavailable blocks next paid run',async t=>{
 const f=await fixture(t,{providerStatus:async()=>{throw Error('unavailable');}});let r=await f.ready();await f.x.create(r.run_id);await f.x.cleanup(r.run_id);const next=await f.x.review({...f.intent,flow_id:'flow-other'});await f.x.approve(next.run_id,{cost_review_id:next.review.cost_review_id,intent_sha256:next.intent.intent_sha256});await assert.rejects(f.x.preflight(next.run_id),/previous_cleanup_required/);
});
test('foreign provider identity is never deleted',async t=>{
 const f=await fixture(t,{create:async intent=>({infra_id:intent.infra_id,intent_sha256:'b'.repeat(64),provider_instance_id:'i-foreign'})});let r=await f.ready();await assert.rejects(f.x.create(r.run_id),/ownership/);assert.equal(f.calls.includes('terminate'),false);
});
test('sealed or disk preflight does not create',async t=>{
 const f=await fixture(t,{preflight:async()=>({ready:false,checks:{openbao:false,disk:false}})});await assert.rejects(f.ready(),/preflight/);assert.equal(f.calls.length,0);
});

test('a consumed VM approval cannot return to preflight and reset its running phase',async t=>{
 const f=await fixture(t),r=await f.ready();await f.x.create(r.run_id);
 await assert.rejects(f.x.preflight(r.run_id),/cloud_preflight_phase_conflict/);
 assert.equal((await f.journal.read(r.run_id)).state,'CREATED');assert.deepEqual(f.calls,['create']);
});

test('failed preparation can retry the same unconsumed approval after the cause is repaired',async t=>{
 let healthy=false;const f=await fixture(t,{preflight:async()=>({ready:healthy})});
 let r=await f.x.review(f.intent);r=await f.x.approve(r.run_id,{cost_review_id:r.review.cost_review_id,intent_sha256:r.intent.intent_sha256});const approvalId=r.approval.approval_id;
 await assert.rejects(f.x.preflight(r.run_id),/cloud_preflight_failed/);healthy=true;r=await f.x.preflight(r.run_id);
 assert.equal(r.state,'READY');assert.equal(r.approval.approval_id,approvalId);assert.equal(f.calls.length,0);
});
test('changed cost review requires fresh approval',async t=>{
 const f=await fixture(t);let r=await f.ready();await f.journal.update(r.run_id,x=>({...x,review:{...x.review,hourly_usd:'99'}}));await assert.rejects(f.x.create(r.run_id),/approval/);assert.equal(f.calls.length,0);
});
test('attachment uncertainty cannot become clean and read-only status never mutates provider',async t=>{
 const f=await fixture(t,{billableAttachments:async()=>({verified:false,remaining:[]})});let r=await f.ready();await f.x.create(r.run_id);await f.x.status(r.run_id);assert.deepEqual(f.calls,['create']);r=await f.x.cleanup(r.run_id);assert.equal(r.state,'CLEANUP_PENDING');
});
test('missing cleanup watcher blocks create even after approved review',async t=>{
 const f=await fixture(t);let r=await f.ready();const x=createCloudExecution({journal:f.journal,client:{},enabled:true,watcher:async()=>({healthy:false})});await assert.rejects(x.create(r.run_id),/watcher/);assert.equal(f.calls.length,0);
});
test('app API outage cannot prevent owned VM termination attempt',async t=>{const f=await fixture(t,{providerStatus:async()=>({state:'Running',instance_id:'i-0123456789abcdef0'})});let r=await f.ready();await f.x.create(r.run_id);const x=createCloudExecution({journal:f.journal,client:f.client,stopApp:async()=>{throw Error('AppDeploy unavailable');}});r=await x.cleanup(r.run_id);assert.ok(f.calls.includes('terminate'));assert.equal(r.state,'CLEANUP_PENDING');});
test('provider read outage still attempts scoped termination but never declares cleanup',async t=>{const f=await fixture(t,{providerStatus:async()=>{throw Error('provider unavailable');}});const r=await f.ready();await f.x.create(r.run_id);const result=await f.x.cleanup(r.run_id);assert.ok(f.calls.includes('terminate'));assert.equal(result.cleanup.confirmed,false);});
test('recovered attachment evidence is durable without changing owned identity',async t=>{
 const f=await fixture(t);let r=await f.ready();r=await f.x.create(r.run_id);f.client.status=async()=>({resource:{...r.resource,attachment_ids:{volumes:['vol-0123456789abcdef0'],elastic_ips:[],captured_at:'verified-now'}}});
 await f.x.status(r.run_id);const stored=await f.journal.read(r.run_id);assert.equal(stored.resource.attachment_ids?.volumes[0],'vol-0123456789abcdef0');assert.equal(stored.resource.provider_instance_id,r.resource.provider_instance_id);
});
test('a slower concurrent cleanup cannot undo provider-confirmed termination and attachment deletion',async t=>{
 let release,entered;const held=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);let reads=0;
 const f=await fixture(t,{providerStatus:async()=>({state:++reads===1?'Running':'Terminated',instance_id:'i-0123456789abcdef0'}),terminate:async()=>{entered();await held;}});
 const r=await f.ready();await f.x.create(r.run_id);const slower=f.x.cleanup(r.run_id);await waiting;
 const confirmed=await f.x.cleanup(r.run_id);assert.equal(confirmed.cleanup.confirmed,true);release();await slower;
 const final=await f.journal.read(r.run_id);assert.equal(final.state,'CLEANED');assert.equal(final.cleanup.confirmed,true);assert.equal(final.cleanup.attachments.verified,true);
});
test('a damaged CLEANED record without confirmation is re-audited instead of treated as final',async t=>{
 const f=await fixture(t);const r=await f.ready();await f.x.create(r.run_id);
 await f.journal.update(r.run_id,x=>({...x,state:'CLEANED',cleanup:{confirmed:false}}));
 const final=await f.x.cleanup(r.run_id);assert.equal(final.cleanup.confirmed,true);assert.equal(final.cleanup.attachments.verified,true);
});

test('a late create failure cannot undo independently confirmed provider rejection cleanup',async t=>{
 let release,entered;const held=new Promise(r=>release=r),waiting=new Promise(r=>entered=r);
 const f=await fixture(t,{create:async()=>{entered();await held;throw Error('late provider response');},rejectedCreation:async r=>({confirmed:true,not_created:true,infra_id:r.intent.infra_id,intent_sha256:r.intent.intent_sha256,provider_name:'tbowned12345678901234',event_ids:['event-owned']}),clearRejected:async()=>({metadata_deleted:true})});
 const r=await f.ready();const delayed=f.x.create(r.run_id);const rejected=assert.rejects(delayed,/create_unknown/);await waiting;
 const settled=await f.x.cleanup(r.run_id);assert.equal(settled.state,'CREATE_REJECTED');release();await rejected;
 const final=await f.journal.read(r.run_id);assert.equal(final.state,'CREATE_REJECTED');assert.equal(final.cleanup.confirmed,true);assert.equal(final.cleanup.not_created,true);assert.equal(final.resource,undefined);
});
