import {requireSelectedAgents,assertAgentExecution,assertAgentSelectionBinding} from './agents.mjs';
import {fault} from './index.mjs';
import {toFeedbackMessages,assertPlanBinding} from './geon-adapter.mjs';
const terminal=new Set(['STOPPED','COMPLETED','RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED','EXTERNAL_API_FAILED']);
export const realExecutionActive=r=>['CREATE_REQUESTED','CREATE_UNKNOWN','CREATED','PREPARING','RUNNING','CLEANUP_PENDING'].includes(r?.cloud?.state)||(r?.cloud_job_requested&&r?.cloud?.state!=='CLEANED')||!!r?.appdeploy&&(r.execution?!terminal.has(r.execution.status):r.appdeploy.status!=='PREPARED');
function assertLocal(r,version){
  if(version!==undefined&&r.selection_version!==version)throw fault(409,'stale_selection');
  if(r.input.execution_target!=='local'||r.selected_candidate_id!=='khu-local-pc')throw fault(422,'local_target_required');
  if(r.geon?.decision?.action!=='DEPLOY'||r.geon?.guard?.status!=='APPROVED'||r.geon?.deployment_plan?.selected_candidate_id!==r.selected_candidate_id||r.application_profile.requirements.accelerator.required)throw fault(422,'approved_local_plan_required');
  if(r.geon.deployment_plan.desired_infrastructure.node_count!==1||r.geon.deployment_plan.inference_configuration.replicas!==1)throw fault(422,'single_local_worker_required');
}
async function assertTargets(clients){const targets=(await clients.localTargets()).filter(t=>t.target_profile_id==='khu-local-pc'||t.csp==='local');if(targets.length!==1||targets[0].target_profile_id!=='khu-local-pc'||targets[0].csp!=='local'||targets[0].runtime?.runtime_type!=='local'||targets[0].runtime?.operating_mode!=='local_process')throw fault(422,'local_target_required');}
function confirmed(r,delivery){
  const d=delivery.deployment;if(!d?.deployment_id||d.target_profile_id!==r.selected_candidate_id||(d.app_version_id&&d.app_version_id!==r.appdeploy.app_version_id))throw fault(422,'deployment_identity_mismatch');
  if(r.execution&&d.deployment_id!==r.execution.execution_id)throw fault(422,'deployment_identity_mismatch');
  return {...r,state:'LOCAL_'+d.status,appdeploy:{...r.appdeploy,status:delivery.status,delivery},execution:{mode:'local_process',source:'appdeploy',execution_id:d.deployment_id,target_profile_id:d.target_profile_id,status:d.status,app_version_id:r.appdeploy.app_version_id,observed_at:new Date().toISOString()},history:[...r.history,{event:'appdeploy_status',at:new Date().toISOString(),action:d.status}]};
}
export function createLocalExecutionRoutes(){
  // Serialize the local worker slot across flows as well as the durable per-flow receipt.
  let slot=Promise.resolve();
  const exclusive=fn=>{const next=slot.catch(()=>{}).then(fn);slot=next;return next;};
  return async ({request,path,value,store,clients})=>{
    const match=path.match(/^\/api\/v1\/flows\/([\w-]+)\/appdeploy\/(prepare|submit|status|stop|measure|prometheus-feedback)$/);if(!match)return;
    const [,id,action]=match;if(request.method!==(action==='status'?'GET':'POST'))throw fault(405,'method_not_allowed');
    return exclusive(async()=>{
      let r=await store.read(id);assertLocal(r,value?.selection_version);
      if(action==='prepare'){
        if(r.appdeploy)return {data:r};await assertTargets(clients);
        const app=await clients.registerLocalApp(r.application_profile,{model:r.input.model,flow_id:r.flow_id});const delivery=await clients.appPrepare(r.geon_flow_id,{app_version_id:app.app_version_id});
        r=await store.update(id,current=>{assertLocal(current,r.selection_version);return {...current,appdeploy:{status:'PREPARED',app_version_id:app.app_version_id,delivery},history:[...current.history,{event:'appdeploy_prepared',at:new Date().toISOString()}]};});return {data:r};
      }
      if(!r.appdeploy)throw fault(409,'prepare_deployment_first');
      if(action==='submit'){
        if(r.execution)return {data:r};if(r.appdeploy.status!=='PREPARED')throw fault(409,'deployment_unknown');
        await assertTargets(clients);
        if((await clients.localDeployments()).some(d=>!terminal.has(d.status)))throw fault(409,'local_worker_busy');
        r=await store.update(id,current=>{assertLocal(current,r.selection_version);if(current.appdeploy.status!=='PREPARED')throw fault(409,'deployment_unknown');return {...current,appdeploy:{...current.appdeploy,status:'PENDING'},history:[...current.history,{event:'appdeploy_requested',at:new Date().toISOString()}]};});
        let delivery;try{delivery=await clients.appSubmit(r.geon_flow_id,{app_version_id:r.appdeploy.app_version_id,accept_projection_limits:true});}catch{await store.update(id,current=>({...current,appdeploy:{...current.appdeploy,status:'UNKNOWN'}}));return {status:502,data:{flow_id:id,error:{code:'deployment_unknown'}}};}
        if(!delivery.deployment){await store.update(id,current=>({...current,appdeploy:{...current.appdeploy,status:'UNKNOWN',delivery}}));return {status:502,data:{flow_id:id,error:{code:'deployment_unknown'}}};}
        r=await store.update(id,current=>confirmed(current,delivery));return {data:r};
      }
      if(action==='stop'){
        if(!r.execution)throw fault(409,'deployment_unknown');
        await clients.appStop(r.execution.execution_id);
      }
      const delivery=await clients.appStatus(r.geon_flow_id);
      if(!delivery.deployment)return {status:502,data:{flow_id:id,error:{code:'deployment_unknown'}}};
      r=await store.update(id,current=>confirmed(current,delivery));
      const logs=await clients.appLogs(r.execution.execution_id);
      r=await store.update(id,current=>({...current,appdeploy:{...current.appdeploy,logs}}));
      if(action==='measure'||action==='prometheus-feedback'){
        if(r.execution.status!=='RUNNING'||['UNKNOWN','PENDING'].includes(r.feedback_state))throw fault(409,'running_worker_required');
        await requireSelectedAgents(clients,r);
        const observation=action==='measure'?await clients.measureLocalWorker(delivery.deployment.runtime_id,{model:r.input.model,flow_id:r.flow_id}):await clients.prometheusObservation(r),messages=toFeedbackMessages({receipt:r,observation});
        const observation_hash=JSON.stringify(observation);
        if(action==='prometheus-feedback'&&r.feedback?.observation_hash===observation_hash)return {data:r};
        r=await store.update(id,current=>{assertAgentSelectionBinding(r,current);return {...current,[action==='measure'?'measurement':'prometheus_measurement']:observation,feedback_state:'PENDING',feedback_pending:{messages,observation,observation_hash},history:[...current.history,{event:'feedback_requested',at:new Date().toISOString(),selection_version:current.selection_version}]};});
        try{
          const result=await clients.feedback(messages.optimizationFeedback,{operationAgent:r.agents?.operation?.name});assertAgentExecution(r.agents?.operation,result.operation_agent_execution);assertPlanBinding({receipt:r,geonFlow:result,selectionVersion:r.selection_version});
          r=await store.update(id,current=>({...current,state:'FEEDBACK_RECORDED',feedback_state:'RECORDED',feedback:{...current.feedback_pending,geon:result},feedback_history:[...(current.feedback_history??[]),{...current.feedback_pending,geon:result}],feedback_pending:null,history:[...current.history,{event:'feedback_received',at:new Date().toISOString(),action:result.scaling_decision?.action}]}));
        }catch{await store.update(id,current=>({...current,feedback_state:'UNKNOWN'}));return {status:502,data:{flow_id:id,error:{code:'feedback_unknown'}}};}
      }
      return {data:r};
    });
  };
}
