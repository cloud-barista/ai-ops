import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';import {mkdir,writeFile} from 'node:fs/promises';import {setTimeout as delay} from 'node:timers/promises';
import {randomUUID} from 'node:crypto';
import {prepareValidation,platformApi} from './validate-cloud-vm.mjs';
import {cloudServices,watcherHealth,cloudJournalDirectory} from '../server/cloud-services.mjs';
import {createCloudExecution,activeCloudRun,cloudIntent} from '../server/cloud-execution.mjs';
import {providerUnavailable,boundedProcess} from '../server/cloud-client.mjs';
import {readOwnedConsole} from './read-cloud-console-diagnostic.mjs';
const check=(condition,code)=>{if(!condition)throw Error(code);};
export function consoleCoversProbe(observations,notBefore){return Number.isFinite(notBefore)&&notBefore>0&&observations.some(x=>Number.isInteger(x.at)&&x.at>=notBefore);}
const preflightChecks=new Set(['openbao_unsealed','lab_root_free','lab_service_storage_free','tb_reachable','unique_infra_id','native_ssh_available','anonymous_key_memory','provider_attachment_audit','windows_storage']);
function preflightSummary(value){
 if(typeof value?.ready!=='boolean')return;
 const result={ready:value.ready,checks:Object.fromEntries(Object.entries(value.checks??{}).filter(([k,v])=>preflightChecks.has(k)&&typeof v==='boolean'))},s=value.storage?.lab_root;
 if(s?.path==='/var/tmp'&&Number.isSafeInteger(s.free_bytes)&&s.free_bytes>=0&&s.required_free_bytes===5*1024**3)result.storage={lab_root:{path:'/var/tmp',free_bytes:s.free_bytes,required_free_bytes:s.required_free_bytes}};
 const services={};for(const k of ['tumblebug_log','spider_log','openbao_data','docker_data']){const d=value.storage?.lab_services?.[k];if(typeof d?.available==='boolean'&&d.required_free_bytes===5*1024**3&&(d.available===false||Number.isSafeInteger(d.free_bytes)&&d.free_bytes>=0))services[k]={available:d.available,required_free_bytes:d.required_free_bytes,...d.available?{free_bytes:d.free_bytes}:{}};}
 if(Object.keys(services).length)result.storage={...result.storage,lab_services:services};
 return result;
}
export async function checkSshDiagnosis(prepared,{services,api,verifyIdle,watcher=()=>watcherHealth(cloudJournalDirectory),clock=Date.now}={}){
 const ready=await api('/cloud/readiness'),preflight=preflightSummary(await services.client.preflight({...prepared.cloud.intent,image_id:prepared.cloud.review?.image_id}));check(preflight,'invalid_preflight_response');
 Object.assign(preflight.checks,{previous_cleanup:ready.pending_cleanup?.length===0,cloud_execution_disabled:ready.enabled===false,cleanup_watcher:(await watcher())?.healthy===true,runtime_idle:typeof verifyIdle==='function'&&await verifyIdle()});
 preflight.ready=preflight.ready&&Object.values(preflight.checks).every(v=>v===true);
 return {schema_version:1,purpose:'PREFLIGHT_ONLY',checked_at:new Date(clock()).toISOString(),vm_create_requested:false,cloud_deployment_verified:false,ready:preflight.ready,preflight,failed_checks:Object.entries(preflight.checks).filter(([,v])=>v===false).map(([k])=>k)};
}
function safeProbes(value){
 check(value?.state==='COMPLETE'&&Array.isArray(value.probes)&&value.probes.length>=1&&value.probes.length<=2,'invalid_ssh_probe_response');
 return value.probes.map((p,n)=>{
  check(p.name===(n===0?'SHORT':'BOOTSTRAP_SIZE')&&['PASSED','FAILED'].includes(p.status)&&Number.isInteger(p.command_bytes)&&(n===0?p.command_bytes>0&&p.command_bytes<1024:p.command_bytes===35548)&&Number.isFinite(p.elapsed_ms)&&p.elapsed_ms>=0&&p.elapsed_ms<=240000,'invalid_ssh_probe_response');
  check(n===0||value.probes[0].status==='PASSED','invalid_ssh_probe_response');
  const d=providerUnavailable(p.diagnostic_code,p.ssh_diagnostic);
  return {name:p.name,status:p.status,command_bytes:p.command_bytes,elapsed_ms:p.elapsed_ms,...d.diagnostic_code?{diagnostic_code:d.diagnostic_code}:{},...d.ssh_diagnostic?{ssh_diagnostic:d.ssh_diagnostic}:{}};
 });
}
function confirmedCleanup(r){const a=r.cleanup?.attachments;return r.state==='CLEANED'&&r.cleanup?.confirmed===true&&r.last_provider_status?.state==='Terminated'&&r.last_provider_status.instance_id===r.resource?.provider_instance_id&&a?.verified===true&&Array.isArray(a.remaining)&&a.remaining.length===0;}
export async function executeSshDiagnosis(prepared,{services,api,approved=false,computeBudgetUsd,verifyIdle,save=async()=>{},progress=()=>{},clock=Date.now,pause=delay,cleanupTimeoutMs=600000,requireObserver=false,collectDiagnostic}={}){
 check(approved===true,'explicit_cost_approval_required');
 const reviewed=prepared.cloud;
 check(reviewed?.state==='REVIEWED'&&reviewed.intent.flow_id===prepared.flow_id&&reviewed.intent.candidate_id==='m6a.2xlarge'&&reviewed.intent.max_lifetime_seconds===900,'short_cpu_review_required');
 check(Number.isFinite(computeBudgetUsd)&&computeBudgetUsd>0&&reviewed.review.max_compute_usd<=computeBudgetUsd+1e-8,'reviewed_compute_budget_exceeded');
 const ready=await api('/cloud/readiness');check(ready.enabled===false,'cloud_execution_must_be_disabled');check(ready.pending_cleanup?.length===0,'previous_cleanup_required');
 check(typeof verifyIdle==='function'&&await verifyIdle(),'existing_runtime_must_be_idle');
 const {journal,execution,client}=services,id=reviewed.run_id;
 const currentReview=await journal.read(id);
 check(currentReview.state==='REVIEWED'&&JSON.stringify(currentReview.intent)===JSON.stringify(reviewed.intent)&&JSON.stringify(currentReview.review)===JSON.stringify(reviewed.review),'cost_review_changed');
 const report={schema_version:1,purpose:'SSH_CONNECTION_DIAGNOSIS_ONLY',flow_id:prepared.flow_id,run_id:id,candidate_id:reviewed.intent.candidate_id,started_at:new Date(clock()).toISOString(),max_lifetime_seconds:900,vm_create_requested:false,ssh_probe_verified:false,cloud_deployment_verified:false,cleanup_required:false,cleanup_verified:false,passed:false,probes:[]};
 let stage='APPROVAL',attempted=false;
 const persist=()=>save(report);
 try{
  // Same journal, cost-bound approval, one-create mutex and watcher as the UI.
  await execution.approve(id,{cost_review_id:reviewed.review.cost_review_id,intent_sha256:reviewed.intent.intent_sha256});
  stage='PREFLIGHT';report.preflight=preflightSummary((await execution.preflight(id)).preflight);progress({stage:'PREFLIGHT_PASSED'});
  stage='CREATE';await persist();attempted=true;const run=await execution.create(id);report.vm_create_requested=true;report.cleanup_required=true;report.provider_instance_id=run.resource.provider_instance_id;await persist();progress({stage:'OWNED_CPU_VM_CREATED',instance_id:report.provider_instance_id});
  check(run.resource.attachment_ids?.captured_at,'attachment_audit_required');
  const requireOwned=async()=>{const current=await journal.read(id);check(current.state==='CREATED','cloud_cleanup_in_progress');check(clock()<Date.parse(current.deadline_at)-180000,'probe_timeout');};
  const context={...run.intent,...run.resource,deadline_at:run.deadline_at};
  stage='GUEST_GUARD';await requireOwned();const guard=await client.guestGuard(context);check(guard?.armed===true,'guest_guard_failed');report.guest_observer_ready=guard.observer_ready===true;report.guest_observer_state=['active','inactive','failed','activating'].includes(guard.observer_state)?guard.observer_state:'UNKNOWN';if(requireObserver)check(report.guest_observer_ready,'guest_observer_unavailable');progress({stage:report.guest_observer_ready?'GUEST_OBSERVER_READY':'GUEST_GUARD_ARMED'});
  stage='SSH_PROBE';await requireOwned();report.probes=safeProbes(await client.guestSshProbe(context));report.probe_completed_at=new Date(clock()).toISOString();report.ssh_probe_verified=report.probes.length===2&&report.probes.every(p=>p.status==='PASSED');
  await journal.update(id,r=>({...r,ssh_probe:{purpose:report.purpose,probes:report.probes,verified:report.ssh_probe_verified,checked_at:new Date(clock()).toISOString()}}));await persist();
  for(const probe of report.probes)progress({stage:'SSH_PROBE_RESULT',...probe});
  if(!report.ssh_probe_verified){const failed=report.probes.find(p=>p.status==='FAILED');throw Object.assign(Error('ssh_probe_failed'),providerUnavailable(failed?.diagnostic_code,failed?.ssh_diagnostic));}
 }catch(e){
  const safe=new Set(['cloud_preflight_failed','cleanup_watcher_unavailable','cost_approval_required','cost_review_expired','cost_review_changed','previous_cleanup_required','cloud_preflight_expired','live_cloud_disabled','reconcile_existing_cloud_run','create_unknown','cloud_ownership_mismatch','attachment_audit_required','guest_guard_failed','guest_observer_unavailable','probe_timeout','invalid_ssh_probe_response','ssh_probe_failed','provider_unavailable','cloud_cleanup_in_progress']);
  report.error_code=safe.has(e.message)?e.message:'ssh_diagnosis_failed';
  const d=providerUnavailable(e.diagnostic_code,e.ssh_diagnostic);report.failure={stage,code:report.error_code,...d.diagnostic_code?{diagnostic_code:d.diagnostic_code}:{},...d.ssh_diagnostic?{ssh_diagnostic:d.ssh_diagnostic}:{}};
  if(stage==='PREFLIGHT')try{report.preflight=preflightSummary((await journal.read(id)).preflight);if(report.preflight)report.failure.failed_checks=Object.entries(report.preflight.checks).filter(([,v])=>v===false).map(([k])=>k);}catch{}
  // Durable evidence survives termination even if the console/report is closed.
  try{await journal.update(id,r=>({...r,failure:r.failure??{...report.failure,at:new Date(clock()).toISOString()}}));}catch{}
 }finally{
  if(attempted){
   if(report.guest_observer_ready&&typeof collectDiagnostic==='function')try{report.console_diagnostic=await collectDiagnostic(report.run_id,Math.floor(Date.parse(report.probe_completed_at??new Date(clock()).toISOString())/1000));await persist();}catch{report.console_diagnostic={state:'UNAVAILABLE'};}
   let current;try{current=await journal.read(id);report.vm_create_requested=!!current.approval?.consumed_at||activeCloudRun(current);}catch{report.vm_create_request_uncertain=true;}
   // An unreadable journal after a create attempt is uncertainty, never proof
   // that nothing was created. Keep trying the same run's cleanup path.
   report.cleanup_required=!current||activeCloudRun(current)||!!current.approval?.consumed_at;
   if(report.cleanup_required){
    const deadline=clock()+cleanupTimeoutMs;progress({stage:'CLEANUP_REQUESTED',run_id:id});
    for(;;){
     try{current=await execution.cleanup(id);report.vm_create_requested=!!current.approval?.consumed_at||activeCloudRun(current);if(current.approval?.consumed_at)delete report.vm_create_request_uncertain;report.provider_instance_id??=current.resource?.provider_instance_id;report.cleanup=current.cleanup;report.provider_status=current.last_provider_status;
      if(confirmedCleanup(current)){report.cleanup_verified=true;progress({stage:'AWS_VM_AND_ATTACHMENTS_DELETED',instance_id:report.provider_instance_id});break;}
     }catch{}
     await persist();if(clock()>=deadline){report.error_code='cleanup_unconfirmed';break;}await pause(10000);
    }
   }
   try{await api('/flows/'+prepared.flow_id+'/cloud/status');}catch{}
  }
  report.cleanup_state=report.cleanup_required?(report.cleanup_verified?'CONFIRMED':'UNCONFIRMED'):'NOT_REQUIRED';
  report.passed=report.ssh_probe_verified&&report.cleanup_verified;report.ended_at=new Date(clock()).toISOString();await persist();
 }
 return report;
}
export function cloudGroupIsCleaned(group,run){
 const match=group?.target_profile_id?.match(/^khu-vm-([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/);
 return !!match&&run?.run_id==='run-'+match[1]&&run.intent?.flow_id===group.flow_id&&run.app?.app_version_id===group.app_version_id&&run.app?.target_profile_id===group.target_profile_id&&run.state==='CLEANED'&&run.cleanup?.confirmed===true&&run.cleanup?.attachments?.verified===true&&Array.isArray(run.cleanup.attachments.remaining)&&run.cleanup.attachments.remaining.length===0&&run.last_provider_status?.state==='Terminated'&&/^i-[a-f0-9]{8,17}$/.test(run.resource?.provider_instance_id??'')&&run.last_provider_status.instance_id===run.resource.provider_instance_id;
}
export async function idleRuntime(){
 const list=async(path,fields)=>{const r=await fetch('http://127.0.0.1:18150/api/v1/'+path,{signal:AbortSignal.timeout(10000)});check(r.ok,'runtime_status_unavailable');const data=await r.json();if(Array.isArray(data))return data;for(const field of fields)if(Array.isArray(data?.[field]))return data[field];throw Error('runtime_status_unavailable');};
 const groups=await list('deployment-groups',['items','groups']),runs=await (await cloudServices()).journal.list();if(groups.some(g=>(g.operation_state!=='STOPPED'||g.actual_replicas!==0)&&!cloudGroupIsCleaned(g,runs.find(r=>r.app?.target_profile_id===g.target_profile_id))))return false;
 const stopped=new Set(['STOPPED','COMPLETED','RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED']);
 return !(await list('deployments',['items','deployments'])).some(d=>!d.group_id&&!stopped.has(d.status));
}
async function main(){
 const args=process.argv.slice(2),execute=args.includes('--execute-approved'),checkOnly=args.includes('--check-only'),index=args.indexOf('--compute-budget-usd'),budget=Number(index<0?undefined:args[index+1]);
 check(args.every((a,i)=>['--execute-approved','--compute-budget-usd','--check-only'].includes(a)||i>0&&args[i-1]==='--compute-budget-usd'),'unsupported_arguments');check(!(execute&&checkOnly),'conflicting_modes');if(execute)check(Number.isFinite(budget)&&budget>0,'compute_budget_required');
 const directory=resolve(fileURLToPath(new URL('../../',import.meta.url)),'output/khu-platform-validation/cloud-ssh-diagnosis');await mkdir(directory,{recursive:true});
 const stamp=new Date().toISOString().replace(/[:.]/g,'-'),preparedFile=resolve(directory,'cpu-prepared-'+stamp+'.json'),resultFile=resolve(directory,'cpu-result-'+stamp+'.json');
 if(checkOnly){
  const services=await cloudServices(),intent=cloudIntent({flow_id:'flow-preflight-only',selection_version:1,spec_sha256:'0'.repeat(64),candidate_id:'m6a.2xlarge',region:'us-west-2',max_lifetime_seconds:900},'run-'+randomUUID());
  // A hypothetical identity is used only for GET-based uniqueness checks. No
  // review, approval, VM create, journal run, or provider cleanup is requested.
  const result=await checkSshDiagnosis({cloud:{intent,review:{image_id:services.config.image_ids?.cpu}}},{services,api:platformApi,verifyIdle:idleRuntime});
  const savedTo=resolve(directory,'cpu-check-'+stamp+'.json');await writeFile(savedTo,JSON.stringify(result,null,2));console.log(JSON.stringify({...result,saved_to:savedTo},null,2));if(!result.ready)process.exitCode=2;return;
 }
 const prepared=await prepareValidation(platformApi,{kind:'cpu',lifetimeSeconds:900,save:r=>writeFile(preparedFile,JSON.stringify(r,null,2))});
 console.log(JSON.stringify({mode:execute?'APPROVED_SSH_DIAGNOSIS':'REVIEW_ONLY',vm_create_requested:false,flow_id:prepared.flow_id,review:prepared.cloud.review,prepared_file:preparedFile}));if(!execute)return;
 const services=await cloudServices();let account;
 try{account=await boundedProcess(services.config.aws_cli,['--profile',services.config.aws_profile,'--region','us-west-2','--output','json','--no-cli-pager','sts','get-caller-identity','--query','Account'],'',{timeout:20000});}catch{throw Error('aws_read_access_required');}
 check(account==='000000000000','approved_aws_account_required');
 // An explicit CLI cost approval enables only this one synchronous diagnosis;
 // the UI and AppDeploy stay disabled for cloud creation/application execution.
 services.execution=createCloudExecution({journal:services.journal,client:services.client,enabled:true,watcher:()=>watcherHealth(cloudJournalDirectory)});
 const collectDiagnostic=async(runId,notBefore)=>{const deadline=Date.now()+90000;for(;;){const result=await readOwnedConsole(runId);if(consoleCoversProbe(result.observations,notBefore))return {state:'COLLECTED',covers_probe:true,observations:result.observations.length,packet_capture:result.observations.some(x=>x.packet_capture===true),saved_to:result.saved_to};if(Date.now()>=deadline)return {state:result.observations.length?'STALE':'EMPTY',covers_probe:false,saved_to:result.saved_to};await delay(10000);}};
 const report=await executeSshDiagnosis(prepared,{services,api:platformApi,approved:true,computeBudgetUsd:budget,verifyIdle:idleRuntime,requireObserver:true,collectDiagnostic,save:r=>writeFile(resultFile,JSON.stringify(r,null,2)),progress:r=>console.log(JSON.stringify({at:new Date().toISOString(),...r}))});
 console.log(JSON.stringify({...report,saved_to:resultFile},null,2));if(!report.passed)process.exitCode=2;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(JSON.stringify({error_code:/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'ssh_diagnosis_failed'}));process.exitCode=2;});
