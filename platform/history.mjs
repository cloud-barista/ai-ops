const error=(status,code)=>Object.assign(Error(code),{status,code});
const visibleEvents=new Set([
  'reasoning_requested','reasoning_compared','reasoning_unavailable','research_policy_compared',
  'analysis_requested','profile_received','resource_received','analysis_stopped','selected',
  'plan_requested','plan_received','plan_unknown','mock_recorded',
  'feedback_requested','feedback_received','feedback_unknown',
  'appdeploy_prepared','appdeploy_requested','appdeploy_status','resources_refreshed',
  'group_requested','group_running','replica_apply_requested','revision_applied','group_stopped',
]);

// Stable indices refer to the append-only audit history; deleting never renumbers it.
export function updateHistoryVisibility(receipt,options){
  const {operation,event_id}=options??{};
  const ids=[];
  for(const [index,entry] of (receipt.history??[]).entries()){
    if(Number.isFinite(Date.parse(entry?.at))&&visibleEvents.has(entry.event))ids.push(`event-${index}`);
  }
  const deleted=new Set(receipt.history_view?.deleted_event_ids??[]);
  if(operation==='delete'){
    if(!ids.includes(event_id))throw error(404,'history_event_not_found');
    deleted.add(event_id);
  }else if(operation==='delete_all')ids.forEach(id=>deleted.add(id));
  else if(operation==='restore')deleted.clear();
  else throw error(400,'invalid_history_operation');
  return {...receipt,history_view:{deleted_event_ids:[...deleted],updated_at:new Date().toISOString()}};
}
