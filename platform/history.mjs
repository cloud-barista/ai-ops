import {pipelineEvents} from './pipeline.mjs';

const error=(status,code)=>Object.assign(Error(code),{status,code});
const validFlowId=value=>typeof value==='string'&&/^[a-zA-Z0-9_-]{1,160}$/.test(value);
export function flowFromLocation(location,saved){
  const url=new URL(location),id=url.searchParams.has('flow')?url.searchParams.get('flow'):saved;
  return validFlowId(id)?id:null;
}
export function initialPlatformFlow(location,saved,activeRuns=[]){
 const url=new URL(location);if(url.searchParams.get('new')==='1')return null;
 const explicit=url.searchParams.get('flow');if(explicit)return flowFromLocation(location,saved);
 const active=activeRuns.filter(r=>validFlowId(r.flow_id)&&['RUNNING','CREATED','PREPARING','CREATE_UNKNOWN','CLEANUP_PENDING'].includes(r.state));
 const savedActive=active.find(r=>r.flow_id===saved);if(savedActive)return savedActive.flow_id;
 if(active.length===1)return active[0].flow_id;
 return flowFromLocation(location,saved);
}
export function flowLocation(location,id){
  const url=new URL(location);
  if(id===null)url.searchParams.delete('flow');
  else if(validFlowId(id))url.searchParams.set('flow',id);
  else throw error(400,'invalid_flow_id');
  return url.pathname+url.search+url.hash;
}
// Stable indices refer to the append-only audit history; deleting never renumbers it.
export function updateHistoryVisibility(receipt,options){
  const {operation,event_id}=options??{};
  const ids=pipelineEvents(receipt,{includeDeleted:true}).map(e=>e.id);
  const deleted=new Set(receipt.history_view?.deleted_event_ids??[]);
  if(operation==='delete'){
    if(!ids.includes(event_id))throw error(404,'history_event_not_found');
    deleted.add(event_id);
  }else if(operation==='delete_all')ids.forEach(id=>deleted.add(id));
  else if(operation==='restore')deleted.clear();
  else throw error(400,'invalid_history_operation');
  return {...receipt,history_view:{deleted_event_ids:[...deleted],updated_at:new Date().toISOString()}};
}

function redact(value){
  if(Array.isArray(value))return value.map(redact);
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,child])=>[key,/(password|authorization|secret|private_key|access_token|api_key|credentials)/i.test(key)?'[redacted]':redact(child)]));
  return value;
}
function selectionVersion(receipt,index){
  return receipt.history[index].selection_version??receipt.history.slice(0,index+1).filter(e=>e.event==='selected').length;
}
export function historyEventDetails(receipt,eventId){
  if(!pipelineEvents(receipt,{includeDeleted:true}).some(e=>e.id===eventId))throw error(404,'history_event_not_found');
  const index=Number(eventId.slice(6)),event=receipt.history[index],version=selectionVersion(receipt,index);
  const context=version===receipt.selection_version?receipt:receipt.selection_history?.find(s=>s.selection_version===version);
  let payload;
  switch(event.event){
    case 'reasoning_requested':case 'reasoning_compared':case 'reasoning_unavailable':payload=receipt.reasoning_comparisons?.[event.comparison_index];break;
    case 'research_policy_compared':payload=receipt.research_validations?.[event.research_index];break;
    case 'analysis_requested':payload=receipt.input?.analysis_input;break;
    case 'profile_received':payload={application_profile:receipt.application_profile,analysis:receipt.analysis};break;
    case 'resource_received':payload=receipt.resource_recommendation;break;
    case 'analysis_stopped':payload=receipt.error;break;
    case 'selected':payload=receipt.candidates?.all?.find(c=>c.candidate_id===event.candidate_id);break;
    case 'plan_requested':payload=context?.plan_request;break;
    case 'plan_received':payload=context?.geon;break;
    case 'mock_recorded':payload=context?.execution;break;
    case 'appdeploy_prepared':case 'appdeploy_requested':payload=context?.appdeploy?.delivery?.request;break;
    case 'appdeploy_status':payload={status:event.action,execution_id:context?.execution?.execution_id};break;
    case 'feedback_requested':case 'feedback_received':{
      const ordinal=receipt.history.slice(0,index).filter((e,i)=>e.event==='feedback_received'&&selectionVersion(receipt,i)===version).length;
      const feedback=context?.feedback_history?.[ordinal]??(event.event==='feedback_requested'?context?.feedback_pending:undefined);
      payload=event.event==='feedback_requested'?feedback?.messages:feedback?{observation:feedback.observation,scaling_decision:feedback.geon?.scaling_decision,operation_agent_execution:feedback.geon?.operation_agent_execution}:undefined;
      break;
    }
  }
  return redact({flow_id:receipt.flow_id,trace_id:receipt.trace_id,event_type:event.event,occurred_at_utc:event.at,selection_version:version,record:event,payload:payload??null});
}
