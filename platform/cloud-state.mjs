// A VM lifecycle receipt alone is not an application deployment receipt.
export function cloudAgentEditability(r){
 const active=!!(r?.cloud_job_requested&&r.cloud?.state!=='CLEANED'||['CREATE_REQUESTED','CREATE_UNKNOWN','CREATED','PREPARING','RUNNING','CLEANUP_PENDING'].includes(r?.cloud?.state));
 const operationReady=r?.cloud?.state==='RUNNING'&&r.execution?.status==='RUNNING'&&!!r.agents?.decision&&r.replica_group?.operation_state==='IDLE'&&!!r.appdeploy?.group_id&&r.cloud.app?.group_id===r.appdeploy.group_id&&r.replica_group.group_id===r.appdeploy.group_id;
 return {active,canChangeOperation:!active||operationReady};
}

export function cloudApplicationDeployed(receipt,run=receipt?.cloud){
 return !!(run?.app?.group_id||receipt?.replica_group?.group_id||receipt?.execution?.group_id||receipt?.execution?.execution_id);
}
export function cloudPreparationFailed(receipt){
 return receipt?.input?.execution_target==='cloud'&&!!receipt.cloud&&!cloudApplicationDeployed(receipt)&&
  (!!receipt.cloud.failure||(['CLEANED','CREATE_REJECTED'].includes(receipt.cloud.state)&&receipt.cloud.cleanup?.confirmed===true));
}
export function cloudPreparationLabel(receipt){
 if(receipt?.cloud?.state==='CREATE_REJECTED'&&receipt.cloud.cleanup?.not_created===true)return 'VM 생성 거절 · 생성 자원 없음 확인';
 if(receipt?.cloud?.failure?.code==='cloud_cleanup_in_progress')return receipt.cloud.cleanup?.confirmed===true?'응용 준비 중단 · VM 정리 완료':'응용 준비 중단 · VM 정리 진행 중';
 return receipt?.cloud?.state==='CLEANED'&&receipt.cloud.cleanup?.confirmed===true?'배포 준비 실패 · VM 정리 확인 완료':'배포 준비 실패';
}
