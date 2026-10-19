import {digest,fault} from './index.mjs';
import {cloudAgentEditability} from '../cloud-state.mjs';

const roles={decision:['ai_application_automation','generate_deployment_decision'],operation:['ai_application_operation_optimization','generate_scaling_decision']};
const fields=['name','version','role','source','endpoint','invocation_path','auth_token_env','registered_at','enabled'];
function identity(agent){return {...Object.fromEntries(fields.map(k=>[k,agent[k]??null])),capabilities:[...(agent.capabilities??[])].sort(),bounded_actions:[...(agent.bounded_actions??[])].sort()};}
function find(registry,name,role){
  const a=registry?.agents?.find(a=>a.name===name),[capability,action]=roles[role];
  if(!a||a.enabled!==true||!a.capabilities?.includes(capability)||!a.bounded_actions?.includes(action))throw fault(422,'agent_unavailable');
  return a;
}
function selection(a){return {name:a.name,version:a.version??'configuration',registry_sha256:digest(identity(a))};}
export function validateAgentSelection(registry,saved){
  if(!saved)return;
  for(const role of Object.keys(roles)){
    const expected=saved[role];if(!expected)throw fault(422,'agent_unavailable');
    const current=selection(find(registry,expected.name,role));
    if(current.version!==expected.version||current.registry_sha256!==expected.registry_sha256)throw fault(409,'agent_selection_stale');
  }
}
export async function requireSelectedAgents(clients,r){if(r.agents)validateAgentSelection(await clients.agents(),r.agents);}
export function assertAgentSelectionBinding(before,current){
 if(digest(before.agents??null)!==digest(current.agents??null))throw fault(409,'agent_selection_stale');
}
export function assertAgentExecution(selected,result){
  if(!selected)return;
  if(result?.agent_name!==selected.name||result.status!=='completed')throw fault(422,'agent_execution_mismatch');
}
export function createAgentRoutes({allowedEndpoints=['http://127.0.0.1:18185']}={}){
  return async({request,path,value,store,clients})=>{
    if(path==='/api/v1/agents'){
      if(request.method==='GET'){
        const r=await clients.agents();return {data:{version:r.version,defaults:r.defaults,persistence:r.persistence,agents:r.agents,eligible_decision_agents:r.eligible_decision_agents,eligible_operation_agents:r.eligible_operation_agents,allowed_endpoints:allowedEndpoints}};
      }
      if(request.method!=='POST')throw fault(405,'method_not_allowed');
      let u;try{u=new URL(value?.endpoint);}catch{throw fault(422,'agent_endpoint_not_allowed');}
      if(u.username||u.password||u.search||u.hash||u.pathname!=='/'||!allowedEndpoints.includes(u.origin))throw fault(422,'agent_endpoint_not_allowed');
      if(value.invocation_path!=='/execute')throw fault(422,'agent_endpoint_not_allowed');
      const keys=['name','korean_name','version','role','capabilities','bounded_actions','responsibilities','reward_signals','enabled','endpoint','invocation_path','auth_token_env'];
      const body=Object.fromEntries(keys.filter(k=>value[k]!==undefined).map(k=>[k,value[k]]));
      return {status:201,data:await clients.registerAgent(body)};
    }
    const match=path.match(/^\/api\/v1\/flows\/([\w-]+)\/agents$/);if(!match)return;
    if(request.method!=='POST')throw fault(405,'method_not_allowed');
    const registry=await clients.agents(),chosen={decision:selection(find(registry,value?.decision_agent,'decision')),operation:selection(find(registry,value?.operation_agent,'operation'))};
    const r=await store.update(match[1],current=>{
      if(current.selection_version!==value.selection_version)throw fault(409,'stale_selection');
      if(['PENDING','UNKNOWN'].includes(current.feedback_state))throw fault(409,'feedback_unknown');
      if(['PLAN_PENDING','PLAN_UNKNOWN'].includes(current.state))throw fault(409,'deployment_unknown');
      const previous=current.agents?.decision?.name??current.geon?.agent_execution?.agent_name??(current.execution?undefined:registry.defaults?.ai_application_automation);
      const decisionChanged=previous!==chosen.decision.name||!!current.agents&&digest(current.agents.decision)!==digest(chosen.decision);
      const cloud=cloudAgentEditability(current);
      if(cloud.active&&(decisionChanged||!cloud.canChangeOperation))throw fault(409,'agent_change_requires_new_flow');
      if(current.execution&&decisionChanged)throw fault(409,'agent_change_requires_new_flow');
      const invalidate=!!current.geon&&decisionChanged;
      const archived=invalidate?[...(current.agent_plan_history??[]),{selection_version:current.selection_version,geon:current.geon,agents:current.agents}]:current.agent_plan_history;
      return {...current,agents:chosen,...invalidate?{state:'SELECTED',selection_version:current.selection_version+1,geon:null,geon_flow_id:null,plan_sha256:null,plan_request:null,appdeploy:null,agent_plan_history:archived}:{},history:[...(current.history??[]),{event:'agents_selected',agents:chosen,at:new Date().toISOString()}]};
    });return {data:r};
  };
}
