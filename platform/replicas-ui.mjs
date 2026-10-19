export function replicaView(r){const g=r?.replica_group,f=r?.feedback?.geon??r?.geon,revision=f?.manifest_revisions?.at(-1),target=revision?.desired_deployment_spec?.inference_configuration?.replicas??f?.scaling_decision?.desired_replicas??g?.actual_replicas,pendingRevision=!!g&&revision?.revision>g.applied_revision;return {visible:!!g,state:g?.operation_state,actual:g?.operation_state==='UNKNOWN'?null:g?.actual_replicas??null,desired:target,applied:g?.applied_revision??0,pendingRevision,canApply:g?.operation_state==='IDLE'&&pendingRevision&&f?.operation_agent_execution?.scaling_guard?.status==='APPROVED'&&!['PENDING','UNKNOWN'].includes(r?.feedback_state)};}
export function observationMatchesExecution(r,o){
 if(!o)return false;
 if(o.flow_id&&o.flow_id!==r.flow_id)return false;
 const g=r.replica_group;if(!g)return true;
 if(o.group_id&&o.group_id!==g.group_id)return false;
 if(o.membership_epoch!==undefined&&g.membership_epoch!==undefined)return o.membership_epoch===g.membership_epoch;
 const changed=Date.parse(g.membership_changed_at),observed=Date.parse(o.started_at??o.ended_at);
 return !Number.isFinite(changed)||!Number.isFinite(observed)||observed>=changed;
}
export function measurementNeedsRefresh(r){return !!(r?.execution?.status==='RUNNING'&&(r.measurement??r.feedback?.observation)&&!observationMatchesExecution(r,r.measurement??r.feedback.observation));}
export function setupReplicaUI({getReceipt,api,onReceipt,onError=()=>{},isBusy=()=>false,run=work=>work()}){
 const panel=document.getElementById('replica-panel'),button=document.getElementById('replica-apply');
 button.addEventListener('click',async()=>{if(isBusy())return;const r=getReceipt();if(!replicaView(r).canApply)return;try{await run(async()=>{button.disabled=true;onReceipt(await api('/flows/'+r.flow_id+'/appdeploy/apply-revision',{selection_version:r.selection_version,expected_applied_revision:r.replica_group.applied_revision}));});}catch(e){onError(e);}finally{render();}});
 function render(){const r=getReceipt(),v=replicaView(r);panel.hidden=!v.visible;if(!v.visible)return;
  const states={IDLE:'실행 중',STOPPED:'실행 종료',UNKNOWN:'실제 상태 확인 중',APPLYING:'복제본 변경 중',STOPPING:'어플리케이션 종료 중'};
  document.getElementById('replica-current').textContent=v.actual===null?'확인 중':v.actual+'개';document.getElementById('replica-desired').textContent=v.desired==null?'—':v.desired+'개';document.getElementById('replica-desired-label').textContent=v.state==='STOPPED'?'마지막 승인 목표':'승인된 목표';document.getElementById('replica-revision').textContent='명세 '+v.applied+' · '+(states[v.state]??'실행 상태 확인');
  document.getElementById('replica-help').textContent=v.state==='STOPPED'?'어플리케이션이 종료됐습니다. 마지막 계획과 측정 결과는 보관되며, 다시 실행하려면 새 실험을 시작하세요.':v.state==='UNKNOWN'?'상태 조회로 기존 실행을 확인하세요. 확인 전에는 새 복제본을 추가하지 않습니다.':'같은 자원의 어플리케이션 프로세스를 1~2개로 변경합니다. VM 대수는 그대로이며, 변경 검증을 통과한 뒤 적용합니다.';
  button.disabled=isBusy()||!v.canApply;const list=document.getElementById('replica-children');list.replaceChildren();for(const [index,c] of r.replica_group.replicas.entries()){const line=document.createElement('p');line.className='help';line.textContent='복제본 '+(index+1)+' · '+({RUNNING:'실행 중',STOPPED:'실행 종료',UNKNOWN:'확인 중'}[c.status]??c.status)+' · 포트 '+c.port+' · '+c.runtime_id;list.append(line);}}
 return {render};
}

export function setupLoadTestUI({getReceipt,api,onReceipt,isBusy=()=>false,runAction=(_label,work)=>work(),document:doc=globalThis.document}){
 const $=id=>doc.getElementById(id);let inFlight=false,polling=false,message='',messageFlow;
 function render(){const r=getReceipt(),visible=r?.input?.execution_target==='local'&&!!r.replica_group,active=r?.load_test?.state==='RUNNING',pending=replicaView(r).pendingRevision;
  $('load-test-panel').hidden=!visible;
  $('load-test-start').disabled=inFlight||isBusy()||!visible||r.execution?.status!=='RUNNING'||r.replica_group?.operation_state!=='IDLE'||active||pending;
  $('load-test-stop').disabled=inFlight||!active;
  const load=r?.load_test;$('load-test-status').textContent=message&&messageFlow===r?.flow_id?message:load?({RUNNING:'실제 추론 부하 실행 중',COMPLETED:'부하 완료',STOPPED:'부하 중지',FAILED:'부하 요청 실패 · 실행 상태 확인',INTERRUPTED:'플랫폼 재시작으로 부하 중단'})[load.state]+` · 완료 ${load.completed_requests??0}회 · 오류 ${load.errors??0}회`:'20초 동안 최대 8,192회, 동시 요청 32개로 현재 앱에 실제 추론 부하를 보냅니다.';
 }
 for(const action of ['start','stop'])$('load-test-'+action).addEventListener('click',async()=>{if(inFlight||$('load-test-'+action).disabled)return;const r=getReceipt();inFlight=true;message='';render();try{await runAction(action==='start'?'현재 앱에 실제 부하 시작 중…':'새 부하 요청 중지 중…',async()=>{const next=await api('/flows/'+r.flow_id+'/load-test'+(action==='stop'?'/stop':''),action==='stop'?{}:{selection_version:r.selection_version,request_id:crypto.randomUUID()});if(getReceipt()?.flow_id===r.flow_id)onReceipt(next);});}catch{messageFlow=r.flow_id;message=action==='start'?'부하를 시작하지 못했습니다. 현재 앱 실행 상태와 승인된 변경을 확인하세요.':'부하 중지 요청을 완료하지 못했습니다. 현재 요청 상태를 확인하세요.';}finally{inFlight=false;render();}});
 const timer=setInterval(async()=>{const r=getReceipt();if(polling||!r||r.load_test?.state!=='RUNNING')return;polling=true;try{const next=await api('/flows/'+r.flow_id+'/load-test');if(getReceipt()?.flow_id===r.flow_id)onReceipt(next);}catch{}finally{polling=false;}},1000);
 return {render,dispose:()=>clearInterval(timer)};
}
