import {createHash,randomUUID} from 'node:crypto';
import {activeCloudRun} from './cloud-execution.mjs';
const hash=v=>createHash('sha256').update(JSON.stringify(v)).digest('hex');
const fail=code=>Object.assign(Error(code),{code,status:409});
const requireValue=(v,code)=>{if(!v)throw fail(code);};
const reviewHash=r=>hash({...r,review_sha256:undefined});
const active=p=>['ACTIVE','CLEANUP_PENDING'].includes(p.state);
const settled=r=>!activeCloudRun(r)&&(!r.approval?.consumed_at||r.cleanup?.confirmed===true);
const member=r=>({run_id:r.run_id,flow_id:r.intent.flow_id,intent_sha256:r.intent.intent_sha256,review_sha256:hash(r.review),artifact_sha256:r.review.artifact_sha256});

export function createCloudCampaigns({journal,cloudJournal,execution,client,connectionAudit,now=()=>new Date()}){
 const stamp=()=>now().toISOString();
 const children=p=>Promise.all(p.intent.members.map(m=>cloudJournal.read(m.run_id)));
 function binding(p,r){const m=p.intent.members.find(m=>m.run_id===r.run_id);requireValue(m&&JSON.stringify(m)===JSON.stringify(member(r)),'campaign_child_changed');return m;}
 function approved(p){requireValue(p.approval?.intent_sha256===p.intent.intent_sha256&&p.intent.intent_sha256===hash({...p.intent,intent_sha256:undefined})&&p.review.review_sha256===reviewHash(p.review)&&p.approval.review_sha256===p.review.review_sha256,'campaign_approval_required');}
 const service={read:journal.read,list:journal.list,
  async review(flows){
   requireValue(Array.isArray(flows)&&flows.length===2&&new Set(flows.map(f=>f.flow_id)).size===2,'two_reviewed_flows_required');
   const runs=await Promise.all(flows.map(f=>cloudJournal.read(f.cloud_run_id??f.cloud?.run_id)));
   requireValue(new Set(runs.map(r=>r.run_id)).size===2&&new Set(flows.map(f=>f.application_profile?.app_id)).size===2,'distinct_campaign_children_required');
   for(let i=0;i<2;i++){const f=flows[i],r=runs[i];requireValue(r.state==='REVIEWED'&&!r.campaign_id&&r.intent.flow_id===f.flow_id&&r.intent.candidate_id==='m6a.2xlarge'&&r.intent.region==='us-west-2'&&r.intent.max_lifetime_seconds===2400&&r.review.artifact_sha256?.length===64&&f.input?.execution_target==='cloud'&&!f.application_profile?.requirements.accelerator?.required&&f.application_profile?.requirements.deployment?.replicas_min===1&&f.application_profile.requirements.deployment.replicas_max===1,'campaign_cpu_plan_required');}
   requireValue(JSON.stringify(flows[0].input.model)===JSON.stringify(flows[1].input.model)&&runs[0].review.artifact_sha256===runs[1].review.artifact_sha256,'campaign_workload_mismatch');
   const intent={members:runs.map(member),candidate_id:'m6a.2xlarge',region:'us-west-2',max_vms:2,max_lifetime_seconds:2400};intent.intent_sha256=hash(intent);
   const review={max_compute_usd:Number(runs.reduce((n,r)=>n+r.review.max_compute_usd,0).toFixed(6)),non_compute_margin_usd:runs.reduce((n,r)=>n+r.review.non_compute_margin_usd,0),not_billing_cap:true,checked_at:stamp()};review.review_sha256=reviewHash(review);
   return journal.write({schema_version:1,run_id:'run-campaign-'+randomUUID(),intent,review,state:'REVIEWED',phase:'REVIEWED',created_at:stamp(),events:[{event:'two_child_reviews_bound',at:stamp()}]});
  },
  async approve(id,value){return cloudJournal.exclusive(async()=>{
   let p=await journal.read(id);requireValue(p.state==='REVIEWED'&&value?.intent_sha256===p.intent.intent_sha256&&value.review_sha256===p.review.review_sha256&&p.review.review_sha256===reviewHash(p.review),'campaign_review_changed');
   requireValue(Number.isFinite(value.compute_budget_usd)&&value.compute_budget_usd>0&&value.compute_budget_usd<=0.4608&&p.review.max_compute_usd<=value.compute_budget_usd+1e-8,'campaign_compute_budget_exceeded');
   requireValue(!(await cloudJournal.list()).some(activeCloudRun)&&!(await journal.list()).some(active),'previous_cleanup_required');
   const runs=await children(p);for(const r of runs){binding(p,r);requireValue(r.state==='REVIEWED'&&!r.campaign_id&&now().getTime()-Date.parse(r.review.checked_at)<900000,'campaign_review_expired');}
   p=await journal.update(id,x=>({...x,state:'ACTIVE',phase:'APPROVED',deadline_at:new Date(now().getTime()+2400000).toISOString(),approval:{intent_sha256:x.intent.intent_sha256,review_sha256:x.review.review_sha256,compute_budget_usd:value.compute_budget_usd,approved_at:stamp()},events:[...x.events,{event:'explicit_aggregate_cost_approval',at:stamp()}]}));
   try{for(const r of runs){await cloudJournal.update(r.run_id,x=>({...x,campaign_id:id}));await execution.approve(r.run_id,{cost_review_id:r.review.cost_review_id,intent_sha256:r.intent.intent_sha256});}}catch(e){await journal.update(id,x=>({...x,state:'CLEANUP_PENDING',phase:'STOPPING',error_code:'campaign_child_approval_failed'}));throw e;}
   return p;
  });},
  async admit(r,runs){
   const parents=(await journal.list()).filter(active);
   if(!r.campaign_id){requireValue(parents.length===0,'campaign_admission_required');requireValue(!runs.some(x=>x.run_id!==r.run_id&&activeCloudRun(x)),'previous_cleanup_required');return {};}
   const p=await journal.read(r.campaign_id);binding(p,r);approved(p);
   requireValue(p.state==='ACTIVE'&&parents.length===1&&parents[0].run_id===p.run_id,'campaign_admission_required');
   requireValue(Date.parse(p.deadline_at)>now().getTime()+180000,'campaign_deadline');
   const index=p.intent.members.findIndex(m=>m.run_id===r.run_id);
   requireValue(index===0&&p.phase==='STARTING'||index===1&&p.phase==='EXPANDING','campaign_phase_conflict');
   for(const other of runs.filter(activeCloudRun)){requireValue(other.campaign_id===p.run_id,'previous_cleanup_required');binding(p,other);}
   requireValue(runs.filter(activeCloudRun).every(x=>p.intent.members.some(m=>m.run_id===x.run_id)),'campaign_admission_required');
   return {deadline_at:p.deadline_at};
  },
  async action(id,action){
   await service.refresh(id);
   return cloudJournal.exclusive(()=>journal.update(id,async p=>{
    if(action==='stop'){if(p.state==='CLEANED')return p;requireValue(['ACTIVE','CLEANUP_PENDING','REVIEWED'].includes(p.state),'campaign_phase_conflict');return {...p,state:'CLEANUP_PENDING',phase:'STOPPING',events:[...p.events,{event:'stop_requested',at:stamp()}]};}
    approved(p);requireValue(p.state==='ACTIVE'&&Date.parse(p.deadline_at)>now().getTime()+180000,'campaign_phase_conflict');
    const expected={start:'APPROVED',expand:'SINGLE_RUNNING',shrink:'DUAL_RUNNING'},phase={start:'STARTING',expand:'EXPANDING',shrink:'SHRINKING'};
    requireValue(expected[action]&&p.phase===expected[action],'campaign_phase_conflict');
    return {...p,phase:phase[action],events:[...p.events,{event:action+'_requested',at:stamp()}]};
   }));
  },
  async refresh(id){
   return cloudJournal.exclusive(()=>journal.update(id,async p=>{
    if(!active(p))return p;const runs=await children(p);for(const r of runs)binding(p,r);
    const failed=runs.some((r,i)=>r.failure||r.state==='CREATE_UNKNOWN'||r.state==='CREATE_REJECTED'||r.state==='PREFLIGHT_FAILED'||i===0&&['CLEANED','CLEANUP_PENDING'].includes(r.state)&&!['STOPPING','CLEANED'].includes(p.phase)||i===1&&['CLEANED','CLEANUP_PENDING'].includes(r.state)&&!['SHRINKING','SINGLE_AFTER_SHRINK','STOPPING','CLEANED'].includes(p.phase));
    if(p.state==='ACTIVE'&&(failed||Date.parse(p.deadline_at)<=now().getTime()))p={...p,state:'CLEANUP_PENDING',phase:'STOPPING',error_code:failed?'campaign_child_failed':'campaign_deadline'};
    if(p.state==='CLEANUP_PENDING'&&runs.every(settled))return {...p,state:'CLEANED',phase:'CLEANED',cleanup:{confirmed:true,confirmed_at:stamp()},events:[...p.events,{event:'all_children_cleanup_confirmed',at:stamp()}]};
    const running=r=>r.state==='RUNNING'&&!!r.app?.group_id;
    if(p.phase==='STARTING'&&running(runs[0]))p={...p,phase:'SINGLE_RUNNING'};
    if(p.phase==='EXPANDING'&&runs.every(running))p={...p,phase:'DUAL_RUNNING'};
    if(p.phase==='SHRINKING'&&running(runs[0])&&settled(runs[1]))p={...p,phase:'SINGLE_AFTER_SHRINK'};
    return p;
   }));
  },
  async snapshot(id){
   const p=await service.refresh(id),runs=await children(p),rows=[];let verified=true;const ids=new Set();
   for(const r of runs){let provider;
    if(r.resource?.provider_instance_id){try{provider=await client.providerStatus(r.resource);requireValue(provider.instance_id===r.resource.provider_instance_id&&!ids.has(provider.instance_id)&&['pending','running','stopping','stopped','shutting-down','Terminated'].includes(provider.state),'campaign_provider_mismatch');ids.add(provider.instance_id);}catch{verified=false;provider={instance_id:r.resource.provider_instance_id,state:'UNKNOWN'};}}
    else if(!r.approval?.consumed_at||r.state==='CREATE_REJECTED'&&r.cleanup?.not_created===true&&r.cleanup?.confirmed===true)provider={state:'NOT_CREATED'};
    else{verified=false;provider={state:'UNKNOWN'};}
    rows.push({run_id:r.run_id,flow_id:r.intent.flow_id,instance_id:r.resource?.provider_instance_id??null,provider_state:provider.state,cloud_state:r.state,app_group_id:r.app?.group_id??null,cleanup_confirmed:r.cleanup?.confirmed===true});
   }
   const snapshot={verified,running_vm_count:verified?rows.filter(r=>r.provider_state==='running').length:null,checked_at:stamp(),source:'aws-describe-instances-exact-owned-ids',children:rows};
   return journal.update(id,x=>({...x,provider_snapshot:snapshot}));
  },
  async reconcile(){for(const p of (await journal.list()).filter(active)){let current=await service.refresh(p.run_id);if(current.state==='CLEANUP_PENDING'||current.phase==='SHRINKING'){const runs=await children(current);for(const r of current.phase==='SHRINKING'?[runs[1]]:runs)if(activeCloudRun(r))try{await execution.cleanup(r.run_id);}catch{}await service.refresh(p.run_id);}}},
  async connectionAudit(id){const p=await journal.read(id);requireValue(p.state==='CLEANED'&&p.cleanup?.confirmed,'campaign_cleanup_required');const runs=await children(p);const result=await connectionAudit(runs.map(r=>r.resource?.provider_instance_id).filter(Boolean));await journal.update(id,x=>({...x,connection_audit:result}));return result;},
  children,
 };
 return service;
}
