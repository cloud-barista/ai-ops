import {isCloudGpuType} from '../cloud-types.mjs';
import {setTimeout as delay} from 'node:timers/promises';
import {providerUnavailable} from './cloud-client.mjs';
export function vmSshSettleMs(raw=process.env.KHU_VM_SSH_SETTLE_MS){
 if(raw===undefined||raw==='0')return 0;
 if(raw==='180000')return 180000;
 throw Error('invalid_ssh_settle_time');
}
export function validateVmRegistration({run,credential,proof}){
 if(!(proof.execution_mode==='ssh_guest'||proof.execution_mode==='ssm_guest'&&proof.management_transport==='ssm'))throw Error('actual_guest_required');
 if(run.state!=='PREPARING'||!run.resource?.provider_instance_id||proof.state!=='READY'||proof.provider_instance_id!==run.resource.provider_instance_id||proof.intent_sha256!==run.intent.intent_sha256||proof.artifact_sha256!==run.artifact_sha256)throw Error('vm_identity_mismatch');
 if(!/^SHA256:[A-Za-z0-9+/=]+$/.test(credential.host_key_fingerprint??'')||!credential.private_key||!credential.user||!/^\d+\.\d+\.\d+\.\d+$/.test(credential.host)||credential.host==='192.0.2.216'||credential.host.startsWith('127.'))throw Error('verified_host_key_required');
 if(!Number.isInteger(proof.cpu_cores)||proof.cpu_cores<1||!Number.isFinite(proof.memory_bytes)||proof.memory_bytes<1073741824||!Number.isFinite(proof.storage_bytes)||proof.storage_bytes<1073741824||!proof.forward_finite)throw Error('observed_vm_capacity_required');
 const gpu=isCloudGpuType(run.intent.candidate_id);if(gpu&&(!proof.cuda_available||!String(proof.model_device).startsWith('cuda:')||!String(proof.input_device).startsWith('cuda:')||!Number.isInteger(proof.gpu_count)||proof.gpu_count<1))throw Error('cuda_forward_required');
 return {gpu};
}
export async function pollVmBootstrapStatus({client,context,deadlineAt,requirePreparing,pause=delay,onRetry=async()=>{},clock=Date.now}){
 let sshFailures=0;
 for(let n=0;n<90;n++){
  await requirePreparing();
  if(clock()>Date.parse(deadlineAt)-120000)throw Error('bootstrap_timeout');
  let proof;
  try{proof=await client.guestBootstrapStatus(context);}
  catch(e){
   if(e.message!=='provider_unavailable'||e.diagnostic_code!=='guest_ssh_connection_failed'||sshFailures>=3)throw e;
   sshFailures++;
   await onRetry({attempt:sshFailures});
   await pause(15000);
   continue;
  }
  if(proof.state==='FAILED')throw Error('bootstrap_failed');
  if(proof.state==='READY')return proof;
  await pause(10000);
 }
 throw Error('bootstrap_timeout');
}
export async function prepareVmApp({run,journal,client,clients,receipt,artifact,sshSettleMs=0,pause=delay}){
 if(!Number.isInteger(sshSettleMs)||![0,180000].includes(sshSettleMs))throw Error('invalid_ssh_settle_time');
 if(!run.resource?.attachment_ids?.captured_at)throw Error('attachment_audit_required');
 const requirePreparing=async()=>{const current=await journal.read(run.run_id);if(current.state!=='PREPARING')throw Error('cloud_cleanup_in_progress');};
 if(run.state!=='CREATED')throw Error('cloud_cleanup_in_progress');
 let stage;
 const enter=async value=>{stage=value;await journal.update(run.run_id,r=>{if(!['CREATED','PREPARING'].includes(r.state))throw Error('cloud_cleanup_in_progress');return {...r,preparation:{stage,started_at:new Date().toISOString()}};});};
 try{
 await enter('GUEST_GUARD');
 const context={...run.intent,...run.resource,deadline_at:run.deadline_at};await client.guestGuard(context);
 run=await journal.update(run.run_id,r=>{if(r.state!=='CREATED')throw Error('cloud_cleanup_in_progress');return {...r,state:'PREPARING',artifact_sha256:artifact.sha256};});
 await requirePreparing();
 if(sshSettleMs){
  await enter('GUEST_SSH_SETTLE');
  if(Date.now()+sshSettleMs>=Date.parse(run.deadline_at)-120000)throw Error('bootstrap_timeout');
  await pause(sshSettleMs);
  await requirePreparing();
 }
 await enter('BOOTSTRAP_START');
 await client.guestBootstrap({...context,artifact:artifact.payload,artifact_sha256:artifact.sha256,device:isCloudGpuType(run.intent.candidate_id)?'cuda':'cpu'});
 await enter('BOOTSTRAP_STATUS');
 const proof=await pollVmBootstrapStatus({client,context,deadlineAt:run.deadline_at,requirePreparing,pause,onRetry:async({attempt})=>{
  await journal.update(run.run_id,r=>{if(r.state!=='PREPARING')throw Error('cloud_cleanup_in_progress');return {...r,preparation:{...r.preparation,status_ssh_retries:attempt}};});
 }});
 await enter('APP_REGISTRATION');const credential=await client.credential(context),{gpu}=validateVmRegistration({run,credential,proof});
 await requirePreparing();const registered=await clients.registerVmApp({receipt,run,credential,proof,artifact,gpu});
 await journal.update(run.run_id,r=>{if(r.state!=='PREPARING')throw Error('cloud_cleanup_in_progress');return {...r,app:{app_version_id:registered.app.app_version_id,target_profile_id:registered.target.target_profile_id,proof,artifact_sha256:artifact.sha256,credential_ref:registered.credential_ref}};});return registered;
 }catch(e){
  const safe=['provider_unavailable','bootstrap_failed','bootstrap_timeout','cloud_cleanup_in_progress','cuda_forward_required','vm_identity_mismatch','verified_host_key_required','observed_vm_capacity_required','ssm_provider_unavailable','ssm_provider_timeout','ssm_online_timeout','ssm_command_failed','ssm_command_timeout','ssm_command_deadline_insufficient','ssm_guest_output_invalid','owned_ssm_guest_required','ssm_association_required'];
  const diagnostic=providerUnavailable(e.diagnostic_code,e.ssh_diagnostic);
  const appdeploy=e.appdeploy_diagnostic;
  const appdeployDiagnostic=stage==='APP_REGISTRATION'&&['credentials','target-profiles','apps'].includes(appdeploy?.operation)&&Number.isInteger(appdeploy?.http_status)&&appdeploy.http_status>=400&&appdeploy.http_status<=599?{operation:appdeploy.operation,http_status:appdeploy.http_status}:null;
  await journal.update(run.run_id,r=>({...r,failure:r.failure??{stage,code:safe.includes(e.message)?e.message:'cloud_app_prepare_failed',...(diagnostic.diagnostic_code?{diagnostic_code:diagnostic.diagnostic_code}:{}),...(diagnostic.ssh_diagnostic?{ssh_diagnostic:diagnostic.ssh_diagnostic}:{}),...(appdeployDiagnostic?{appdeploy_diagnostic:appdeployDiagnostic}:{}),at:new Date().toISOString()}}));throw e;
 }
}
