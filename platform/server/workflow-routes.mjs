import {createAgentRoutes,requireSelectedAgents,assertAgentExecution} from './agents.mjs';
import {cloudRuntimeIssue} from '../cloud-types.mjs';
import {createReplicaExecutionRoutes} from './replica-execution.mjs';
import {createCloudRoutes} from './cloud-routes.mjs';
import {fault,digest} from './index.mjs';
import {toExternalFlow,assertPlanBinding,toFeedbackMessages} from './geon-adapter.mjs';
import {createMockAdapter} from './mock-adapter.mjs';
import {createLocalExecutionRoutes,realExecutionActive} from './local-execution.mjs';
import {reasoningRoute,reasoningPending} from './reasoning-comparison.mjs';
import {createLoadExperimentRoutes} from './load-experiment.mjs';

function pendingFeedbackId(r){return r.feedback_pending?.messages.optimizationFeedback.data.optimization_feedback.feedback_id;}
function assertPendingBinding(current,prepared){
  if(current.selection_version!==prepared.selection_version||pendingFeedbackId(current)!==pendingFeedbackId(prepared))throw fault(409,'stale_selection');
}
function feedbackAccepted(result,pending){
  const expected=pending.messages.optimizationFeedback,actual=result?.optimization_feedback;
  const e=expected.data.optimization_feedback,a=actual?.data?.optimization_feedback;
  return actual?.message_id===expected.message_id&&actual?.correlation_id===expected.correlation_id&&actual?.trace_id===expected.trace_id&&a?.feedback_id===e.feedback_id&&a?.deployment_id===e.deployment_id&&a?.decision_id===e.decision_id;
}
function recordFeedback(current,prepared,result){
  assertPendingBinding(current,prepared);
  assertAgentExecution(prepared.agents?.operation,result.operation_agent_execution);
  const feedback={...prepared.feedback_pending,geon:result};
  return {...current,state:'FEEDBACK_RECORDED',feedback_state:'RECORDED',feedback,feedback_history:[...(current.feedback_history??[]),feedback],feedback_pending:null,history:[...(current.history??[]),{event:'feedback_received',at:new Date().toISOString(),selection_version:current.selection_version,action:result.scaling_decision?.action}]};
}
export function createWorkflowRoutes({mockAdapter}={}){
  const localRoutes=createLocalExecutionRoutes(),agentRoutes=createAgentRoutes(),replicaRoutes=createReplicaExecutionRoutes(),cloudRoutes=createCloudRoutes(),loadRoutes=createLoadExperimentRoutes();
  return async({request,path,value,store,clients})=>{
    const load=await loadRoutes({request,path,value,store,clients});if(load)return load;
    const agent=await agentRoutes({request,path,value,store,clients});if(agent)return agent;
    const cloud=await cloudRoutes({request,path,value,store,clients});if(cloud)return cloud;
    const reasoning=await reasoningRoute({request,path,value,store,clients});if(reasoning)return reasoning;
    const research=path.match(/^\/api\/v1\/flows\/([\w-]+)\/research-policy$/);
    if(research){
      if(request.method!=='POST')throw fault(405,'method_not_allowed');
      if(!['planning','operation'].includes(value?.phase)||typeof value.case_id!=='string')throw fault(400,'invalid_research_case');
      const prepared=await store.read(research[1]);
      if(value.selection_version!==prepared.selection_version)throw fault(409,'stale_selection');
      const options=await clients.researchCases().catch(()=>{throw fault(502,'research_policy_unavailable');});
      if(!options.cases?.some(c=>c.case_id===value.case_id&&c.phase===value.phase))throw fault(422,'research_case_unavailable');
      const result=await clients.researchReplay({case_id:value.case_id,phase:value.phase}).catch(()=>{throw fault(502,'research_policy_unavailable');});
      if(result.case_id!==value.case_id||result.phase!==value.phase||result.execution_performed!==false)throw fault(422,'research_result_mismatch');
      const receipt=await store.update(research[1],r=>{
        if(r.selection_version!==prepared.selection_version)throw fault(409,'stale_selection');
        const comparison={phase:value.phase,selection_version:r.selection_version,result,at:new Date().toISOString()};
        return {...r,research_validations:[...(r.research_validations??[]),comparison],history:[...r.history,{event:'research_policy_compared',phase:value.phase,at:comparison.at,selection_version:r.selection_version,research_index:r.research_validations?.length??0,action:result.proposal?.action}]};
      });
      return {data:receipt};
    }
    const replicas=await replicaRoutes({request,path,value,store,clients});if(replicas)return replicas;
    const local=await localRoutes({request,path,value,store,clients});if(local)return local;
    const reconciliation=path.match(/^\/api\/v1\/flows\/([\w-]+)\/feedback-reconciliation$/);
    if(reconciliation&&request.method==='GET'){
      const id=reconciliation[1],prepared=await store.read(id);
      if(!['PENDING','UNKNOWN'].includes(prepared.feedback_state)||!prepared.feedback_pending)return {data:prepared};
      let result;
      try{result=await clients.flow(prepared.geon_flow_id);assertPlanBinding({receipt:prepared,geonFlow:result,selectionVersion:prepared.selection_version});}catch{result=null;}
      const recovered=await store.update(id,current=>{
        assertPendingBinding(current,prepared);
        return result&&feedbackAccepted(result,prepared.feedback_pending)?recordFeedback(current,prepared,result):{...current,feedback_state:'UNKNOWN'};
      });return {data:recovered};
    }
    const match=path.match(/^\/api\/v1\/flows\/([\w-]+)\/(selection|plan|mock-executions|feedback)$/);if(!match||request.method!=='POST')return;
    const [,id,action]=match;
    if(action==='selection'){
      const receipt=await store.update(id,r=>{
        if(reasoningPending(r))throw fault(409,'reasoning_result_unknown');
        if(realExecutionActive(r))throw fault(409,'stop_local_worker_first');
        if(value.expected_selection_version!==r.selection_version)throw fault(409,'stale_selection');
        const issue=r.input.execution_target==='cloud'&&cloudRuntimeIssue(r.application_profile?.requirements,r.candidates.all.find(c=>c.candidate_id===value.candidate_id));
        if(issue)throw fault(422,issue.code);
        if(!r.candidates.all.some(c=>c.candidate_id===value.candidate_id&&c.selectable))throw fault(422,'candidate_unavailable');
        const archived={selection_version:r.selection_version,selected_candidate_id:r.selected_candidate_id,geon:r.geon,geon_flow_id:r.geon_flow_id,plan_sha256:r.plan_sha256,plan_request:r.plan_request,execution:r.execution,appdeploy:r.appdeploy,cloud:r.cloud,cloud_run_id:r.cloud_run_id,replica_group:r.replica_group,measurement:r.measurement,operation_observation_state:r.operation_observation_state,feedback:r.feedback,feedback_state:r.feedback_state,feedback_pending:r.feedback_pending,feedback_history:r.feedback_history??[]};
        return {...r,state:'SELECTED',selection_version:r.selection_version+1,selected_candidate_id:value.candidate_id,geon:null,geon_flow_id:null,plan_sha256:null,plan_request:null,execution:null,appdeploy:null,cloud:null,cloud_run_id:null,cloud_job_requested:false,replica_group:null,group_intent:null,worker_bindings:null,measurement:null,operation_observation_state:null,feedback:null,feedback_state:null,feedback_pending:null,feedback_history:[],selection_history:[...(r.selection_history??[]),archived],history:[...r.history,{event:'selected',candidate_id:value.candidate_id,at:new Date().toISOString()}]};
      });return {data:receipt};
    }
    if(action==='plan'){
      await requireSelectedAgents(clients,await store.read(id));
      let fresh=false;
      const prepared=await store.update(id,r=>{
        if(value.selection_version!==r.selection_version)throw fault(409,'stale_selection');
        if(r.geon||r.state==='PLAN_PENDING'||r.state==='PLAN_UNKNOWN')return r;
        if(r.state!=='SELECTED')throw fault(409,'select_candidate_first');
        const plan_request=toExternalFlow({receipt:r,selectionVersion:r.selection_version});fresh=true;
        return {...r,state:'PLAN_PENDING',plan_request,geon_flow_id:plan_request.application_context.correlation_id,history:[...(r.history??[]),{event:'plan_requested',at:new Date().toISOString(),selection_version:r.selection_version}]};
      });
      if(prepared.geon)return {data:prepared};
      let result;
      try{result=fresh?await clients.plan(prepared.plan_request):await clients.flow(prepared.geon_flow_id);}catch{
        try{result=await clients.flow(prepared.geon_flow_id);}catch{const r=await store.update(id,r=>r.selection_version===value.selection_version?{...r,state:'PLAN_UNKNOWN',history:[...(r.history??[]),{event:'plan_unknown',at:new Date().toISOString(),selection_version:r.selection_version}]}:r);return {status:502,data:{flow_id:id,state:r.state,error:{code:'provider_unavailable'}}};}
      }
      const receipt=await store.update(id,r=>{assertPlanBinding({receipt:r,geonFlow:result,selectionVersion:value.selection_version});assertAgentExecution(r.agents?.decision,result.agent_execution);return {...r,state:result.state,geon:result,plan_sha256:result.deployment_plan?digest(result.deployment_plan):null,history:[...(r.history??[]),{event:'plan_received',at:new Date().toISOString(),selection_version:r.selection_version,action:result.decision?.action}]};});return {data:receipt};
    }
    if(action==='mock-executions'){
      if(['local','cloud'].includes((await store.read(id)).input.execution_target))throw fault(409,'real_execution_required');
      await (mockAdapter??createMockAdapter({store})).execute({flow_id:id,selection_version:value.selection_version});return {data:await store.read(id)};
    }
    if(action==='feedback'){
      await requireSelectedAgents(clients,await store.read(id));
      if(['local','cloud'].includes((await store.read(id)).input.execution_target))throw fault(422,'measured_feedback_required');
      let fresh=false;
      const r=await store.update(id,r=>{
        if(r.selection_version!==value.selection_version)throw fault(409,'stale_selection');
        if(r.feedback_state==='PENDING'||r.feedback_state==='UNKNOWN')throw fault(409,'feedback_unknown');
        const observation_hash=digest(value.observation);
        if(r.feedback?.observation_hash===observation_hash)return r;
        const messages=toFeedbackMessages({receipt:r,observation:value.observation});fresh=true;
        return {...r,feedback_state:'PENDING',feedback_pending:{messages,observation:value.observation,observation_hash},history:[...(r.history??[]),{event:'feedback_requested',at:new Date().toISOString(),selection_version:r.selection_version}]};
      });
      if(!fresh)return {data:r};
      try{
        await clients.deploymentStatus(r.feedback_pending.messages.deploymentStatus);
        const result=await clients.feedback(r.feedback_pending.messages.optimizationFeedback,{operationAgent:r.agents?.operation?.name});
        assertPlanBinding({receipt:r,geonFlow:result,selectionVersion:r.selection_version});
        assertAgentExecution(r.agents?.operation,result.operation_agent_execution);
        const updated=await store.update(id,current=>recordFeedback(current,r,result));return {data:updated};
      }catch(error){
        if(error.code==='stale_selection')throw error;
        await store.update(id,current=>{assertPendingBinding(current,r);return {...current,feedback_state:'UNKNOWN',history:[...(current.history??[]),{event:'feedback_unknown',at:new Date().toISOString(),selection_version:current.selection_version}]};});return {status:502,data:{flow_id:id,error:{code:'provider_unavailable'},feedback_state:'UNKNOWN'}};
      }
    }
    throw fault(409,'operation_not_available');
  };
}
