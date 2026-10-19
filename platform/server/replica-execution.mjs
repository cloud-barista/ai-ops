import {fault,digest} from './index.mjs';
import {envelope,assertPlanBinding,toFeedbackMessages} from './geon-adapter.mjs';
import {requireSelectedAgents,assertAgentExecution,assertAgentSelectionBinding} from './agents.mjs';
import {measureReplicaGroup,bindReplicaWorkers} from './replica-probe.mjs';
import {syncReplicaTargets} from './prometheus-targets.mjs';

export function replicaDeploymentRequest(r){const request=structuredClone(r.appdeploy.delivery.request);request.app_version_id=r.appdeploy.app_version_id;request.target_profile_id=r.appdeploy.target_profile_id??r.selected_candidate_id;request.manifest.spec.target_profile_id=request.target_profile_id;for(const requirements of [request.requirements,request.manifest.spec.requirements])if(requirements){delete requirements.command;delete requirements.args;}return request;}

export function projectApprovedRevision(r,f){
 assertPlanBinding({receipt:r,geonFlow:f,selectionVersion:r.selection_version});
 if(r.geon?.decision?.action!=='DEPLOY'||r.geon?.guard?.status!=='APPROVED')throw fault(409,'approved_plan_required');
 const revisions=f.manifest_revisions,latest=revisions?.at(-1),first=revisions?.[0];
 const manifest=x=>x?.deployment_request?.data?.deployment_request?.deployment_manifest;
 const initial=manifest(first),m=manifest(latest),count=m?.inference_configuration?.replicas;
 if(!m||m.desired_infrastructure?.node_count!==1||!Number.isInteger(count)||count<1||count>2||JSON.stringify(m.application)!==JSON.stringify(initial.application)||JSON.stringify(m.desired_infrastructure)!==JSON.stringify(initial.desired_infrastructure)||JSON.stringify({...m.inference_configuration,replicas:1})!==JSON.stringify({...initial.inference_configuration,replicas:1}))throw fault(409,'unsupported_replica_revision');
 if(latest.revision>1&&(f.operation_agent_execution?.status!=='completed'||f.operation_agent_execution.scaling_guard?.status!=='APPROVED'||f.scaling_decision?.desired_replicas!==count))throw fault(409,'approved_revision_required');
 return {revision:latest.revision,desired_replicas:count,spec_sha256:digest(m),manifest:m};
}
export function actualGroupStatus(r,g){
 const revisions=r.feedback?.geon?.manifest_revisions??r.geon.manifest_revisions;
 const applied=revisions?.find(v=>v.revision===g.applied_revision),at=new Date().toISOString();
 return {...envelope(r,'deployment.status.changed',at),...(applied?.deployment_request?.message_id?{causation_id:applied.deployment_request.message_id}:{}),data:{deployment_status:{deployment_id:g.group_id,decision_id:r.geon.decision.decision_id,state:g.operation_state==='IDLE'?'RUNNING':g.operation_state==='STOPPED'?'STOPPED':'UNKNOWN',actual_replicas:g.actual_replicas,applied_revision:g.applied_revision,group_id:g.group_id,execution_mode:r.execution_mode==='cloud_vm'?'cloud_vm':'local_process',updated_at:at,actual_infrastructure:{resource_ids:[g.target_profile_id]},message:'AppDeploy confirmed replica membership'}}};
}
function bound(r,g){if(g.flow_id!==r.flow_id||g.app_version_id!==r.appdeploy.app_version_id||g.target_profile_id!==(r.appdeploy.target_profile_id??r.selected_candidate_id))throw fault(409,'group_identity_mismatch');}
export function confirmReplicaGroup(r,g){
 bound(r,g);const status=g.operation_state==='IDLE'?'RUNNING':g.operation_state==='STOPPED'?'STOPPED':'UNKNOWN',at=new Date().toISOString();
 const event=status==='STOPPED'&&r.execution?.status!=='STOPPED'?'group_stopped':status==='RUNNING'&&(r.execution?.status!=='RUNNING'||r.execution.group_id!==g.group_id)?'group_running':status==='RUNNING'&&r.execution.applied_revision!==g.applied_revision?'revision_applied':null;
 const history=event?[...(r.history??[]),{event,at,group_id:g.group_id,revision:g.applied_revision,actual_replicas:g.actual_replicas,membership_epoch:g.membership_epoch,selection_version:r.selection_version}]:r.history;
 return {...r,history,replica_group:g,appdeploy:{...r.appdeploy,status,group_id:g.group_id},execution:{...r.execution,mode:r.execution_mode,source:'appdeploy',execution_id:g.group_id,group_id:g.group_id,target_profile_id:g.target_profile_id,app_version_id:g.app_version_id,status,runtime_id:g.replicas.find(c=>c.status==='RUNNING')?.runtime_id,actual_replicas:g.actual_replicas,applied_revision:g.applied_revision,observed_at:at}};
}
export function createReplicaExecutionRoutes(){
 let slot=Promise.resolve();const exclusive=fn=>{const next=slot.catch(()=>{}).then(fn);slot=next;return next;};
 return async({request,path,value,store,clients})=>{
  const match=path.match(/^\/api\/v1\/flows\/([\w-]+)\/appdeploy\/(prepare|submit|status|stop|measure|prometheus-feedback|apply-revision)$/);if(!match)return;
  const [,id,action]=match;let r=await store.read(id);
  if(!['local','cloud'].includes(r.input.execution_target)||(!r.input.replica_group_enabled&&!r.appdeploy?.group_schema)||(r.appdeploy&&!r.appdeploy.group_schema))return;
  if(request.method!==(action==='status'?'GET':'POST'))throw fault(405,'method_not_allowed');
  return exclusive(async()=>{
   r=await store.read(id);
   if(value&&Object.keys(value).some(k=>!['selection_version','expected_applied_revision'].includes(k)))throw fault(400,'invalid_replica_request');
   if(value?.selection_version!==undefined&&value.selection_version!==r.selection_version)throw fault(409,'stale_selection');
   if((r.input.execution_target==='local'&&r.selected_candidate_id!=='khu-local-pc')||r.geon?.guard?.status!=='APPROVED')throw fault(409,'approved_real_plan_required');
   if(r.input.execution_target==='cloud'&&(['prepare','submit'].includes(action)||!r.appdeploy?.target_profile_id))throw fault(409,'cloud_pipeline_required');
   if(r.input.execution_target==='cloud'&&r.cloud?.state!=='RUNNING'&&action!=='status'&&action!=='stop')throw fault(409,'cloud_running_required');
   if(action==='prepare'){
    if(r.appdeploy)return {data:r};
    if(r.application_profile.requirements.accelerator.required||r.application_profile.requirements.deployment.replicas_max>2)throw fault(422,'replica_bounds_required');
    const app=await clients.registerLocalApp(r.application_profile,{model:r.input.model,flow_id:r.flow_id});
    const delivery=await clients.appPrepare(r.geon_flow_id,{app_version_id:app.app_version_id});
    r=await store.update(id,current=>({...current,appdeploy:{group_schema:1,status:'PREPARED',app_version_id:app.app_version_id,delivery},history:[...current.history,{event:'appdeploy_prepared',at:new Date().toISOString()}]}));return {data:r};
   }
   if(!r.appdeploy)throw fault(409,'prepare_deployment_first');
   if(action==='submit'){
    if(r.execution||r.appdeploy.status!=='PREPARED')throw fault(409,'reconcile_group_first');
    if((await clients.localDeployments()).some(d=>!d.group_id&&!['STOPPED','COMPLETED','RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED'].includes(d.status)))throw fault(409,'local_worker_busy');
    const f=await clients.flow(r.geon_flow_id),p=projectApprovedRevision(r,f);if(p.revision!==1||p.desired_replicas!==1)throw fault(409,'initial_replica_required');
    r=await store.update(id,current=>({...current,appdeploy:{...current.appdeploy,status:'PENDING'},group_intent:{flow_id:id,app_version_id:current.appdeploy.app_version_id,target_profile_id:current.selected_candidate_id,spec_sha256:p.spec_sha256,idempotency_key:id+'-initial',deployment:replicaDeploymentRequest(current)},history:[...current.history,{event:'group_requested',at:new Date().toISOString()}]}));
    try{const g=await clients.groupCreate(r.group_intent);r=await store.update(id,current=>confirmReplicaGroup(current,g));}catch{await store.update(id,current=>({...current,appdeploy:{...current.appdeploy,status:'UNKNOWN'}}));throw fault(502,'group_unknown');}
   }else{
    let groupId=r.appdeploy.group_id;
    if(!groupId){const existing=(await clients.groupList()).filter(g=>g.flow_id===id);if(existing.length!==1)throw fault(409,'group_unknown');groupId=existing[0].group_id;}
    let g=await clients.groupStatus(groupId);bound(r,g);
    if(action==='stop')g=await clients.groupStop(groupId);
    if(action==='apply-revision'){
     if(g.operation_state!=='IDLE'||value.expected_applied_revision!==g.applied_revision||['PENDING','UNKNOWN'].includes(r.feedback_state))throw fault(409,'group_revision_stale');
     const p=projectApprovedRevision(r,await clients.flow(r.geon_flow_id));if(p.revision<=g.applied_revision)throw fault(409,'no_pending_revision');
     await store.update(id,current=>({...current,replica_group:{...g,operation_state:'PENDING'},group_apply_intent:{...p,manifest:undefined},history:[...current.history,{event:'replica_apply_requested',at:new Date().toISOString(),revision:p.revision}]}));
     try{g=await clients.groupApply(groupId,{expected_applied_revision:g.applied_revision,revision:p.revision,spec_sha256:p.spec_sha256,idempotency_key:id+'-r'+p.revision,desired_replicas:p.desired_replicas});}catch(error){
      if(error.appdeploy_diagnostic?.code==='RESOURCE_INSUFFICIENT'&&[400,409].includes(error.appdeploy_diagnostic.http_status)){
       let confirmed;try{confirmed=await clients.groupStatus(groupId);bound(r,confirmed);}catch{confirmed=null;}
       if(confirmed?.operation_state==='IDLE'&&confirmed.applied_revision===g.applied_revision&&confirmed.actual_replicas===g.actual_replicas&&confirmed.membership_epoch===g.membership_epoch){
        const rejection={code:'RESOURCE_INSUFFICIENT',revision:p.revision,desired_replicas:p.desired_replicas,at:new Date().toISOString()};
        await store.update(id,current=>({...confirmReplicaGroup(current,confirmed),group_apply_intent:null,replica_apply_rejection:rejection,history:[...current.history,{event:'replica_change_rejected',...rejection}]}));throw fault(409,'replica_capacity_insufficient');
       }
      }
      await store.update(id,current=>({...current,replica_group:{...g,operation_state:'UNKNOWN'}}));throw fault(502,'group_unknown');
     }
    }
    r=await store.update(id,current=>confirmReplicaGroup(current,g));
    if(r.input.execution_target==='cloud'&&action==='apply-revision'){const worker_bindings=await bindReplicaWorkers(clients,r,g);r=await store.update(id,current=>({...current,worker_bindings}));}
   }
   // An observation must refer to the status that preceded its window, not a newly stamped status.
   if(['submit','apply-revision','stop'].includes(action)||!r.execution.acknowledgement){
    const acknowledgement=actualGroupStatus(r,r.replica_group);
    await clients.deploymentStatus(acknowledgement);
    r=await store.update(id,current=>({...current,execution:{...current.execution,acknowledgement}}));
   }
   try{await syncReplicaTargets(await clients.groupList());}catch{r=await store.update(id,current=>({...current,prometheus_discovery:'RETRY_REQUIRED'}));}
   if(action==='measure'||action==='prometheus-feedback'){
    if(r.replica_group.operation_state!=='IDLE'||['PENDING','UNKNOWN'].includes(r.feedback_state))throw fault(409,'running_group_required');
    await requireSelectedAgents(clients,r);
    const observation=action==='measure'?await measureReplicaGroup(clients,r):await clients.prometheusObservation(r);
    if(observation.membership_epoch!==r.replica_group.membership_epoch||Date.parse(observation.started_at)<Date.parse(r.replica_group.membership_changed_at))throw fault(409,'observation_epoch_mismatch');
    if(r.input.execution_target==='cloud'&&r.application_profile.requirements.accelerator.required&&!Number.isFinite(observation.metrics.resource.accelerator_average_percent)){r=await store.update(id,current=>({...current,measurement:observation,operation_observation_state:'GPU_METRICS_UNAVAILABLE'}));return {data:r};}
    const messages=toFeedbackMessages({receipt:r,observation});
    r=await store.update(id,current=>{assertAgentSelectionBinding(r,current);return {...current,measurement:observation,feedback_state:'PENDING',feedback_pending:{messages,observation},history:[...current.history,{event:'feedback_requested',at:new Date().toISOString()}]};});
    try{const f=await clients.feedback(messages.optimizationFeedback,{operationAgent:r.agents?.operation?.name});assertAgentExecution(r.agents?.operation,f.operation_agent_execution);assertPlanBinding({receipt:r,geonFlow:f,selectionVersion:r.selection_version});r=await store.update(id,current=>({...current,feedback_state:'RECORDED',feedback:{...current.feedback_pending,geon:f},feedback_history:[...(current.feedback_history??[]),{...current.feedback_pending,geon:f}],feedback_pending:null,history:[...current.history,{event:'feedback_received',at:new Date().toISOString(),action:f.scaling_decision?.action}]}));}catch{await store.update(id,current=>({...current,feedback_state:'UNKNOWN'}));throw fault(502,'feedback_unknown');}
   }
   return {data:r};
  });
 };
}
