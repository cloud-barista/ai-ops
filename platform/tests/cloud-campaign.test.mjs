import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createCloudJournal} from '../server/cloud-journal.mjs';
import {createCloudExecution} from '../server/cloud-execution.mjs';
const subject=await import('../server/cloud-campaign.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});

async function setup(t){
 assert.equal(typeof subject.createCloudCampaigns,'function','campaign ownership implementation is required');
 const dir=await mkdtemp(join(tmpdir(),'khu-campaign-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const cloudJournal=createCloudJournal(join(dir,'children')),journal=createCloudJournal(join(dir,'parents'));
 const states=new Map();let sequence=0,clock=Date.now(),campaigns;
 const client={review:async()=>({hourly_usd:'0.3456',source:'official-fixture',checked_at:new Date(clock).toISOString(),image_id:'ami-fixture',artifact_sha256:'b'.repeat(64)}),preflight:async()=>({ready:true}),
  create:async i=>{const id='i-'+String(++sequence).padStart(17,'0');states.set(id,'running');return {infra_id:i.infra_id,intent_sha256:i.intent_sha256,provider_instance_id:id};},
  providerStatus:async r=>({instance_id:r.provider_instance_id,state:states.get(r.provider_instance_id)==='terminated'?'Terminated':'running'}),
  status:async()=>({state:'Running'}),terminate:async r=>states.set(r.provider_instance_id,'terminated'),billableAttachments:async()=>({verified:true,remaining:[]})};
 const execution=createCloudExecution({journal:cloudJournal,client,enabled:true,watcher:async()=>({healthy:true}),now:()=>new Date(clock),admission:(r,runs)=>campaigns.admit(r,runs)});
 campaigns=subject.createCloudCampaigns({journal,cloudJournal,execution,client,now:()=>new Date(clock)});
 const flows=[];
 for(const suffix of ['one','two']){const cloud=await execution.review({flow_id:'flow-'+suffix,selection_version:1,spec_sha256:'a'.repeat(64),candidate_id:'m6a.2xlarge',region:'us-west-2',max_lifetime_seconds:2400});flows.push({flow_id:cloud.intent.flow_id,cloud,input:{execution_target:'cloud',model:{model_id:'bert',model_revision:'pinned',batch_size:1,max_length:64}},application_profile:{app_id:'app-'+suffix,requirements:{deployment:{replicas_min:1,replicas_max:1},accelerator:{required:false}}}});}
 const review=()=>campaigns.review(flows);
 const approve=p=>campaigns.approve(p.run_id,{intent_sha256:p.intent.intent_sha256,review_sha256:p.review.review_sha256,compute_budget_usd:0.4608});
 async function running(runId){await execution.preflight(runId);let r=await execution.create(runId);r=await cloudJournal.update(runId,x=>({...x,state:'RUNNING',app:{group_id:'group-'+runId}}));return r;}
 return {campaigns,journal,cloudJournal,execution,flows,review,approve,running,states,tick:ms=>clock+=ms};
}

test('campaign binds two reviewed child plans and sums the full compute budgets',async t=>{
 const f=await setup(t),p=await f.review();assert.equal(p.review.max_compute_usd,0.4608);assert.equal(p.intent.members.length,2);
 const approved=await f.approve(p);assert.equal(approved.state,'ACTIVE');
 for(const flow of f.flows){const run=await f.cloudJournal.read(flow.cloud.run_id);assert.equal(run.campaign_id,p.run_id);assert.equal(run.state,'APPROVED');}
});
test('changing a reviewed child blocks campaign approval before any creation',async t=>{
 const f=await setup(t),p=await f.review();await f.cloudJournal.update(f.flows[1].cloud.run_id,r=>({...r,review:{...r.review,hourly_usd:'9'}}));
 await assert.rejects(f.approve(p),/campaign_child_changed/);assert.equal(f.states.size,0);
});
test('normal runs and forged third members remain blocked by the approved campaign',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());
 const r=await f.execution.review({...f.flows[0].cloud.intent,flow_id:'flow-outsider'});await f.execution.approve(r.run_id,{cost_review_id:r.review.cost_review_id,intent_sha256:r.intent.intent_sha256});
 await assert.rejects(f.execution.preflight(r.run_id),/campaign_admission_required/);
 await f.cloudJournal.update(r.run_id,x=>({...x,campaign_id:p.run_id}));await assert.rejects(f.execution.preflight(r.run_id),/campaign_child_changed/);
 assert.equal(f.states.size,0);
});
test('second VM shares the original deadline and repeated expansion never creates a third VM',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());await f.campaigns.action(p.run_id,'start');const one=await f.running(f.flows[0].cloud.run_id);
 f.tick(120000);await f.campaigns.refresh(p.run_id);await f.campaigns.action(p.run_id,'expand');const two=await f.running(f.flows[1].cloud.run_id);
 assert.equal(two.deadline_at,one.deadline_at);assert.notEqual(one.resource.provider_instance_id,two.resource.provider_instance_id);
 await assert.rejects(f.campaigns.action(p.run_id,'expand'),/campaign_phase_conflict/);assert.equal(f.states.size,2);
});
test('shrinking the second VM preserves the first and stop confirms both cleanups',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());await f.campaigns.action(p.run_id,'start');const one=await f.running(f.flows[0].cloud.run_id);await f.campaigns.refresh(p.run_id);
 await f.campaigns.action(p.run_id,'expand');const two=await f.running(f.flows[1].cloud.run_id);await f.campaigns.refresh(p.run_id);await f.campaigns.action(p.run_id,'shrink');
 await f.execution.cleanup(two.run_id);await f.execution.cleanup(two.run_id);const shrunk=await f.campaigns.refresh(p.run_id);assert.equal(shrunk.phase,'SINGLE_AFTER_SHRINK');assert.equal(f.states.get(one.resource.provider_instance_id),'running');
 await f.campaigns.action(p.run_id,'stop');await f.campaigns.reconcile();await f.campaigns.reconcile();const closed=await f.campaigns.read(p.run_id);assert.equal(closed.state,'CLEANED');assert.equal(closed.cleanup.confirmed,true);
});
test('unknown child creation cancels the campaign while retaining unresolved ownership',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());await f.campaigns.action(p.run_id,'start');await f.running(f.flows[0].cloud.run_id);await f.campaigns.refresh(p.run_id);await f.campaigns.action(p.run_id,'expand');
 await f.cloudJournal.update(f.flows[1].cloud.run_id,r=>({...r,state:'CREATE_UNKNOWN',error_code:'create_unknown'}));const current=await f.campaigns.refresh(p.run_id);
 assert.equal(current.state,'CLEANUP_PENDING');assert.equal(current.error_code,'campaign_child_failed');await f.campaigns.reconcile();assert.notEqual((await f.campaigns.read(p.run_id)).state,'CLEANED');
});
test('expired campaign blocks new creation even if individual review is fresh',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());f.tick(2401000);await assert.rejects(f.campaigns.action(p.run_id,'start'),/campaign_phase_conflict|campaign_deadline/);assert.equal(f.states.size,0);
});
test('provider counts actual unique instances and reports unavailable observations as unknown',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());await f.campaigns.action(p.run_id,'start');const one=await f.running(f.flows[0].cloud.run_id);await f.campaigns.refresh(p.run_id);
 let s=await f.campaigns.snapshot(p.run_id);assert.equal(s.provider_snapshot.running_vm_count,1);assert.equal(s.provider_snapshot.verified,true);
 await f.campaigns.action(p.run_id,'expand');await f.cloudJournal.update(f.flows[1].cloud.run_id,r=>({...r,state:'CREATE_REQUESTED',approval:{...r.approval,consumed_at:new Date().toISOString()}}));
 s=await f.campaigns.snapshot(p.run_id);assert.equal(s.provider_snapshot.verified,false);assert.equal(s.provider_snapshot.running_vm_count,null);assert.equal(f.states.get(one.resource.provider_instance_id),'running');
});
test('campaign stop waits for the same creation gate and prevents a later child create',async t=>{
 const f=await setup(t),p=await f.approve(await f.review());let release,entered;const held=new Promise(r=>release=r),waiting=new Promise(r=>entered=r),original=f.cloudJournal.exclusive;
 f.cloudJournal.exclusive=async fn=>{entered();await held;return original(fn);};
 const stopped=f.campaigns.action(p.run_id,'stop');const outcome=await Promise.race([stopped.then(()=> 'stopped'),waiting.then(()=> 'waiting')]);
 try{assert.equal(outcome,'waiting','parent shutdown must wait for in-flight child admission');assert.equal((await f.journal.read(p.run_id)).phase,'APPROVED');}finally{release();await stopped;}
 await assert.rejects(f.execution.preflight(f.flows[0].cloud.run_id),/campaign_admission_required|campaign_phase_conflict/);assert.equal(f.states.size,0);
});
