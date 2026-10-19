import {randomUUID} from 'node:crypto';
import {fault,digest} from './index.mjs';
export function createMockAdapter({store,now=()=>new Date().toISOString(),id=randomUUID}){
  return {execute:async({flow_id,selection_version})=>{
    const result=await store.update(flow_id,r=>{
      if(r.selection_version!==selection_version)throw fault(409,'stale_selection');
      if(r.execution)return r;
      const g=r.geon;
      if(g?.decision?.action!=='DEPLOY'||g.guard?.status!=='APPROVED'||!g.agent_authorization?.authorized||g.agent_execution?.status!=='completed'||g.agent_execution.request_guard?.status!=='APPROVED'||g.agent_execution.result_guard?.status!=='APPROVED'||g.deployment_plan?.selected_candidate_id!==r.selected_candidate_id)throw fault(409,'plan_not_approved');
      const plan_sha256=digest(g.deployment_plan);
      const occurred_at=now();
      return {...r,state:'MOCK_RUNNING',execution:{execution_id:'mock-'+id(),mode:'mock',status:'RUNNING',selected_candidate_id:r.selected_candidate_id,selection_version,plan_sha256,request_message_id:g.deployment_request?.message_id,occurred_at,cloud_vm_created:false},history:[...(r.history??[]),{event:'mock_recorded',at:occurred_at,selection_version}]};
    });return result.execution;
  }};
}
