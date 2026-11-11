// A VM lifecycle receipt alone is not an application deployment receipt.
export function cloudAgentEditability(r){
 const active=!!(r?.cloud_job_requested&&r.cloud?.state!=='CLEANED'||['CREATE_REQUESTED','CREATE_UNKNOWN','CREATED','PREPARING','RUNNING','CLEANUP_PENDING'].includes(r?.cloud?.state));
 const operationReady=r?.cloud?.state==='RUNNING'&&r.execution?.status==='RUNNING'&&!!r.agents?.decision&&r.replica_group?.operation_state==='IDLE'&&!!r.appdeploy?.group_id&&r.cloud.app?.group_id===r.appdeploy.group_id&&r.replica_group.group_id===r.appdeploy.group_id;
 return {active,canChangeOperation:!active||operationReady};
}

export function cloudApplicationDeployed(receipt,run=receipt?.cloud){
 return !!(run?.app?.group_id||receipt?.replica_group?.group_id||receipt?.execution?.group_id||receipt?.execution?.execution_id);
}
