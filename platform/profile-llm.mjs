export const profileLlmCandidates=[
  {candidate_id:'qwen3.5-profile-generator',actual_model:'qwen3.5:4b',label:'Qwen 3.5 · 4B'},
  {candidate_id:'llama3.2-profile-generator',actual_model:'llama3.2:3b',label:'Llama 3.2 · 3B'}
];
export const profileLlmEvaluationVersion='application-profile-v2';
const fieldNames={
  'workload.task_type':'어플리케이션 유형','workload.request_pattern':'요청 방식',
  'requirements.compute.cpu_cores_min':'CPU 코어','requirements.compute.memory_mib_min':'메모리 MiB','requirements.compute.storage_gib_min':'저장소 GiB',
  'requirements.accelerator.required':'GPU 필요 여부','requirements.accelerator.type':'가속기 종류','requirements.accelerator.count_min':'GPU 개수','requirements.accelerator.memory_mib_min_per_device':'GPU 메모리 MiB',
  'requirements.deployment.replicas_min':'최소 복제본','requirements.deployment.replicas_max':'최대 복제본',
  'requirements.slo.latency_p95_ms_max':'서비스 p95 상한 ms','requirements.slo.throughput_rps_min':'최소 요청/초'
};
function expected(pattern,cpu,memory,storage,gpu,count,vram,max,p95,rps){
  return Object.fromEntries(Object.keys(fieldNames).map((path,index)=>[path,['CLASSIFICATION',pattern,cpu,memory,storage,gpu,gpu?'GPU':'',count,vram,1,max,p95,rps][index]]));
}
export const profileLlmScenarios=[
  {id:'cpu-online',label:'CPU 온라인 분류',request:'온라인 텍스트 분류 서비스입니다. CPU 2코어, 메모리 2GiB, 저장소 20GiB가 필요합니다. GPU는 필요 없습니다. 복제본은 최소 1개, 최대 3개입니다. 서비스 p95 지연 상한은 50ms입니다. 최소 처리량과 비용 한도는 지정하지 않습니다.',expected:expected('ONLINE',2,2048,20,false,0,0,3,50,0)},
  {id:'gpu-online',label:'GPU 온라인 분류',request:'온라인 이미지 분류 서비스입니다. CPU 8코어, 메모리 16GiB, 저장소 40GiB가 필요합니다. GPU 1개와 GPU당 메모리 16GiB가 필요합니다. 복제본은 최소 1개, 최대 2개입니다. 서비스 p95 지연 상한은 150ms, 최소 처리량은 초당 100요청입니다. 비용 한도는 지정하지 않습니다.',expected:expected('ONLINE',8,16384,40,true,1,16384,2,150,100)},
  {id:'cpu-batch',label:'CPU 배치·단위 변환',request:'배치 텍스트 분류 작업입니다. CPU 4코어, 메모리 8192MiB, 저장소 30GiB가 필요합니다. GPU는 필요 없습니다. 복제본은 최소 1개, 최대 1개입니다. 서비스 지연 상한, 최소 처리량과 비용 한도는 지정하지 않습니다.',expected:expected('BATCH',4,8192,30,false,0,0,1,0,0)}
];
const get=(value,path)=>path.split('.').reduce((v,key)=>v?.[key],value);
const optionalDefaults={'requirements.accelerator.type':'','requirements.slo.latency_p95_ms_max':0,'requirements.slo.throughput_rps_min':0};
function profileValue(profile,path){
  const value=get(profile,path);
  if(value!==undefined)return value;
  const parent=get(profile,path.slice(0,path.lastIndexOf('.')));
  return parent&&typeof parent==='object'&&Object.hasOwn(optionalDefaults,path)?optionalDefaults[path]:undefined;
}
export function scoreProfileResult({candidate,scenario,response,durationMs,errorCode}){
  const analysis=response?.analysis,profile=response?.application_profile;
  const contractValid=analysis?.mode==='llm'&&analysis.candidate_id===candidate.candidate_id&&analysis.actual_model===candidate.actual_model&&profile?.app_id==='llm-eval-'+scenario.id&&profile.app_version==='1.0.0';
  const fields=Object.entries(scenario.expected).map(([path,wanted])=>({path,label:fieldNames[path],expected:wanted,actual:profileValue(profile,path)??null,matched:!!contractValid&&Object.is(profileValue(profile,path),wanted)}));
  const matched=fields.filter(f=>f.matched).length;
  return {candidate_id:candidate.candidate_id,actual_model:candidate.actual_model,scenario_id:scenario.id,scenario_label:scenario.label,contract_valid:!!contractValid,matched_fields:matched,total_fields:fields.length,exact_match:!!contractValid&&matched===fields.length,duration_ms:durationMs,fields,
    ...(errorCode||!contractValid?{error_code:errorCode??'llm_contract_failed'}:{}),...(response?{response}:{})};
}
export function summarizeProfileComparison(results,candidates,scenarioCount){
  const rows=candidates.map(candidate=>{
    const values=results.filter(r=>r.candidate_id===candidate.candidate_id),valid=values.filter(r=>r.contract_valid),matched=values.reduce((n,r)=>n+r.matched_fields,0),total=values.reduce((n,r)=>n+r.total_fields,0);
    return {...candidate,completed_count:values.length,scenario_count:scenarioCount,contract_count:valid.length,exact_count:values.filter(r=>r.exact_match).length,matched_fields:matched,total_fields:total,field_accuracy_percent:total?Math.round(matched/total*10000)/100:0,contract_valid_percent:scenarioCount?Math.round(valid.length/scenarioCount*10000)/100:0,average_duration_ms:values.length?Math.round(values.reduce((n,r)=>n+r.duration_ms,0)/values.length):null};
  });
  rows.sort((a,b)=>b.exact_count-a.exact_count||b.field_accuracy_percent-a.field_accuracy_percent||b.contract_count-a.contract_count||(a.average_duration_ms??Infinity)-(b.average_duration_ms??Infinity));
  const eligible=rows.filter(r=>r.contract_count>0&&r.completed_count===scenarioCount);
  return {policy:'extraction_accuracy_first',recommended_candidate_id:eligible[0]?.candidate_id??null,candidates:rows};
}
