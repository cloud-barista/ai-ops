import {cloudVmTypes,isCloudGpuType} from '../cloud-types.mjs';
import {createHash,randomUUID} from 'node:crypto';
const hash=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
const failure=(code,status=409)=>Object.assign(Error(code),{code,status});
const cleanupSettled=r=>(r.state==='CLEANED'||r.state==='CREATE_REJECTED'&&r.cleanup?.not_created===true&&r.rejection?.confirmed===true)&&r.cleanup?.confirmed===true;
function preserveObservedResource(existing,created){
 if(!existing)return created;
 const identity=r=>JSON.stringify(Object.entries(r).filter(([key])=>!['attachment_ids','attachment_audit_state'].includes(key)).sort(([a],[b])=>a.localeCompare(b)));
 if(identity(existing)!==identity(created))throw failure('cloud_ownership_mismatch');
 if(existing.attachment_ids){
  if(created.attachment_ids){
   const ids=r=>JSON.stringify(['volumes','elastic_ips'].map(key=>[key,[...(r[key]??[])].sort()]));
   if(ids(existing.attachment_ids)!==ids(created.attachment_ids))throw failure('cloud_attachment_identity_mismatch');
  }
  return existing;
 }
 return {...existing,...created.attachment_ids?{attachment_ids:created.attachment_ids,attachment_audit_state:created.attachment_audit_state}:{}};
}
export const activeCloudRun=r=>['CREATE_REQUESTED','CREATE_UNKNOWN','CREATED','PREPARING','RUNNING','CLEANUP_PENDING'].includes(r.state)||(['CLEANED','CREATE_REJECTED'].includes(r.state)&&!cleanupSettled(r));
export function cloudIntent(input,run_id){
 if(!/^flow-[\w-]+$/.test(input.flow_id)||!Number.isInteger(input.selection_version)||input.selection_version<1||!/^\w{64}$/.test(input.spec_sha256)||!cloudVmTypes.includes(input.candidate_id)||input.region!=='us-west-2'||!Number.isInteger(input.max_lifetime_seconds)||input.max_lifetime_seconds<900||input.max_lifetime_seconds>2700)throw failure('invalid_cloud_intent',400);
 const intent={flow_id:input.flow_id,selection_version:input.selection_version,spec_sha256:input.spec_sha256,candidate_id:input.candidate_id,region:input.region,max_lifetime_seconds:input.max_lifetime_seconds,infra_id:'khu-poc-'+run_id.slice(4),node_group_id:'app',idempotency_key:run_id,root_disk_gib:isCloudGpuType(input.candidate_id)?100:30};
 if(input.availability_zone!==undefined){if(!['us-west-2a','us-west-2b','us-west-2c','us-west-2d'].includes(input.availability_zone))throw failure('invalid_cloud_zone',400);intent.availability_zone=input.availability_zone;}
 return {...intent,intent_sha256:hash(intent)};
}
export function createCloudExecution({journal,client,enabled=false,watcher,stopApp=async()=>{},now=()=>new Date(),admission}){
 const stamp=()=>now().toISOString();
 async function healthy(){if(!(await watcher?.())?.healthy)throw failure('cleanup_watcher_unavailable');}
 function approval(r){const a=r.approval;if(!a||a.intent_sha256!==r.intent.intent_sha256||a.review_sha256!==hash(r.review)||Date.parse(a.expires_at)<=now().getTime()||r.review.intent_sha256!==r.intent.intent_sha256)throw failure('cost_approval_required');}
 async function previous(id){const runs=await journal.list();if(admission)return admission(await journal.read(id),runs);if(runs.some(r=>r.run_id!==id&&activeCloudRun(r)))throw failure('previous_cleanup_required');return {};}
 async function resolveOwned(r){if(r.resource)return r;const s=await client.status(r.intent);if(!s.resource)throw failure('resource_identity_unknown');const resource=s.resource;if(resource.infra_id!==r.intent.infra_id||resource.intent_sha256!==r.intent.intent_sha256||!/^i-[a-f0-9]{8,17}$/.test(resource.provider_instance_id))throw failure('cloud_ownership_mismatch');return journal.update(r.run_id,x=>({...x,resource}));}
 return {
  async review(input){const run_id='run-'+randomUUID(),intent=cloudIntent(input,run_id),quote=await client.review(intent);if(!/^\d+(\.\d+)?$/.test(String(quote.hourly_usd))||!quote.source)throw failure('price_review_unavailable',502);const review={...quote,cost_review_id:'review-'+randomUUID(),intent_sha256:intent.intent_sha256,max_compute_usd:Number((Number(quote.hourly_usd)*intent.max_lifetime_seconds/3600).toFixed(6)),non_compute_margin_usd:1,lifetime_seconds:intent.max_lifetime_seconds,not_billing_cap:true};return journal.write({schema_version:1,run_id,intent,review,state:quote.image_id&&quote.ready_for_approval!==false?'REVIEWED':'REVIEW_INCOMPLETE',created_at:stamp(),events:[{event:'cost_reviewed',at:stamp()}]});},
  async approve(id,value){return journal.update(id,r=>{if(r.state!=='REVIEWED'||value.cost_review_id!==r.review.cost_review_id||value.intent_sha256!==r.intent.intent_sha256||Object.keys(value).some(k=>!['cost_review_id','intent_sha256'].includes(k)))throw failure('cost_review_changed');if(now().getTime()-Date.parse(r.review.checked_at)>900000)throw failure('cost_review_expired');return {...r,state:'APPROVED',approval:{approval_id:'approval-'+randomUUID(),intent_sha256:r.intent.intent_sha256,review_sha256:hash(r.review),max_compute_usd:r.review.max_compute_usd,approved_at:stamp(),expires_at:new Date(now().getTime()+900000).toISOString()},events:[...r.events,{event:'explicit_cost_approval',at:stamp()}]};});},
  async preflight(id){let r=await journal.read(id);approval(r);if(!['APPROVED','PREFLIGHT_FAILED'].includes(r.state)||r.approval.consumed_at)throw failure('cloud_preflight_phase_conflict');await previous(id);await healthy();const preflight=await client.preflight({...r.intent,image_id:r.review.image_id,...r.review.network_binding?{network_binding:r.review.network_binding}:{}});r=await journal.update(id,x=>{approval(x);if(!['APPROVED','PREFLIGHT_FAILED'].includes(x.state)||x.approval.consumed_at)throw failure('cloud_preflight_phase_conflict');return {...x,preflight,preflight_at:stamp(),state:preflight.ready?'READY':'PREFLIGHT_FAILED'};});if(!preflight.ready)throw failure('cloud_preflight_failed');return r;},
  async create(id){if(!enabled)throw failure('live_cloud_disabled');await healthy();let r=await journal.exclusive(async()=>{const admitted=await previous(id);const current=await journal.read(id);approval(current);if(current.state!=='READY'||current.approval.consumed_at)throw failure('reconcile_existing_cloud_run');if(now().getTime()-Date.parse(current.preflight_at)>120000)throw failure('cloud_preflight_expired');const deadline=Math.min(now().getTime()+current.intent.max_lifetime_seconds*1000,admitted.deadline_at?Date.parse(admitted.deadline_at):Infinity);if(!Number.isFinite(deadline)||deadline<=now().getTime()+180000)throw failure('campaign_deadline');return journal.update(id,x=>{if(x.state!=='READY'||x.approval.consumed_at)throw failure('reconcile_existing_cloud_run');return {...x,state:'CREATE_REQUESTED',deadline_at:new Date(deadline).toISOString(),approval:{...x.approval,consumed_at:stamp()},events:[...x.events,{event:'create_requested',at:stamp()}]};});});
   let resource;try{resource=await client.create({...r.intent,image_id:r.review.image_id,deadline_at:r.deadline_at,...r.review.network_binding?{network_binding:r.review.network_binding}:{}});}catch{await journal.update(id,x=>cleanupSettled(x)?x:({...x,state:x.state==='CLEANUP_PENDING'?x.state:'CREATE_UNKNOWN',error_code:'create_unknown'}));throw failure('create_unknown',502);}
   if(resource.infra_id!==r.intent.infra_id||resource.intent_sha256!==r.intent.intent_sha256||!/^i-[a-f0-9]{8,17}$/.test(resource.provider_instance_id)){await journal.update(id,x=>cleanupSettled(x)?x:({...x,state:x.state==='CLEANUP_PENDING'?x.state:'CREATE_UNKNOWN',error_code:'cloud_ownership_mismatch'}));throw failure('cloud_ownership_mismatch',502);}
   if(r.intent.availability_zone&&resource.availability_zone!==r.intent.availability_zone){await journal.update(id,x=>cleanupSettled(x)?x:({...x,resource,state:x.state==='CLEANUP_PENDING'?x.state:'CREATE_UNKNOWN',error_code:'cloud_zone_mismatch'}));throw failure('cloud_zone_mismatch',502);}
   return journal.update(id,x=>cleanupSettled(x)?x:({...x,resource:preserveObservedResource(x.resource,resource),state:x.state==='CLEANUP_PENDING'?x.state:'CREATED',error_code:x.state==='CLEANUP_PENDING'?x.error_code:undefined,events:[...x.events,{event:'owned_vm_identified',at:stamp()}]}));
  },
  async confirmCreated(id){
   if(!enabled)throw failure('live_cloud_disabled');await healthy();
   const eligible=r=>['CREATE_REQUESTED','CREATE_UNKNOWN'].includes(r.state)&&r.resource&&!r.app&&!r.ssm&&!r.preparation&&r.approval?.consumed_at&&Number.isFinite(Date.parse(r.approval.consumed_at))&&now().getTime()-Date.parse(r.approval.consumed_at)>300000&&Date.parse(r.deadline_at)>now().getTime()+300000;
   const r=await journal.read(id);if(!eligible(r))throw failure('creation_recovery_unavailable');
   if(r.approval.intent_sha256!==r.intent.intent_sha256||r.approval.review_sha256!==hash(r.review))throw failure('cost_approval_required');
   const observed=await client.status({...r.intent,...r.resource}),provider=await client.providerStatus(r.resource);
   if(provider.instance_id!==r.resource.provider_instance_id||!observed.resource||['infra_id','intent_sha256','provider_instance_id','node_id','spider_name','connection_name'].some(key=>observed.resource[key]!==r.resource[key]))throw failure('cloud_ownership_mismatch');
   if(provider.state!=='running'||!/^Running(?:$|:)/.test(observed.state??''))throw failure('cloud_vm_not_running');
   return journal.update(id,x=>{if(!eligible(x))throw failure('creation_recovery_unavailable');return {...x,state:'CREATED',error_code:undefined,last_provider_status:provider,events:[...x.events,{event:'creation_confirmation_recovered',at:stamp(),source:'operator-provider-and-tumblebug-audit'}]};});
  },
  async status(id){let r=await journal.read(id);if(!activeCloudRun(r))return r;try{
   r=await resolveOwned(r);const observed=await client.status({...r.intent,...r.resource});
   if(observed.resource?.attachment_ids&&!r.resource.attachment_ids){const owned=observed.resource;if(owned.infra_id!==r.resource.infra_id||owned.provider_instance_id!==r.resource.provider_instance_id||owned.intent_sha256!==r.resource.intent_sha256)throw failure('cloud_ownership_mismatch');r=await journal.update(id,x=>({...x,resource:{...x.resource,attachment_ids:owned.attachment_ids,attachment_audit_state:'VERIFIED'}}));}
   const provider=await client.providerStatus(r.resource);return journal.update(id,x=>({...x,last_status:{...observed,resource:undefined},last_provider_status:provider,last_checked_at:stamp()}));
  }catch{return journal.update(id,x=>({...x,error_code:'provider_unavailable',last_checked_at:stamp()}));}},
  async cleanup(id){
   let r=await journal.read(id);if(cleanupSettled(r))return r;if(!activeCloudRun(r))throw failure('no_owned_cloud_request');
   r=await journal.update(id,x=>cleanupSettled(x)?x:({...x,state:'CLEANUP_PENDING',cleanup:{...x.cleanup,confirmed:false,requested_at:x.cleanup?.requested_at??stamp()}}));
   if(cleanupSettled(r))return r;
   try{
    if(!r.resource&&typeof client.rejectedCreation==='function'){
     let rejection;try{rejection=await client.rejectedCreation(r);}catch{}
     if(rejection?.confirmed===true&&rejection.not_created===true&&rejection.infra_id===r.intent.infra_id&&rejection.intent_sha256===r.intent.intent_sha256&&/^tb[a-z0-9]{18,30}$/.test(rejection.provider_name??'')&&rejection.event_ids?.length>0){
      const cleared=await client.clearRejected(r.intent,rejection);if(cleared.metadata_deleted!==true)throw failure('creation_rejection_unverified');
      return journal.update(id,x=>cleanupSettled(x)?x:({...x,state:'CREATE_REJECTED',rejection,cleanup:{...x.cleanup,confirmed:true,not_created:true,metadata_deleted:true,attachments:{verified:true,remaining:[],source:'provider-rejection-audit'},confirmed_at:stamp()},error_code:'provider_capacity_unavailable'}));
     }
    }
    r=await resolveOwned(await journal.read(id));let appStopped=true;try{await stopApp(r);}catch{appStopped=false;}
    let provider;try{provider=await client.providerStatus(r.resource);}catch{}
    if(provider&&provider.instance_id!==r.resource.provider_instance_id)throw failure('cloud_ownership_mismatch');
    if(provider?.state!=='Terminated'){
     // A read outage must not prevent an exact, ownership-checked delete request.
     await client.terminate({...r.intent,...r.resource});
     try{provider=await client.providerStatus(r.resource);}catch{}
     return journal.update(id,x=>cleanupSettled(x)?x:({...x,last_provider_status:provider??x.last_provider_status,cleanup:{...x.cleanup,confirmed:false,app_stop_confirmed:appStopped},error_code:provider?.state==='Terminated'?null:provider?'termination_pending':'provider_unavailable'}));
    }
    const attachments=await client.billableAttachments(r.resource),confirmed=attachments.verified===true&&Array.isArray(attachments.remaining)&&attachments.remaining.length===0;
    return journal.update(id,x=>cleanupSettled(x)?x:({...x,state:confirmed?'CLEANED':'CLEANUP_PENDING',last_provider_status:provider,cleanup:{...x.cleanup,confirmed,app_stop_confirmed:appStopped,app_terminated_by_vm:true,attachments,confirmed_at:confirmed?stamp():null},error_code:confirmed?null:'attachment_audit_required'}));
   }catch(e){return journal.update(id,x=>cleanupSettled(x)?x:({...x,state:'CLEANUP_PENDING',error_code:e.code==='cloud_ownership_mismatch'?e.code:'provider_unavailable',cleanup:{...x.cleanup,confirmed:false}}));}
  },
 };
}
