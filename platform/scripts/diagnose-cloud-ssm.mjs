import {mkdir,writeFile} from 'node:fs/promises';import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';import {setTimeout as delay} from 'node:timers/promises';
import {prepareValidation,platformApi} from './validate-cloud-vm.mjs';import {idleRuntime} from './diagnose-cloud-ssh.mjs';
import {cloudServices,cloudJournalDirectory,watcherHealth} from '../server/cloud-services.mjs';import {createCloudExecution} from '../server/cloud-execution.mjs';import {boundedProcess} from '../server/cloud-client.mjs';import {createSsmClient,safeAwsErrorCode} from '../server/ssm-client.mjs';
const check=(value,code)=>{if(!value)throw Error(code);};
const clean=r=>r.state==='CLEANED'&&r.cleanup?.confirmed===true&&r.cleanup.attachments?.verified===true&&Array.isArray(r.cleanup.attachments.remaining)&&r.cleanup.attachments.remaining.length===0&&r.last_provider_status?.state==='Terminated'&&r.last_provider_status.instance_id===r.resource?.provider_instance_id;
export async function executeSsmDiagnosis(prepared,{services,api,ssm,approved=false,computeBudgetUsd,verifyIdle,save=async()=>{},progress=()=>{},clock=Date.now,pause=delay,onlineTimeoutMs=300000}={}){
 const reviewed=prepared.cloud,id=reviewed?.run_id;
 check(approved===true,'explicit_cost_approval_required');
 check(reviewed?.state==='REVIEWED'&&reviewed.intent.flow_id===prepared.flow_id&&reviewed.intent.candidate_id==='m6a.2xlarge'&&reviewed.intent.max_lifetime_seconds===900,'short_cpu_review_required');
 check(Number.isFinite(computeBudgetUsd)&&computeBudgetUsd>0&&reviewed.review.max_compute_usd<=computeBudgetUsd+1e-8,'reviewed_compute_budget_exceeded');
 const ready=await api('/cloud/readiness');check(ready.enabled===false&&ready.pending_cleanup?.length===0,'cloud_must_be_disabled_and_clean');check(await verifyIdle(),'existing_runtime_must_be_idle');
 const current=await services.journal.read(id);check(current.state==='REVIEWED'&&JSON.stringify(current.intent)===JSON.stringify(reviewed.intent)&&JSON.stringify(current.review)===JSON.stringify(reviewed.review),'cost_review_changed');
 const report={schema_version:1,purpose:'SSM_CONNECTION_DIAGNOSIS_ONLY',run_id:id,flow_id:prepared.flow_id,started_at:new Date(clock()).toISOString(),vm_create_requested:false,ssm_verified:false,cloud_deployment_verified:false,cleanup_verified:false,passed:false};
 let stage='APPROVAL',attempted=false;
 try{
  await services.execution.approve(id,{cost_review_id:reviewed.review.cost_review_id,intent_sha256:reviewed.intent.intent_sha256??reviewed.review.intent_sha256});
  stage='PREFLIGHT';await services.execution.preflight(id);progress({stage:'PREFLIGHT_PASSED'});
  stage='CREATE';attempted=true;const run=await services.execution.create(id);report.vm_create_requested=true;report.provider_instance_id=run.resource.provider_instance_id;await save(report);progress({stage:'OWNED_CPU_VM_CREATED',instance_id:report.provider_instance_id});
  stage='SSM_PROFILE';report.association=await ssm.attach(run.resource);await save(report);progress({stage:'EXISTING_SSM_PROFILE_ASSOCIATED'});
  stage='SSM_ONLINE';const deadline=clock()+onlineTimeoutMs;
  while(!await ssm.online(run.resource)){check(clock()<deadline,'ssm_online_timeout');const owned=await services.journal.read(id);check(owned.state==='CREATED'&&clock()<Date.parse(run.deadline_at)-120000,'owned_vm_no_longer_available');await pause(10000);}
  progress({stage:'SSM_MANAGED_NODE_ONLINE'});
  stage='SSM_COMMAND';report.command_id=await ssm.sendProbe(run.resource);await save(report);const commandDeadline=clock()+60000;
  for(;;){const result=await ssm.commandStatus(run.resource,report.command_id);if(result.state==='Success'){report.proof=result.proof;report.ssm_verified=true;progress({stage:'FIXED_LINUX_PROBE_PASSED'});break;}check(clock()<commandDeadline,'ssm_command_timeout');await pause(3000);}
 }catch(e){report.error_code=/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'ssm_diagnosis_failed';report.failure={stage,code:report.error_code,...safeAwsErrorCode(e.aws_code)?{aws_code:safeAwsErrorCode(e.aws_code)}:{}};
 }finally{
  if(attempted){progress({stage:'CLEANUP_REQUESTED',run_id:id});const deadline=clock()+600000;for(;;){try{const ended=await services.execution.cleanup(id);report.provider_instance_id??=ended.resource?.provider_instance_id;report.cleanup=ended.cleanup;report.provider_status=ended.last_provider_status;if(clean(ended)){report.cleanup_verified=true;progress({stage:'AWS_VM_AND_ATTACHMENTS_DELETED'});break;}}catch{}await save(report);if(clock()>=deadline){report.error_code='cleanup_unconfirmed';break;}await pause(10000);}try{await api('/flows/'+prepared.flow_id+'/cloud/status');}catch{}}
  report.passed=report.ssm_verified&&report.cleanup_verified;report.ended_at=new Date(clock()).toISOString();await save(report);
 }
 return report;
}
async function main(){
 const args=process.argv.slice(2),execute=args.includes('--execute-approved'),index=args.indexOf('--compute-budget-usd'),budget=Number(index<0?undefined:args[index+1]);
 check(args.every((a,i)=>['--execute-approved','--compute-budget-usd'].includes(a)||i>0&&args[i-1]==='--compute-budget-usd'),'unsupported_arguments');if(execute)check(Number.isFinite(budget)&&budget>0,'compute_budget_required');
 const directory=resolve(fileURLToPath(new URL('../../',import.meta.url)),'output/khu-platform-validation/cloud-ssm-diagnosis');await mkdir(directory,{recursive:true});const stamp=new Date().toISOString().replace(/[:.]/g,'-');
 const prepared=await prepareValidation(platformApi,{kind:'cpu',lifetimeSeconds:900,save:r=>writeFile(resolve(directory,'cpu-prepared-'+stamp+'.json'),JSON.stringify(r,null,2))});
 console.log(JSON.stringify({mode:execute?'APPROVED_SSM_DIAGNOSIS':'REVIEW_ONLY',run_id:prepared.cloud.run_id,vm_create_requested:false,instance_profile:'AmazonSSMRoleForInstancesQuickSetup',review:prepared.cloud.review}));if(!execute)return;
 const services=await cloudServices(),account=await boundedProcess(services.config.aws_cli,['--profile',services.config.aws_profile,'--region','us-west-2','--output','json','sts','get-caller-identity','--query','Account'],'',{timeout:20000});check(account==='000000000000','approved_aws_account_required');
 services.execution=createCloudExecution({journal:services.journal,client:services.client,enabled:true,watcher:()=>watcherHealth(cloudJournalDirectory)});
 const path=resolve(directory,'cpu-result-'+stamp+'.json'),report=await executeSsmDiagnosis(prepared,{services,api:platformApi,ssm:createSsmClient(services.config),approved:true,computeBudgetUsd:budget,verifyIdle:idleRuntime,save:r=>writeFile(path,JSON.stringify(r,null,2)),progress:r=>console.log(JSON.stringify({at:new Date().toISOString(),...r}))});
 console.log(JSON.stringify({...report,saved_to:path},null,2));if(!report.passed)process.exitCode=2;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(JSON.stringify({error_code:/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'ssm_diagnosis_failed'}));process.exitCode=2;});
