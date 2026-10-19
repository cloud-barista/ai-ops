export function buildAnalysisInput({mode,request,appSpec,candidateId='qwen3.5-profile-generator'}){
  if(mode==='structured')return {input_type:'structured',app_spec:appSpec};
  if(mode!=='natural_language'||typeof request!=='string'||!request.trim())throw Error('invalid_analysis_input');
  if(!['qwen3.5-profile-generator','llama3.2-profile-generator'].includes(candidateId))throw Error('invalid_llm_candidate');
  const {app_id,app_version,artifact}=appSpec;
  return {input_type:'natural_language',candidate_id:candidateId,request:request.trim(),application:{app_id,app_version,...(artifact?{artifact}:{})}};
}

export function analysisControlDisabled(id,{target,mode,busy=false,live=false}){
  if(id==='new-flow')return busy;
  const directExecution=['local','cloud'].includes(target),natural=mode==='natural_language';
  return busy
    ||(directExecution&&['cohort','objective','throughput','latency'].includes(id))
    ||(!directExecution&&id==='local-model')
    ||(natural&&['scenario','cpu','memory','storage','max-replicas','service-latency','service-throughput'].includes(id))
    ||(!natural&&['natural-request','profile-llm'].includes(id));
}

export function reviewAnalysisInput(receipt){
 const input=structuredClone(receipt.input),application=receipt.requested_application??input.application;
 input.application={...application};
 if(input.analysis_input.input_type==='structured')input.analysis_input.app_spec={...input.analysis_input.app_spec,...application};
 else input.analysis_input.application={...input.analysis_input.application,...application};
 return input;
}
export async function reviewRunningCandidate(receipt,candidateId,api){
 const requested=receipt.candidates.all.find(c=>c.candidate_id===candidateId&&c.selectable);
 if(!requested)throw Error('candidate_unavailable');
 const next=await api('/flows',reviewAnalysisInput(receipt));
 const candidate=next.candidates.all.find(c=>c.name===requested.name&&c.selectable);
 if(!candidate)throw Object.assign(Error('candidate_unavailable'),{receipt:next});
 return api('/flows/'+next.flow_id+'/selection',{candidate_id:candidate.candidate_id,expected_selection_version:next.selection_version});
}

export function analysisExecutionActive(receipt){
  if(!receipt)return false;
  const target=receipt.input?.execution_target;
  if(target==='cloud'&&(['CREATE_REQUESTED','CREATE_UNKNOWN','CREATED','PREPARING','RUNNING','CLEANUP_PENDING'].includes(receipt.cloud?.state)||receipt.cloud_job_requested&&!['CLEANED','CREATE_REJECTED'].includes(receipt.cloud?.state)))return true;
  return ['local','cloud'].includes(target)&&!!receipt.appdeploy&&(receipt.execution?!['STOPPED','COMPLETED','RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED','EXTERNAL_API_FAILED'].includes(receipt.execution.status):receipt.appdeploy.status!=='PREPARED');
}

export function requirementExecutionLimits({target,scenario}){
  const gpuAvailable=target!=='local';
  const resolvedScenario=!gpuAvailable&&scenario==='gpu'?'cpu':scenario;
  const fixedReplicas=target==='cloud'&&resolvedScenario==='gpu';
  return {scenario:resolvedScenario,gpuAvailable,maxReplicas:fixedReplicas?1:2,fixedReplicas};
}
