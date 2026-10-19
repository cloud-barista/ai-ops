import {randomUUID} from 'node:crypto';
import {digest,fault} from './index.mjs';
export function envelope(receipt,type,nowUtc=new Date().toISOString()){
  return {contract_version:'1.0',message_id:randomUUID(),message_type:type,occurred_at:nowUtc,correlation_id:`${receipt.flow_id}-s${receipt.selection_version}`,trace_id:receipt.trace_id,source:{system:'khu-research-poc',component:'integration'},target:{system:'khu-geon',component:'agent-control'}};
}
export function toExternalFlow({receipt:r,selectionVersion,nowUtc=new Date().toISOString()}){
  if(r.selection_version!==selectionVersion)throw fault(409,'stale_selection');
  const candidate=r.candidates.all.find(c=>c.candidate_id===r.selected_candidate_id);
  const row=r.resource_recommendation.recommendations.find(c=>c.resource.resource_id===r.selected_candidate_id);
  const p=r.application_profile, req=p.requirements, resource=row?.resource;
  if(!candidate?.selectable||!resource||r.resource_recommendation.profile_id!==p.profile_id)throw fault(422,'candidate_unavailable');
  const enough=(value,min)=>Number.isFinite(value)&&value>=min;
  if(!resource.healthy||!enough(resource.cpu?.available_cores,req.compute.cpu_cores_min)||!enough(resource.memory?.available_mib,req.compute.memory_mib_min)||!enough(resource.storage?.available_gib,req.compute.storage_gib_min))throw fault(422,'resource_facts_missing');
  const accelerator=req.accelerator;
  if(accelerator.required&&(resource.accelerators??[]).filter(a=>a.type===accelerator.type&&enough(a.memory_available_mib,accelerator.memory_mib_min_per_device??0)).length<(accelerator.count_min??1))throw fault(422,'resource_facts_missing');
  const m=r.input.model;
  const desired={node_count:req.deployment.replicas_min,cpu_cores_per_node:req.compute.cpu_cores_min,memory_mib_per_node:req.compute.memory_mib_min,storage_gib_per_node:req.compute.storage_gib_min,isolation:req.deployment.isolation,accelerator:accelerator.required?{type:accelerator.type,count:accelerator.count_min,memory_mib_min_per_device:accelerator.memory_mib_min_per_device}:{count:0},placement:{required_labels:{spec_id:candidate.spec_id},preferred_labels:{},anti_affinity:true}};
  const normalized=v=>Number.isFinite(v)?Math.min(1,Math.max(0,v/100)):0;
  return {input_origin:'khu-research-poc',...(r.agents?.decision?{decision_agent:r.agents.decision.name}:{}),application_context:{...envelope(r,'application.context.created',nowUtc),data:{application_profile:p,model_recommendation:{selected_model:{model_id:m.model_id,model_version:m.model_revision,source:'user_selected'},inference_configuration:{runtime_engine:m.runtime_engine,precision:m.precision,max_batch_size:m.batch_size,max_concurrency:m.max_concurrency??1,tensor_parallel_size:1,replicas:req.deployment.replicas_min}}}},resource_recommendation:{...envelope(r,'resource.recommendation.created',nowUtc),data:{resource_recommendation:{recommendation_id:r.flow_id+'-resources',profile_id:p.profile_id,snapshot_id:digest(r.resource_recommendation),status:'FOUND',selected_candidate_id:r.selected_candidate_id,candidates:[{candidate_id:r.selected_candidate_id,rank:row.rank,feasible:true,desired_infrastructure:desired,resource_hints:['spec_id='+candidate.spec_id,'resource_id='+r.selected_candidate_id],scores:{resource_fit:normalized(row.score),slo_headroom:0,cost_efficiency:0,availability:normalized(row.score_breakdown?.health),total:normalized(row.score)}}]}}}};
}
export function assertPlanBinding({receipt:r,geonFlow:f,selectionVersion}){
  if(r.selection_version!==selectionVersion)throw fault(409,'stale_selection');
  if(f.correlation_id!==`${r.flow_id}-s${selectionVersion}`||f.trace_id!==r.trace_id||f.profile_id!==r.application_profile.profile_id)throw fault(422,'plan_identity_mismatch');
  for(const id of [f.decision?.selected_candidate_id,f.deployment_plan?.selected_candidate_id])if(id&&id!==r.selected_candidate_id)throw fault(422,'plan_candidate_mismatch');
  if(f.decision?.action==='DEPLOY'&&(!f.deployment_plan||f.decision.selected_candidate_id!==r.selected_candidate_id||f.deployment_plan.selected_candidate_id!==r.selected_candidate_id))throw fault(422,'plan_candidate_mismatch');
}

