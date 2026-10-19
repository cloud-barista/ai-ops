import {cloudAgentEditability} from './cloud-state.mjs';
export function setupAgents({api,getReceipt,isBusy,onReceipt,runAction=(_label,work)=>work()}){
  const $=id=>document.getElementById(id);let registry,saving=false;
  function populate(id,agents,selected){
    const select=$(id);select.replaceChildren();for(const agent of agents??[]){const option=document.createElement('option');option.value=agent.name;option.textContent=`${agent.korean_name||agent.name} · ${agent.version??'기본'}`;select.append(option);}if(selected)select.value=selected;
  }
  async function refresh({notify=false}={}){
    const work=async()=>{registry=await api('/agents');const r=getReceipt();populate('decision-agent',registry.eligible_decision_agents,committedAgent(r,'decision'));populate('operation-agent',registry.eligible_operation_agents,committedAgent(r,'operation'));$('agents-status').textContent=`등록된 Agent ${registry.agents?.length??0}개 · 선택한 Agent가 판단을 수행합니다.`;render();};
    try{await (notify?runAction('Agent 목록 조회 중…',work):work());}
    catch{$('agents-status').textContent='Agent 목록을 조회하지 못했습니다.';}
  }
  function committedAgent(r,role){
    return r?.agents?.[role]?.name??(role==='decision'?r?.geon?.agent_execution?.agent_name:r?.feedback?.geon?.operation_agent_execution?.agent_name)??registry?.defaults?.[role==='decision'?'ai_application_automation':'ai_application_operation_optimization'];
  }
  async function save(role){
    const r=getReceipt(),cloud=cloudAgentEditability(r);if(!r||isBusy()||saving||role==='decision'&&(r.execution||cloud.active)||role==='operation'&&!cloud.canChangeOperation)return;
    const status=$(role==='decision'?'decision-agent-status':'operation-agent-status');
    saving=true;render();status.textContent='선택한 Agent 적용 중…';
    try{
      await runAction('선택한 Agent 적용 중…',async()=>{
        const updated=await api(`/flows/${r.flow_id}/agents`,{selection_version:r.selection_version,decision_agent:role==='decision'?$('decision-agent').value:committedAgent(r,'decision'),operation_agent:role==='operation'?$('operation-agent').value:committedAgent(r,'operation')});
        onReceipt(updated);status.textContent=`${role==='decision'?'배포':'운영'} 판단 Agent 선택을 적용했습니다.`;
      });
    }catch(e){
      status.textContent=({agent_selection_stale:'등록 정보가 바뀌었습니다. Agent 설정에서 목록을 새로 조회해 주세요.',agent_change_requires_new_flow:'새 실험에서 배포 판단 Agent를 변경해 주세요.',agent_unavailable:'사용 가능한 Agent를 다시 선택해 주세요.'})[e.message]??'Agent 선택을 적용하지 못했습니다.';
    }finally{saving=false;render();}
  }
  function render(){
    const r=getReceipt(),cloud=cloudAgentEditability(r),disabled=isBusy()||saving||!r||['PENDING','UNKNOWN'].includes(r.feedback_state)||['PLAN_PENDING','PLAN_UNKNOWN'].includes(r.state);
    $('decision-agent').disabled=disabled||!!r?.execution||cloud.active;$('operation-agent').disabled=disabled||!cloud.canChangeOperation;$('agents-save').disabled=disabled||!!r?.execution||cloud.active;$('operation-agents-save').disabled=disabled||!cloud.canChangeOperation;
    $('operation-agents-save').title=cloud.canChangeOperation?'다음 운영 판단에 사용할 Agent 적용':'VM 준비 또는 변경이 끝난 뒤 운영 Agent를 선택하세요.';
    for(const [id,role,key] of [['decision-agent-execution','decision','agent_execution'],['operation-agent-execution','operation','operation_agent_execution']]){
      const execution=role==='decision'?r?.geon?.[key]:r?.feedback?.geon?.[key];$(id).textContent=execution?`실제 호출: ${execution.agent_name} · ${execution.status} · ${execution.latency_ms??0} ms`:'실제 호출 결과는 판단 후 표시됩니다.';
    }
  }
  $('agents-refresh').addEventListener('click',()=>refresh({notify:true}));
  $('agents-save').addEventListener('click',()=>save('decision'));
  $('operation-agents-save').addEventListener('click',()=>save('operation'));
  $('agent-register').addEventListener('submit',async event=>{
    event.preventDefault();if(isBusy())return;const button=$('agent-register-button');button.disabled=true;
    try{
      await runAction('Agent 등록 중…',async()=>{
        const name=$('agent-name').value.trim(),version=$('agent-version').value.trim();
        await api('/agents',{name,version,role:'application_decision_operation',endpoint:'http://127.0.0.1:18185',invocation_path:'/execute',capabilities:['ai_application_automation','ai_application_operation_optimization'],bounded_actions:['generate_deployment_decision','generate_scaling_decision'],enabled:true});await refresh();$('agents-status').textContent=`${name} 등록 완료 · 목록에서 선택해 주세요.`;
      });
    }catch{$('agents-status').textContent='Agent를 등록하지 못했습니다. 이름 중복과 연결 상태를 확인해 주세요.';}finally{button.disabled=false;}
  });
  return {refresh,render};
}
