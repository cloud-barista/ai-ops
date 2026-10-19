import {prepareValidation,platformApi,assertMeasured,confirmedCleanup} from './validate-cloud-vm.mjs';
import {randomUUID} from 'node:crypto';import {mkdir,writeFile} from 'node:fs/promises';import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';import {setTimeout as delay} from 'node:timers/promises';
const check=(v,code)=>{if(!v)throw Error(code);};
export async function executeCampaignValidation(prepared,{api,approved=false,computeBudgetUsd,save=async()=>{},progress=()=>{},clock=Date.now,pause=delay,waitTimeoutMs=1200000,cleanupTimeoutMs=600000}={}){
 check(approved,'explicit_cost_approval_required');check(Number.isFinite(computeBudgetUsd)&&computeBudgetUsd>0&&computeBudgetUsd<=0.4608&&prepared.review.max_compute_usd<=computeBudgetUsd+1e-8,'reviewed_compute_budget_exceeded');
 const ready=await api('/cloud/readiness');check(ready.enabled&&ready.pending_cleanup.length===0,'cloud_not_ready');
 const base='/cloud-campaigns/'+prepared.run_id,ids=prepared.intent.members.map(m=>m.flow_id);
 const report={schema_version:1,campaign_id:prepared.run_id,flow_ids:ids,started_at:new Date(clock()).toISOString(),scope:'Explicit user-requested CPU VM count changes; not autonomous GEON VM scaling or comparative performance',snapshots:[],measurements:[],prometheus:[],vm_count_verified:false,cleanup_verified:false,passed:false};
 let mutation=false;
 const event=(stage,extra={})=>progress({stage,...extra});
 async function waitPhase(phase){const deadline=clock()+waitTimeoutMs;for(;;){const p=await api(base+'/status');check(!p.error_code,'campaign_child_failed');if(p.phase===phase)return p;check(p.state!=='CLEANED'&&p.state!=='CLEANUP_PENDING'&&clock()<deadline,'campaign_phase_timeout');await pause(5000);}}
 async function snapshot(expected){const p=await api(base+'/status'),s=p.provider_snapshot;check(s?.verified&&s.running_vm_count===expected,'actual_vm_count_unverified');report.snapshots.push({...s,phase:p.phase});await save(report);event('AWS_VM_COUNT_CONFIRMED',{count:expected});}
 async function measure(id,phase){let r=await api('/flows/'+id);check(r.cloud?.state==='RUNNING'&&r.execution?.status==='RUNNING'&&r.replica_group?.actual_replicas===1,'running_child_app_required');await pause(12000);
  r=await api('/flows/'+id+'/appdeploy/measure',{selection_version:r.selection_version});assertMeasured(r);
  report.measurements.push({phase,flow_id:id,instance_id:r.cloud.resource.provider_instance_id,bootstrap_proof:r.cloud.app.proof,measurement:r.measurement,operation:r.feedback.geon.scaling_decision,guard:r.feedback.geon.operation_agent_execution.scaling_guard});await save(report);
  const deadline=clock()+90000;for(;;){const p=await api('/flows/'+id+'/prometheus');if(p.can_feedback&&p.observation?.source==='prometheus'&&p.observation.flow_id===id&&p.observation.group_id===r.replica_group.group_id&&p.observation.membership_epoch===r.replica_group.membership_epoch&&p.observation.sample_count>0){report.prometheus.push({phase,flow_id:id,...p});break;}check(clock()<deadline,'child_prometheus_unverified');await pause(5000);}
  await save(report);event('CHILD_INFERENCE_VERIFIED',{phase,flow_id:id,requests:64,instance_id:r.cloud.resource.provider_instance_id});
 }
 try{
  mutation=true;await save(report);await api(base+'/approve',{intent_sha256:prepared.intent.intent_sha256,review_sha256:prepared.review.review_sha256,compute_budget_usd:computeBudgetUsd});event('CAMPAIGN_APPROVED');
  await api(base+'/start',{});event('FIRST_VM_REQUESTED');await waitPhase('SINGLE_RUNNING');await measure(ids[0],'initial');await snapshot(1);
  await api(base+'/expand',{});event('SECOND_VM_REQUESTED');await waitPhase('DUAL_RUNNING');await measure(ids[0],'dual');await measure(ids[1],'dual');
  const unique=new Set(report.measurements.filter(m=>m.phase==='dual').map(m=>m.instance_id));check(unique.size===2,'distinct_actual_vms_required');await snapshot(2);
  await api(base+'/shrink',{});event('SHRINK_REQUESTED');await waitPhase('SINGLE_AFTER_SHRINK');
  const removed=await api('/flows/'+ids[1]+'/cloud/status');check(confirmedCleanup(removed),'removed_vm_cleanup_unverified');
  await measure(ids[0],'after_shrink');await snapshot(1);report.vm_count_verified=true;
 }catch(e){report.error_code=/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'campaign_validation_failed';event('VALIDATION_FAILED',{error_code:report.error_code});}
 finally{
  if(mutation){event('ALL_VM_CLEANUP_REQUESTED');
   const deadline=clock()+cleanupTimeoutMs;for(;;){try{await api(base+'/stop',{});}catch{}try{
    const p=await api(base+'/status');report.final_campaign=p;
    const closed=await Promise.all(ids.map(id=>api('/flows/'+id+'/cloud/status')));report.child_cleanup=closed.map(r=>({flow_id:r.flow_id,cloud:r.cloud}));
    const allClosed=closed.every(r=>confirmedCleanup(r)||!r.cloud?.approval?.consumed_at&&!r.cloud?.resource||r.cloud?.state==='CREATE_REJECTED'&&r.cloud?.cleanup?.confirmed&&r.cloud.cleanup.not_created);
    if(p.state==='CLEANED'&&p.cleanup?.confirmed&&p.provider_snapshot?.verified&&p.provider_snapshot.running_vm_count===0&&allClosed){report.connection_audit=await api(base+'/connections');if(report.connection_audit.verified){report.cleanup_verified=true;report.snapshots.push({...p.provider_snapshot,phase:p.phase});break;}}
   }catch{}await save(report);if(clock()>=deadline){report.cleanup_error='cleanup_unconfirmed';break;}await pause(10000);}
  }
  report.passed=report.vm_count_verified&&report.cleanup_verified&&report.measurements.length===4&&report.prometheus.length===4&&JSON.stringify(report.snapshots.map(s=>s.running_vm_count))==='[1,2,1,0]';report.ended_at=new Date(clock()).toISOString();await save(report);
 }return report;
}
async function main(){
 const args=process.argv.slice(2),option=name=>{const i=args.indexOf(name);return i<0?undefined:args[i+1];};check(args.every((a,i)=>['--execute-approved','--compute-budget-usd'].includes(a)||i>0&&args[i-1]==='--compute-budget-usd'),'unsupported_arguments');
 const root=fileURLToPath(new URL('../../',import.meta.url)),directory=resolve(root,'output/khu-platform-validation/cloud-live-validation');await mkdir(directory,{recursive:true});const stamp=new Date().toISOString().replace(/[:.]/g,'-'),suffix=randomUUID().slice(0,8),flows=[];
 for(const n of [1,2])flows.push(await prepareValidation(platformApi,{kind:'cpu',appId:'khu-campaign-'+suffix+'-'+n,lifetimeSeconds:2400}));
 const prepared=await platformApi('/cloud-campaigns',{flow_ids:flows.map(r=>r.flow_id)});await writeFile(resolve(directory,'campaign-prepared-'+stamp+'.json'),JSON.stringify({campaign:prepared,flows},null,2));
 console.log(JSON.stringify({mode:args.includes('--execute-approved')?'APPROVED_EXECUTION':'REVIEW_ONLY',campaign_id:prepared.run_id,review:prepared.review,flow_ids:flows.map(r=>r.flow_id)}));
 if(!args.includes('--execute-approved'))return;
 const resultFile=resolve(directory,'campaign-result-'+stamp+'.json');const result=await executeCampaignValidation(prepared,{api:platformApi,approved:true,computeBudgetUsd:Number(option('--compute-budget-usd')),save:r=>writeFile(resultFile,JSON.stringify(r,null,2)),progress:e=>console.log(JSON.stringify({at:new Date().toISOString(),...e}))});
 console.log(JSON.stringify({campaign_id:result.campaign_id,passed:result.passed,vm_count_verified:result.vm_count_verified,cleanup_verified:result.cleanup_verified,counts:result.snapshots.map(s=>s.running_vm_count),error_code:result.error_code,cleanup_error:result.cleanup_error,saved_to:resultFile}));if(!result.passed)process.exitCode=2;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(JSON.stringify({error_code:/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'campaign_failed'}));process.exitCode=2;});
