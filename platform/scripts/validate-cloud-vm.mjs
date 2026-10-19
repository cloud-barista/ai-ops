import {gpuVmTypes,isCloudGpuType} from '../cloud-types.mjs';
import {localModels} from '../server/local-ai.mjs';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {mkdir,writeFile} from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';
import {cloudConfig,cloudJournalDirectory} from '../server/cloud-services.mjs';
import {createCloudClient,providerUnavailable} from '../server/cloud-client.mjs';
import {createCloudJournal} from '../server/cloud-journal.mjs';

const shapes={cpu:'m6a.2xlarge',gpu:'g6.2xlarge'};
const check=(condition,code)=>{if(!condition)throw Error(code);};

export async function prepareValidation(api,{kind='cpu',candidateId=shapes[kind],lifetimeSeconds=2400,availabilityZone,appId,save=async()=>{}}={}){
  check(kind in shapes,'invalid_validation_kind');
  check((kind==='cpu'?['m6a.2xlarge']:gpuVmTypes).includes(candidateId),'invalid_validation_candidate');
  check(Number.isInteger(lifetimeSeconds)&&lifetimeSeconds>=900&&lifetimeSeconds<=2400,'invalid_validation_lifetime');
  const ready=await api('/cloud/readiness');
  check(ready.pending_cleanup?.length===0,'previous_cleanup_required');
  check(ready.connection_configured&&ready.provider_audit_configured,'connection_configuration_required');
  const {local}=await api('/execution-options'),model={...localModels[0]};
  const app_id=appId??'khu-cloud-'+kind+'-validation';check(/^[a-z][a-z0-9-]{1,100}$/.test(app_id),'invalid_validation_app');
  const app_spec={app_id,app_version:'1.0.0',workload_type:'CLASSIFICATION',request_pattern:'ONLINE',cpu_cores:2,memory_mib:2048,storage_gib:20,replicas_min:1,replicas_max:1,latency_p95_ms_max:5000,artifact:local.artifact};
  if(kind==='gpu')Object.assign(app_spec,{accelerator_type:'GPU',accelerator_count:1,accelerator_memory_mib_per_device:16000});
  let r=await api('/flows',{execution_target:'cloud',replica_group_enabled:true,application:{app_id,app_version:'1.0.0'},model,analysis_input:{input_type:'structured',app_spec},workload:{...model,min_throughput:1,max_batch_p95_ms:1000,input_count:64,objective:'hourly'}});
  await save(r);
  const c=r.candidates?.all.find(x=>x.name===candidateId&&x.selectable);
  check(c,'selected_vm_unavailable');
  r=await api('/flows/'+r.flow_id+'/selection',{candidate_id:c.candidate_id,expected_selection_version:r.selection_version});
  r=await api('/flows/'+r.flow_id+'/plan',{selection_version:r.selection_version});
  check(r.geon?.decision?.action==='DEPLOY'&&r.geon?.guard?.status==='APPROVED','approved_plan_required');
  r=await api('/flows/'+r.flow_id+'/cloud/review',{selection_version:r.selection_version,max_lifetime_seconds:lifetimeSeconds,...availabilityZone?{availability_zone:availabilityZone}:{}});
  await save(r);
  check(r.cloud?.state==='REVIEWED'&&r.cloud.review.ready_for_approval&&r.cloud.review.image_id,'complete_cost_review_required');
  return r;
}

