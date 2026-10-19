import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {profileLlmCandidates,profileLlmScenarios,profileLlmEvaluationVersion,scoreProfileResult,summarizeProfileComparison} from '../profile-llm.mjs';
const fault=(status,code)=>Object.assign(Error(code),{status,code});
export function createLlmComparison({clients,store}){
  let activeId=null,startQueue=Promise.resolve();
  function normalize(record,activeAtRead=null){
    if(record.evaluation_version!==profileLlmEvaluationVersion){
      const results=record.results.map(result=>scoreProfileResult({candidate:record.candidates.find(c=>c.candidate_id===result.candidate_id),scenario:record.scenarios.find(s=>s.id===result.scenario_id),response:result.response,errorCode:result.error_code==='provider_unavailable'?'provider_unavailable':undefined,durationMs:result.duration_ms}));
      record={...record,evaluation_version:profileLlmEvaluationVersion,evaluation_updated:true,results,summary:record.state==='COMPLETED'?summarizeProfileComparison(results,record.candidates,record.scenarios.length):null};
    }
    return record.state==='RUNNING'&&record.run_id!==activeId&&record.run_id!==activeAtRead?{...record,state:'INTERRUPTED',error:{code:'llm_comparison_interrupted'}}:record;
  }
  // A read can return the last RUNNING snapshot after execute clears activeId.
  // Preserve activity at read start; the next poll observes the terminal receipt.
  const get=async id=>{const activeAtRead=activeId;return normalize(await store.read(id),activeAtRead);};
  const list=async()=>{const activeAtRead=activeId;return {runs:(await store.list()).map(r=>normalize(r,activeAtRead)).sort((a,b)=>b.created_at.localeCompare(a.created_at))};};
  async function options(){
    const payload=await clients.llmModels().catch(()=>{throw fault(502,'llm_models_unavailable');});
    const names=new Set((payload.models??[]).map(m=>m.name));
    return {candidates:profileLlmCandidates.map(c=>({...c,available:names.has(c.actual_model)})),active_run_id:activeId,suite:{id:'application-profile-v1',scenario_count:profileLlmScenarios.length,scenarios:profileLlmScenarios.map(({id,label,request,expected})=>({id,label,request,expected}))}};
  }
  async function execute(initial){
    let record=initial;
    try{
      for(const candidate of record.candidates){
        for(const scenario of profileLlmScenarios){
          const started=performance.now();let response,errorCode;
          try{response=await clients.profile({input_type:'natural_language',candidate_id:candidate.candidate_id,request:scenario.request,application:{app_id:'llm-eval-'+scenario.id,app_version:'1.0.0'}});}catch{errorCode='provider_unavailable';}
          const result=scoreProfileResult({candidate,scenario,response,errorCode,durationMs:Math.round(performance.now()-started)});
          record={...record,completed_count:record.completed_count+1,results:[...record.results,result]};await store.write(record);
        }
      }
      record={...record,state:'COMPLETED',benchmark_status:record.results.some(r=>r.contract_valid)?'executed':'no_valid_llm_response',completed_at:new Date().toISOString(),summary:summarizeProfileComparison(record.results,record.candidates,profileLlmScenarios.length)};await store.write(record);
    }catch{
      await store.write({...record,state:'FAILED',completed_at:new Date().toISOString(),error:{code:'llm_comparison_failed'}}).catch(()=>{});
    }finally{if(activeId===record.run_id)activeId=null;}
  }
  async function startOnce(value){
    if(!value||typeof value.request_id!=='string'||! /^[\w-]{8,80}$/.test(value.request_id)||Object.keys(value).some(k=>k!=='request_id'))throw fault(400,'invalid_llm_comparison');
    const run_id='llm-'+value.request_id;
    try{return await get(run_id);}catch(error){if(error.code!=='ENOENT')throw error;}
    if(activeId)throw fault(409,'llm_comparison_busy');
    const available=await options();if(available.candidates.some(c=>!c.available))throw fault(409,'llm_models_unavailable');
    const created_at=new Date().toISOString();
    const record={flow_id:run_id,run_id,request_id:value.request_id,state:'RUNNING',created_at,benchmark_status:'running',evaluation_version:profileLlmEvaluationVersion,suite_id:'application-profile-v1',suite_sha256:createHash('sha256').update(JSON.stringify(profileLlmScenarios)).digest('hex'),candidates:profileLlmCandidates,scenarios:profileLlmScenarios,total_count:6,completed_count:0,results:[],summary:null,protocol:{temperature:0,order:'candidate_then_scenario',repeats_per_case:1,latency:'end_to_end_including_loading_and_retries',generator:'LLM_Op ApplicationProfile API'}};
    activeId=run_id;
    try{await store.write(record);}catch(error){activeId=null;throw error;}
    void execute(record);return record;
  }
  function start(value){const pending=startQueue.then(()=>startOnce(value));startQueue=pending.catch(()=>{});return pending;}
  async function route({request,path,value}){
    if(request.method==='GET'&&path==='/api/v1/llm-candidates')return {data:await options()};
    if(path==='/api/v1/llm-comparisons'){
      if(request.method==='GET')return {data:await list()};
      if(request.method==='POST')return {status:202,data:await start(value)};
    }
    const match=path.match(/^\/api\/v1\/llm-comparisons\/([\w-]+)$/);
    if(match&&request.method==='GET')return {data:await get(match[1])};
  }
  return {options,get,list,start,route,isRunning:()=>!!activeId};
}
