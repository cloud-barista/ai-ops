import {fault,digest} from './index.mjs';
import {buildCandidates} from './candidates.mjs';

function assertRefreshable(r,version){
 if(r.selection_version!==version)throw fault(409,'stale_selection');
 if(r.input?.execution_target!=='local'||!r.application_profile||!['ANALYZED','SELECTED'].includes(r.state)||r.geon||r.execution||r.appdeploy||r.cloud_job_requested)throw fault(409,'resource_refresh_not_allowed');
}
export async function refreshResources({store,clients,catalog,snapshots,id,value}){
 if(!value||!Number.isInteger(value.selection_version)||Object.keys(value).some(k=>k!=='selection_version'))throw fault(400,'invalid_resource_refresh');
 const prepared=await store.read(id);assertRefreshable(prepared,value.selection_version);
 let rec;
 try{rec=await clients.recommend({schema_version:'1.0',request_id:id,application_profile:prepared.application_profile,preferences:{max_results:5},selectors:{providers:['local']}});}catch{throw fault(502,'resource_refresh_unavailable');}
 if(rec.request_id!==id||rec.profile_id!==prepared.application_profile.profile_id)throw fault(422,'identity_mismatch');
 const candidates=buildCandidates({applicationProfile:prepared.application_profile,recommendation:rec,workload:prepared.input.workload,catalog,cpuPolicy:snapshots.cpuPolicy,evidence:snapshots.evidence,executionTarget:'local'});
 return store.update(id,current=>{
  assertRefreshable(current,value.selection_version);
  if(digest(current.application_profile)!==digest(prepared.application_profile))throw fault(409,'stale_selection');
  return {...current,state:'ANALYZED',selected_candidate_id:null,selection_version:current.selection_version+1,resource_recommendation:rec,candidates,hashes:{...current.hashes,recommendation:digest(rec)},history:[...(current.history??[]),{event:'resources_refreshed',at:new Date().toISOString(),selection_version:current.selection_version+1,observed_at:rec.observed_at,selectable:candidates.primary.length}]};
 });
}