export function confirmedCleanup(r){
  const c=r.cloud,provider=c?.last_provider_status,attachments=c?.cleanup?.attachments;
  return c?.state==='CLEANED'&&c.cleanup.confirmed===true&&provider?.state==='Terminated'&&provider.instance_id===c.resource?.provider_instance_id&&attachments?.verified===true&&Array.isArray(attachments.remaining)&&attachments.remaining.length===0;
}
function confirmedNoCreate(r){const c=r.cloud;return c?.state==='CREATE_REJECTED'&&c.cleanup?.confirmed===true&&c.cleanup.not_created===true&&c.rejection?.confirmed===true&&c.rejection.not_created===true&&c.rejection.infra_id===c.intent.infra_id&&c.rejection.intent_sha256===c.intent.intent_sha256&&c.rejection.event_ids?.length>0;}
export function assertMeasured(r){
  const m=r.measurement,gpu=isCloudGpuType(r.cloud.intent.candidate_id),p=r.cloud.app?.proof;
  check((p?.execution_mode==='ssh_guest'||p?.execution_mode==='ssm_guest'&&p.management_transport==='ssm')&&p.forward_finite===true,'actual_guest_inference_required');
  check(m?.source==='cloud_probe'&&m.flow_id===r.flow_id&&m.sample_count===64,'actual_cloud_measurement_required');
  check(['model_id','model_revision','batch_size','max_length'].every(k=>m.model?.[k]===localModels[0][k]),'pinned_model_required');
  check(m.metrics.inference.error_rate_percent===0&&Number.isFinite(m.metrics.inference.latency_p95_ms)&&m.metrics.inference.latency_p95_ms>0&&Number.isFinite(m.metrics.inference.throughput_rps)&&m.metrics.inference.throughput_rps>0,'inference_metrics_required');
  check(Number.isFinite(m.metrics.resource.cpu_average_percent)&&Number.isFinite(m.metrics.resource.memory_peak_mib)&&m.metrics.resource.memory_peak_mib>0,'resource_metrics_required');
  if(gpu)check(p.cuda_available===true&&String(p.model_device).startsWith('cuda:')&&String(p.input_device).startsWith('cuda:')&&m.sample?.device==='cuda'&&String(m.sample.model_device).startsWith('cuda:')&&String(m.sample.input_device).startsWith('cuda:')&&Number.isFinite(m.metrics.resource.accelerator_average_percent),'cuda_inference_metrics_required');
  else check(p.model_device==='cpu'&&m.sample?.device==='cpu'&&m.sample.model_device==='cpu','cpu_inference_required');
  check(r.feedback_state==='RECORDED'&&r.feedback?.geon?.operation_agent_execution?.status==='completed'&&r.feedback.geon.operation_agent_execution.scaling_guard?.status==='APPROVED'&&typeof r.feedback.geon.scaling_decision?.action==='string','operation_feedback_required');
}

