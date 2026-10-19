import {cloudRuntimeIssue} from '../cloud-types.mjs';
import {cloudServices} from './cloud-services.mjs';import {activeCloudRun} from './cloud-execution.mjs';import {projectApprovedRevision,replicaDeploymentRequest,actualGroupStatus} from './replica-execution.mjs';import {buildVmArtifact} from './vm-artifact.mjs';import {prepareVmApp,vmSshSettleMs} from './vm-appdeploy.mjs';import {digest,fault} from './index.mjs';import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';
import {bindReplicaWorkers} from './replica-probe.mjs';
import {cloudApplicationDeployed} from '../cloud-state.mjs';
import {providerUnavailable} from './cloud-client.mjs';
import {createCloudCampaignRoutes} from './cloud-campaign-routes.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
export function safeRuntimeDiagnosisFailure(error){
 const safe=providerUnavailable(error?.diagnostic_code,error?.ssh_diagnostic);
 return {failed:true,...(safe.diagnostic_code?{diagnostic_code:safe.diagnostic_code}:{}),...(safe.ssh_diagnostic?{ssh_diagnostic:safe.ssh_diagnostic}:{})};
}
export function createCloudRoutes({services}={}){
 let servicePromise=services?Promise.resolve(services):undefined;
 const load=ctx=>servicePromise??=cloudServices(ctx);
 const campaignRoutes=createCloudCampaignRoutes({load,childRoute:ctx=>handler(ctx)});
 async function bound(r,s){if(!r.cloud_run_id)throw fault(409,'cloud_review_required');const run=await s.journal.read(r.cloud_run_id);const p=projectApprovedRevision(r,await s.clients.flow(r.geon_flow_id));if(run.intent.flow_id!==r.flow_id||run.intent.selection_version!==r.selection_version||run.intent.spec_sha256!==p.spec_sha256)throw fault(409,'cloud_plan_changed');return run;}
 async function publish(store,id,run){return store.update(id,r=>{
  if(r.cloud?.run_id===run.run_id&&(r.cloud.journal_revision??0)>(run.journal_revision??0))return r;
  const closed=['CLEANED','CREATE_REJECTED'].includes(run.state)&&run.cleanup?.confirmed===true;
  const deployed=cloudApplicationDeployed(r,run);
  return {...r,cloud:run,cloud_run_id:run.run_id,...closed?{cloud_job_requested:false,
   execution:deployed?{...r.execution,mode:'cloud_vm',source:'provider_termination',status:'STOPPED',actual_replicas:0,observed_at:run.cleanup.confirmed_at??new Date().toISOString()}:r.execution?.source==='provider_termination'?undefined:r.execution,
   appdeploy:deployed&&r.appdeploy?{...r.appdeploy,status:'STOPPED',stop_source:'provider_termination'}:r.appdeploy}: {}};
 });}
 async function startApp(ctx,s,run){const {store,clients}=ctx,id=run.intent.flow_id;let r=await store.read(id);try{
  const artifact=await buildVmArtifact(r.input.model,resolve(root,'output/khu-platform-validation/vm-artifacts',run.run_id));if(run.review.artifact_sha256!==artifact.sha256)throw Error('artifact_changed');
  const registered=await prepareVmApp({run,journal:s.journal,client:s.client,clients,receipt:r,artifact,sshSettleMs:vmSshSettleMs()}),delivery=await clients.appPrepare(r.geon_flow_id,{app_version_id:registered.app.app_version_id});
  r=await store.update(id,x=>({...x,execution_mode:'cloud_vm',appdeploy:{group_schema:1,status:'PREPARED',app_version_id:registered.app.app_version_id,target_profile_id:registered.target.target_profile_id,delivery}}));
  const p=projectApprovedRevision(r,await clients.flow(r.geon_flow_id));if(p.revision!==1||p.desired_replicas!==1)throw Error('initial_cloud_replica_required');
  if((await s.journal.read(run.run_id)).state!=='PREPARING')throw Error('cloud_cleanup_in_progress');
  const group_intent={flow_id:id,app_version_id:r.appdeploy.app_version_id,target_profile_id:r.appdeploy.target_profile_id,spec_sha256:p.spec_sha256,idempotency_key:run.run_id+'-initial',deployment:replicaDeploymentRequest(r)};
  await store.update(id,x=>({...x,group_intent,appdeploy:{...x.appdeploy,status:'PENDING'}}));const g=await clients.groupCreate(group_intent);
  if(g.operation_state!=='IDLE'||g.actual_replicas!==1||g.flow_id!==id||g.target_profile_id!==r.appdeploy.target_profile_id)throw Error('cloud_app_unconfirmed');
  const worker_bindings=await bindReplicaWorkers(clients,r,g);
  run=await s.journal.update(run.run_id,x=>{if(x.state!=='PREPARING')throw Error('cloud_cleanup_in_progress');return {...x,state:'RUNNING',app:{...x.app,group_id:g.group_id}};});
  r=await store.update(id,x=>({...x,worker_bindings,cloud:run,replica_group:g,appdeploy:{...x.appdeploy,status:'RUNNING',group_id:g.group_id},execution:{mode:'cloud_vm',source:'appdeploy',status:'RUNNING',execution_id:g.group_id,group_id:g.group_id,target_profile_id:g.target_profile_id,app_version_id:g.app_version_id,actual_replicas:g.actual_replicas,applied_revision:g.applied_revision,observed_at:new Date().toISOString()},history:[...x.history,{event:'cloud_app_running',at:new Date().toISOString()}]}));
  await clients.deploymentStatus(actualGroupStatus(r,g));const {syncReplicaTargets}=await import('./prometheus-targets.mjs');await syncReplicaTargets(await clients.groupList());
 }catch(e){const operation=e.appdeploy_diagnostic?.operation,httpStatus=e.appdeploy_diagnostic?.http_status;
  const appdeployDiagnostic=['credentials','target-profiles','apps','deployment-groups','inference'].includes(operation)&&Number.isInteger(httpStatus)&&httpStatus>=400&&httpStatus<=599?{operation,http_status:httpStatus}:null;
  await s.journal.update(run.run_id,x=>({...x,error_code:['bootstrap_timeout','artifact_changed','attachment_audit_required'].includes(e.message)?e.message:'cloud_app_prepare_failed',failure:x.failure??{stage:x.state==='RUNNING'?'POST_DEPLOY':x.app?'APP_EXECUTION':'APP_PREPARATION',code:'cloud_app_prepare_failed',...(appdeployDiagnostic?{appdeploy_diagnostic:appdeployDiagnostic}:{})}}));
  const failed=await s.journal.read(run.run_id);
  if(typeof s.client.guestRuntimeProbe==='function'&&failed.resource?.provider_instance_id&&['PREPARING','RUNNING'].includes(failed.state)&&Date.now()+150000<Date.parse(failed.deadline_at)){
   try{const runtime_diagnosis=await s.client.guestRuntimeProbe({...failed.intent,...failed.resource});await s.journal.update(run.run_id,x=>({...x,runtime_diagnosis}));}
   catch(error){await s.journal.update(run.run_id,x=>({...x,runtime_diagnosis:safeRuntimeDiagnosisFailure(error)}));}
  }
  run=await s.execution.cleanup(run.run_id);await publish(store,id,run);}}
 const handler=async ctx=>{
  const {request,path,value,store,clients}=ctx;
  const campaign=await campaignRoutes(ctx);if(campaign)return campaign;
  if(path==='/api/v1/cloud/readiness'&&request.method==='GET'){const s=await load(ctx);return {data:{enabled:s.config.enabled===true,ssh_settle_seconds:vmSshSettleMs()/1000,connection_configured:!!s.config.ssh_key,provider_audit_configured:!!s.config.aws_cli&&!!s.config.aws_profile,checks:{ssh_key:!!s.config.ssh_key,cpu_image:!!s.config.image_ids?.cpu,gpu_image:!!s.config.image_ids?.gpu,provider_audit:!!s.config.aws_cli&&!!s.config.aws_profile,execution_enabled:s.config.enabled===true},pending_cleanup:[...(await s.journal.list()).filter(activeCloudRun).map(r=>({run_id:r.run_id,flow_id:r.intent.flow_id,state:r.state,instance_id:r.resource?.provider_instance_id})),...(s.campaigns?(await s.campaigns.list()).filter(p=>['ACTIVE','CLEANUP_PENDING'].includes(p.state)).map(p=>({run_id:p.run_id,state:p.state,phase:p.phase,kind:'vm_count_campaign'})):[])]}};}
  const match=path.match(/^\/api\/v1\/flows\/([\w-]+)\/cloud\/(review|approve|preflight|create|status|cleanup)$/);if(!match)return;
  const [,id,action]=match;if(request.method!==(action==='status'?'GET':'POST'))throw fault(405,'method_not_allowed');const s=await load(ctx);s.clients=clients;let r=await store.read(id);
  if(r.input.execution_target!=='cloud')throw fault(409,'cloud_execution_path_required');
  let run;
  if(action==='review'){
   if(r.cloud_run_id){const previous=await s.journal.read(r.cloud_run_id);if(activeCloudRun(previous))throw fault(409,'previous_cleanup_required');if(previous.resource?.provider_instance_id)throw fault(409,'cloud_fresh_experiment_required');}
   if(value.selection_version!==r.selection_version)throw fault(409,'stale_selection');const p=projectApprovedRevision(r,await clients.flow(r.geon_flow_id));if(p.revision!==1||p.desired_replicas!==1||r.application_profile.requirements.deployment.replicas_max>2)throw fault(422,'initial_cloud_plan_required');
   const candidate=r.candidates.all.find(c=>c.candidate_id===r.selected_candidate_id);if(!candidate)throw fault(422,'candidate_unavailable');const issue=cloudRuntimeIssue(r.application_profile.requirements,candidate);if(issue)throw fault(422,issue.code);
   const artifact=await buildVmArtifact(r.input.model);run=await s.execution.review({flow_id:id,selection_version:r.selection_version,spec_sha256:p.spec_sha256,candidate_id:candidate.name,region:'us-west-2',max_lifetime_seconds:value.max_lifetime_seconds??2700,...value.availability_zone?{availability_zone:value.availability_zone}:{}});run=await s.journal.update(run.run_id,x=>({...x,review:{...x.review,artifact_sha256:artifact.sha256}}));
  }else if(action==='cleanup'||action==='status'){
   if(!r.cloud_run_id)throw fault(409,'cloud_review_required');const owned=await s.journal.read(r.cloud_run_id);if(owned.intent.flow_id!==id)throw fault(409,'cloud_ownership_mismatch');run=await s.execution[action](owned.run_id);
  }else{
   run=await bound(r,s);
   if(action==='approve')run=await s.execution.approve(run.run_id,value);
   if(action==='preflight')run=await s.execution.preflight(run.run_id);
   if(action==='create'){
    if(!s.config.enabled)throw fault(409,'live_cloud_disabled');if(r.cloud_job_requested)throw fault(409,'reconcile_existing_cloud_run');
    await store.update(id,x=>({...x,cloud_job_requested:true,history:[...x.history,{event:'cloud_pipeline_requested',at:new Date().toISOString()}]}));
    const runId=run.run_id;
    (async()=>{try{const created=await s.execution.create(runId);await publish(store,id,created);await startApp(ctx,s,created);}catch{
     const current=await s.journal.read(runId);if(!activeCloudRun(current)&&current.state!=='CLEANED')await store.update(id,x=>({...x,cloud_job_requested:false}));await publish(store,id,current);
    }})().catch(()=>{});
   }
  }
  r=await publish(store,id,run);return {data:r};
 };
 // Explicit operator continuation of a settled creation; never exposed by GET or HTTP routing.
 handler.resumeOwnedApplication=async(ctx,id)=>{
  const s=await load(ctx);s.clients=ctx.clients;const receipt=await ctx.store.read(id);
  if(receipt.input.execution_target!=='cloud'||!receipt.cloud_job_requested||receipt.appdeploy||receipt.group_intent||receipt.execution)throw fault(409,'creation_recovery_unavailable');
  await bound(receipt,s);const run=await s.execution.confirmCreated(receipt.cloud_run_id);
  await publish(ctx.store,id,run);await startApp(ctx,s,run);return ctx.store.read(id);
 };
 return handler;
}