export function toFeedbackMessages({receipt:r,observation:o,nowUtc=new Date().toISOString()}){
  if(!r.execution||!r.geon?.decision?.decision_id)throw fault(409,'mock_execution_required');
  const real=['local_process','cloud_vm'].includes(r.execution.mode);
  if(real&&!['local_probe','cloud_probe','prometheus'].includes(o?.source))throw fault(422,'measured_feedback_required');
  if(o?.source==='prometheus'&&o.flow_id!==r.flow_id)throw fault(422,'measured_feedback_required');
  if(!['demo_input','user_input','local_probe','cloud_probe','prometheus'].includes(o?.source)||(!real&&['local_probe','cloud_probe','prometheus'].includes(o?.source))||!Number.isFinite(Date.parse(o.started_at))||!Number.isFinite(Date.parse(o.ended_at))||Date.parse(o.started_at)>=Date.parse(o.ended_at)||Date.parse(o.ended_at)>Date.parse(nowUtc))throw fault(400,'invalid_observation');
  const metrics=o.metrics;
  for(const [group,keys] of Object.entries({resource:['cpu_average_percent','memory_peak_mib','accelerator_average_percent','accelerator_memory_peak_mib'],inference:['latency_p95_ms','throughput_rps','error_rate_percent'],cost:['estimated_cost']}))for(const k of keys){const n=metrics?.[group]?.[k];if(!Number.isFinite(n)||n<0||(k.endsWith('percent')&&n>100))throw fault(400,'invalid_metrics');}
  if(metrics.cost.currency!=='USD')throw fault(400,'invalid_currency');
  const ids={deployment_id:r.execution.execution_id,decision_id:r.geon.decision.decision_id};
  const actual=r.replica_group?{actual_replicas:r.replica_group.actual_replicas,applied_revision:r.replica_group.applied_revision,group_id:r.replica_group.group_id,execution_mode:r.execution.mode}:{};
  let deploymentStatus={...envelope(r,'deployment.status.changed',nowUtc),data:{deployment_status:{...ids,...actual,state:'RUNNING',message:real?'Actual AppDeploy process measurement':'Mock plan acceptance; no infrastructure executed',updated_at:nowUtc}}};
  const optimizationFeedback={...envelope(r,'optimization.feedback.created',nowUtc),data:{optimization_feedback:{...ids,feedback_id:randomUUID(),outcome:'SUCCEEDED',observation_window:{started_at:o.started_at,ended_at:o.ended_at},metrics,slo_violations:[],created_at:nowUtc}}};
  if(r.execution.mode==='mock'&&r.execution.request_message_id){
    if(!Number.isFinite(Date.parse(r.execution.occurred_at))||Date.parse(o.started_at)<Date.parse(r.execution.occurred_at))throw fault(400,'invalid_observation');
    deploymentStatus.causation_id=r.execution.request_message_id;
    deploymentStatus.data.deployment_status.updated_at=r.execution.occurred_at;
    const accepted=r.feedback?.messages?.deploymentStatus;
    if(accepted){
      const status=accepted.data?.deployment_status;
      if(!accepted.message_id||accepted.message_type!=='deployment.status.changed'||accepted.correlation_id!==deploymentStatus.correlation_id||accepted.trace_id!==r.trace_id||accepted.causation_id!==r.execution.request_message_id||status?.deployment_id!==ids.deployment_id||status?.decision_id!==ids.decision_id||status?.state!=='RUNNING'||status?.updated_at!==r.execution.occurred_at)throw fault(409,'mock_ack_mismatch');
      deploymentStatus=structuredClone(accepted);
    }
    optimizationFeedback.causation_id=deploymentStatus.message_id;
  }
  if(real&&r.replica_group){
    const ack=r.execution.acknowledgement,s=ack?.data?.deployment_status;
    if(!ack?.message_id||!ack.causation_id||s?.state!=='RUNNING'||s.deployment_id!==ids.deployment_id||s.decision_id!==ids.decision_id||s.applied_revision!==r.replica_group.applied_revision)throw fault(409,'deployment_ack_required');
    if(!Number.isFinite(Date.parse(s.updated_at))||Date.parse(o.started_at)<Date.parse(s.updated_at))throw fault(409,'observation_before_deployment');
    if(Date.parse(o.ended_at)<=Date.parse(r.feedback?.observation?.ended_at))throw fault(409,'observation_not_newer');
    deploymentStatus=structuredClone(ack);
    optimizationFeedback.causation_id=ack.message_id;
  }
  return {deploymentStatus,optimizationFeedback};
}