export async function executeValidation(prepared,{api,approved=false,computeBudgetUsd,save=async()=>{},progress=()=>{},clock=Date.now,pause=delay,cleanupTimeoutMs=600000,diagnose}={}){
  check(approved===true,'explicit_cost_approval_required');
  check(Number.isFinite(computeBudgetUsd)&&computeBudgetUsd>0&&prepared.cloud.review.max_compute_usd<=computeBudgetUsd+1e-8,'reviewed_compute_budget_exceeded');
  check(prepared.cloud.state==='REVIEWED'&&prepared.cloud.intent.flow_id===prepared.flow_id,'reviewed_flow_required');
  const ready=await api('/cloud/readiness');check(ready.enabled===true,'live_cloud_disabled');check(ready.pending_cleanup?.length===0,'previous_cleanup_required');
  const id=prepared.flow_id,path='/flows/'+id;
  const report={schema_version:1,flow_id:id,run_id:prepared.cloud.run_id,candidate_id:prepared.cloud.intent.candidate_id,started_at:new Date(clock()).toISOString(),vm_create_requested:false,cloud_deployment_verified:false,prometheus_verified:false,cleanup_verified:false,passed:false,scope:'One actual pinned BERT Tiny deployment; 64 requests; no automatic VM retry or replica scaling; performance comparison is separate'};
  let createRequested=false,r=prepared;
  const persist=()=>save(report);
  try{
    r=await api(path+'/cloud/approve',{cost_review_id:r.cloud.review.cost_review_id,intent_sha256:r.cloud.intent.intent_sha256});
    r=await api(path+'/cloud/preflight',{});check(r.cloud.preflight?.ready===true&&r.cloud.state==='READY','cloud_preflight_failed');
    progress({stage:'PREFLIGHT_PASSED',flow_id:id});
    // Mark uncertainty before sending. A lost response must never cause another create.
    createRequested=true;report.vm_create_requested=true;await persist();
    progress({stage:'VM_CREATE_REQUESTED',flow_id:id,run_id:report.run_id});
    await api(path+'/cloud/create',{});
    const deadline=clock()+1200000;let previousState;
    for(;;){
      r=await api(path);
      check(r.cloud?.run_id===report.run_id,'cloud_run_changed');
      if(r.cloud.state!==previousState){previousState=r.cloud.state;progress({stage:previousState,instance_id:r.cloud.resource?.provider_instance_id});}
      if(r.cloud.state==='RUNNING'&&r.execution?.status==='RUNNING')break;
      check(!['CLEANED','CLEANUP_PENDING','CREATE_UNKNOWN','CREATE_REJECTED'].includes(r.cloud.state),'cloud_deployment_failed');
      check(clock()<deadline&&(!r.cloud.deadline_at||clock()<Date.parse(r.cloud.deadline_at)-120000),'deployment_timeout');
      await pause(5000);
    }
    report.provider_instance_id=r.cloud.resource?.provider_instance_id;report.bootstrap_proof=r.cloud.app?.proof;
    check(r.execution.mode==='cloud_vm'&&r.replica_group?.actual_replicas===1,'real_replica_required');await persist();
    // Give file discovery time to collect a baseline before the actual request window.
    await pause(12000);
    r=await api(path+'/appdeploy/measure',{selection_version:r.selection_version});
    report.measurement=r.measurement;report.operation=r.feedback?.geon?.scaling_decision;report.operation_guard=r.feedback?.geon?.operation_agent_execution?.scaling_guard;
    assertMeasured(r);report.cloud_deployment_verified=true;await persist();
    progress({stage:'ACTUAL_INFERENCE_MEASURED',requests:r.measurement.sample_count,device:r.measurement.sample.device,p95_ms:r.measurement.metrics.inference.latency_p95_ms,operation:report.operation?.action});
    const promDeadline=clock()+90000;
    for(;;){
      const prom=await api(path+'/prometheus');report.prometheus={state:prom.state,server_ready:prom.server_ready,observation:prom.observation};
      if(prom.can_feedback&&prom.observation?.source==='prometheus'&&prom.observation.flow_id===id&&prom.observation.group_id===r.replica_group.group_id&&prom.observation.membership_epoch===r.replica_group.membership_epoch&&prom.observation.sample_count>0){report.prometheus_verified=true;progress({stage:'PROMETHEUS_COLLECTED',requests:prom.observation.sample_count});break;}
      check(clock()<promDeadline,'prometheus_cloud_collection_unverified');await pause(5000);
    }
  }catch(e){report.error_code=/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'validation_failed';
    if(createRequested&&diagnose){
      progress({stage:'RUNTIME_DIAGNOSIS_STARTED'});
      try{report.runtime_diagnosis=await diagnose({receipt:r,report});progress({stage:'RUNTIME_DIAGNOSIS_COLLECTED',health:report.runtime_diagnosis?.health,local_inference:report.runtime_diagnosis?.local_inference});}
      catch(d){const safe=providerUnavailable(d.diagnostic_code,d.ssh_diagnostic);report.runtime_diagnosis={failed:true,diagnostic_code:safe.diagnostic_code,ssh_diagnostic:safe.ssh_diagnostic};}
      await persist();
    }
  }
  finally{
    if(createRequested){
      const deadline=clock()+cleanupTimeoutMs;
      progress({stage:'CLEANUP_REQUESTED',flow_id:id});
      for(;;){
        try{
          const closed=await api(path+'/cloud/cleanup',{});report.cleanup=closed.cloud?.cleanup;report.provider_status=closed.cloud?.last_provider_status;report.failure=closed.cloud?.failure;
          if(closed.cloud?.runtime_diagnosis)report.runtime_diagnosis=closed.cloud.runtime_diagnosis;
          if(confirmedNoCreate(closed)){report.cleanup_verified=true;report.creation_rejected=true;report.vm_created=false;report.cleanup_required=false;report.rejection=closed.cloud.rejection;report.error_code='provider_capacity_unavailable';progress({stage:'PROVIDER_REJECTION_AND_NO_RESOURCES_CONFIRMED'});break;}
          report.provider_instance_id??=closed.cloud?.resource?.provider_instance_id;
          if(confirmedCleanup(closed)){report.cleanup_verified=true;progress({stage:'AWS_TERMINATION_AND_ATTACHMENTS_CONFIRMED',instance_id:closed.cloud.resource.provider_instance_id});break;}
          // A definitively rejected request creates nothing; never call that a successful deployment.
          if(closed.cloud?.run_id!==report.run_id)throw Error('cloud_run_changed');
        }catch{/* Persist uncertainty and let the independent watcher retain supervision. */}
        await persist();if(clock()>=deadline){report.error_code='cleanup_unconfirmed';break;}await pause(10000);
      }
    }
    report.passed=report.cloud_deployment_verified&&report.prometheus_verified&&report.cleanup_verified;
    report.ended_at=new Date(clock()).toISOString();await persist();
  }
  return report;
}

