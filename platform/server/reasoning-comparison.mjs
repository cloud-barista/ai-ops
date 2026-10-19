import {randomUUID} from 'node:crypto';
import {fault} from './index.mjs';
import {assertPlanBinding} from './geon-adapter.mjs';

const candidateId='qwen3.5-profile-generator';
const activeEntry=r=>(r.reasoning_comparisons??[]).findLast(e=>e.selection_version===r.selection_version);
const uncertain=e=>e&&['PENDING','UNKNOWN'].includes(e.status);
export function reasoningPending(r){return activeEntry(r)?.status==='PENDING';}

function validResult(r,entry,result){
  if(result?.correlation_id!==entry.geon_flow_id||result.candidate_id!==candidateId||!Number.isFinite(Date.parse(result.created_at))||Date.parse(result.created_at)<Date.parse(entry.started_at))return false;
  const rule=result.rule_based;
  if(rule?.execution_status!=='executed'||rule.action!==r.geon.decision.action||(rule.selected_candidate_id??'')!==(r.geon.decision.selected_candidate_id??''))return false;
  return ['simple_inference','validated_inference'].every(k=>{
    const row=result[k];
    return row&&['executed','skipped','provider_unavailable','rejected'].includes(row.execution_status)&&
      (row.execution_status!=='executed'||(['DEPLOY','REJECT','RETRY'].includes(row.action)&&Number.isFinite(row.latency_ms)&&row.latency_ms>=0));
  });
}
async function finish(store,id,entry,result){
  return store.update(id,r=>{
    if(r.selection_version!==entry.selection_version)throw fault(409,'stale_selection');
    const index=(r.reasoning_comparisons??[]).findIndex(e=>e.comparison_id===entry.comparison_id);
    if(index<0||!uncertain(r.reasoning_comparisons[index]))return r;
    if(!validResult(r,entry,result))throw fault(422,'reasoning_result_mismatch');
    const completed=result.simple_inference.execution_status==='executed'&&result.validated_inference.execution_status==='executed';
    const finished_at=new Date().toISOString(),saved={...entry,result,status:completed?'COMPLETED':'UNAVAILABLE',finished_at,elapsed_ms:Math.max(0,Date.parse(finished_at)-Date.parse(entry.started_at))};
    const entries=r.reasoning_comparisons.slice();entries[index]=saved;
    return {...r,reasoning_comparisons:entries,history:[...r.history,{event:completed?'reasoning_compared':'reasoning_unavailable',at:finished_at,selection_version:entry.selection_version,comparison_index:index}]};
  });
}
async function recover(store,clients,id,entry){
  try{
    const r=await store.read(id),flow=await clients.flow(entry.geon_flow_id);
    assertPlanBinding({receipt:r,geonFlow:flow,selectionVersion:entry.selection_version});
    if(validResult(r,entry,flow.reasoning_comparison))return await finish(store,id,entry,flow.reasoning_comparison);
  }catch{/* A GET can confirm an existing result; it never retries a model call. */}
  return store.read(id);
}
export async function reasoningRoute({request,path,value,store,clients}){
  const match=path.match(/^\/api\/v1\/flows\/([\w-]+)\/reasoning-comparison$/);if(!match)return;
  const id=match[1];
  if(request.method==='GET'){
    const r=await store.read(id),entry=activeEntry(r);
    return {data:uncertain(entry)?await recover(store,clients,id,entry):r};
  }
  if(request.method!=='POST')throw fault(405,'method_not_allowed');
  if(!value||Object.keys(value).some(k=>k!=='selection_version')||!Number.isInteger(value.selection_version))throw fault(400,'invalid_reasoning_request');
  let entry;
  await store.update(id,r=>{
    if(r.selection_version!==value.selection_version)throw fault(409,'stale_selection');
    if(!r.geon?.decision||!r.geon_flow_id)throw fault(409,'plan_required');
    assertPlanBinding({receipt:r,geonFlow:r.geon,selectionVersion:value.selection_version});
    if(uncertain(activeEntry(r)))throw fault(409,'reasoning_result_unknown');
    entry={comparison_id:randomUUID(),selection_version:r.selection_version,geon_flow_id:r.geon_flow_id,selected_candidate_id:r.selected_candidate_id,profile_sha256:r.hashes.profile,resource_sha256:r.hashes.recommendation,candidate_id:candidateId,status:'PENDING',started_at:new Date().toISOString()};
    return {...r,reasoning_comparisons:[...(r.reasoning_comparisons??[]),entry],history:[...r.history,{event:'reasoning_requested',at:entry.started_at,selection_version:r.selection_version,comparison_index:r.reasoning_comparisons?.length??0}]};
  });
  try{
    const result=await clients.reasoning(entry.geon_flow_id,{candidate_id:candidateId});
    return {data:await finish(store,id,entry,result)};
  }catch{
    const r=await recover(store,clients,id,entry);
    if(!uncertain(activeEntry(r)))return {data:r};
    await store.update(id,current=>({...current,reasoning_comparisons:current.reasoning_comparisons.map(e=>e.comparison_id===entry.comparison_id&&uncertain(e)?{...e,status:'UNKNOWN'}:e)}));
    return {status:502,data:{flow_id:id,error:{code:'reasoning_result_unknown'}}};
  }
}
