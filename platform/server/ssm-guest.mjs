import {createSsmClient} from './ssm-client.mjs';import {createCloudJournal} from './cloud-journal.mjs';import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';import {setTimeout as delay} from 'node:timers/promises';
const root=fileURLToPath(new URL('../../',import.meta.url)),check=(value,code)=>{if(!value)throw Error(code);};
export function createSsmGuestAdapter(config,{journal=createCloudJournal(process.env.KHU_CLOUD_JOURNAL_DIR??resolve(root,'output/khu-platform-validation/cloud-journal')),ssm=createSsmClient(config),invoke,pause=delay,clock=Date.now}={}){
 async function owned(input,action){const states=action==='guest_guard'?['CREATED']:action==='guest_bootstrap'?['PREPARING']:['CREATED','PREPARING','RUNNING','CLEANUP_PENDING'];const matches=(await journal.list()).filter(r=>states.includes(r.state)&&r.intent.infra_id===input.infra_id&&r.intent.intent_sha256===input.intent_sha256&&r.resource?.provider_instance_id===input.provider_instance_id&&r.resource.intent_sha256===input.intent_sha256&&Date.parse(r.deadline_at)>clock()+180000);check(matches.length===1,'owned_ssm_guest_required');return matches[0];}
 return async(action,input)=>{
  check(['guest_guard','guest_bootstrap','guest_bootstrap_status','guest_runtime_probe'].includes(action),'unsupported_ssm_guest_action');let run=await owned(input,action);
  if(action==='guest_guard'&&!run.ssm){
   check(run.state==='CREATED','owned_ssm_guest_required');const association=await ssm.attach(run.resource);
   run=await journal.update(run.run_id,r=>{check(r.state==='CREATED','cloud_cleanup_in_progress');return {...r,ssm:{...association,online:false,transport:'ssm'}};});
  }
  check(run.ssm?.association_id,'ssm_association_required');
  if(!run.ssm.online){const deadline=clock()+300000;while(!await ssm.online(run.resource)){run=await owned(input,action);check(run.state==='CREATED'&&clock()<deadline&&clock()<Date.parse(run.deadline_at)-180000,'ssm_online_timeout');await pause(10000);}
   run=await journal.update(run.run_id,r=>{check(r.state==='CREATED','cloud_cleanup_in_progress');return {...r,ssm:{...r.ssm,online:true,online_at:new Date(clock()).toISOString()}};});
  }
  const prepared=await invoke('guest_command_prepare',{...input,guest_action:action});check(prepared.provider_instance_id===input.provider_instance_id,'owned_ssm_guest_required');
  run=await owned(input,action);const result=await ssm.executeOwned(run.resource,prepared,{deadlineAt:run.deadline_at}),text=result.output;
  if(action==='guest_guard'){check(text.includes('KHU_GUEST_GUARD_ARMED'),'guest_guard_failed');return {armed:true,management_transport:'ssm'};}
  if(action==='guest_bootstrap'){check(text.includes('KHU_BOOTSTRAP_STARTED'),'bootstrap_start_failed');return {state:'STARTED',management_transport:'ssm'};}
  let proof;try{proof=JSON.parse(text.trim());}catch{throw Error('ssm_guest_output_invalid');}
  if(action==='guest_bootstrap_status'&&proof.state==='READY'){
   check(proof.provider_instance_id===input.provider_instance_id&&proof.intent_sha256===input.intent_sha256,'bootstrap_identity_mismatch');
   check(proof.execution_mode==='ssh_guest','actual_guest_required');proof={...proof,execution_mode:'ssm_guest',management_transport:'ssm'};
  }
  return proof;
 };
}