export async function platformApi(path,body){
  const r=await fetch('http://127.0.0.1:18100/api/v1'+path,{method:body===undefined?'GET':'POST',headers:{'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(path.endsWith('/appdeploy/measure')?300000:100000)});
  const value=await r.json();if(!r.ok)throw Error(value.error?.code??'provider_unavailable');return value;
}

async function main(){
  const args=process.argv.slice(2),option=name=>{const i=args.indexOf(name);return i<0?undefined:args[i+1];};
  const kind=option('--kind')??'cpu',execute=args.includes('--execute-approved'),budget=Number(option('--compute-budget-usd'));
  const valueOptions=['--kind','--compute-budget-usd','--availability-zone','--candidate-id','--lifetime-seconds'];
  check(args.every((a,i)=>[...valueOptions,'--execute-approved'].includes(a)||i>0&&valueOptions.includes(args[i-1])),'unsupported_arguments');
  check(kind in shapes,'invalid_validation_kind');if(execute)check(Number.isFinite(budget)&&budget>0,'compute_budget_required');
  const root=fileURLToPath(new URL('../../',import.meta.url)),directory=resolve(root,'output/khu-platform-validation/cloud-live-validation');
  await mkdir(directory,{recursive:true});const stamp=new Date().toISOString().replace(/[:.]/g,'-'),preparedFile=resolve(directory,kind+'-prepared-'+stamp+'.json'),resultFile=resolve(directory,kind+'-result-'+stamp+'.json');
  const savePrepared=r=>writeFile(preparedFile,JSON.stringify(r,null,2));
  const prepared=await prepareValidation(platformApi,{kind,candidateId:option('--candidate-id'),lifetimeSeconds:option('--lifetime-seconds')===undefined?2400:Number(option('--lifetime-seconds')),availabilityZone:option('--availability-zone'),save:savePrepared});
  console.log(JSON.stringify({mode:execute?'APPROVED_EXECUTION':'REVIEW_ONLY',flow_id:prepared.flow_id,run_id:prepared.cloud.run_id,vm_create_requested:false,candidate_id:prepared.cloud.intent.candidate_id,review:prepared.cloud.review,prepared_file:preparedFile}));
  if(!execute)return;
  const diagnose=async({report})=>{
    const run=await createCloudJournal(cloudJournalDirectory).read(report.run_id);
    check(run.intent.flow_id===report.flow_id&&run.resource?.provider_instance_id&&['CREATED','PREPARING','RUNNING'].includes(run.state),'owned_running_vm_required');
    return createCloudClient(await cloudConfig()).guestRuntimeProbe({...run.intent,...run.resource});
  };
  const result=await executeValidation(prepared,{api:platformApi,approved:true,computeBudgetUsd:budget,diagnose,save:r=>writeFile(resultFile,JSON.stringify(r,null,2)),progress:r=>console.log(JSON.stringify({at:new Date().toISOString(),...r}))});
  console.log(JSON.stringify({...result,measurement:result.measurement?{source:result.measurement.source,requests:result.measurement.sample_count,metrics:result.measurement.metrics}:undefined,bootstrap_proof:undefined,prometheus:result.prometheus?{state:result.prometheus.state,sample_count:result.prometheus.observation?.sample_count}:undefined,saved_to:resultFile},null,2));
  if(!result.passed)process.exitCode=2;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(JSON.stringify({error_code:/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'validation_failed'}));process.exitCode=2;});
