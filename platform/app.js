import {setupCloudUI,prepareAwsRecording} from './cloud-ui.mjs';
import {setupReplicaUI,replicaView,setupLoadTestUI} from './replicas-ui.mjs';
import {setupAgents} from './agents-ui.mjs';
import {buildAnalysisInput,analysisControlDisabled,analysisExecutionActive,requirementExecutionLimits,reviewAnalysisInput,reviewRunningCandidate} from './analysis-input.mjs';
import {setupLlmComparison} from './llm-comparison-ui.mjs';
import {setupPrometheusMonitoring} from './prometheus-ui.mjs';
import {setupCloudCampaignUI} from './cloud-campaign-ui.mjs';
import {setupValidationUI} from './validation-ui.mjs';
import {candidateCardModel,infrastructureCandidates,flowViewModel,judgementLabel,reasonLabel,modelName,captureSelectionFocus,restoreSelectionFocus,revisionComparisonModel,workspaceGuidanceModel,operationErrorModel} from './view-model.mjs';
import {pipelineViewModel,pipelineEvents} from './pipeline.mjs';
import {setupNavigation,setupActionFeedback} from './navigation.mjs';
import {deploymentViewModel,deploySelectedApp} from './deployment.mjs';
import {historyEventDetails,initialPlatformFlow,flowLocation} from './history.mjs';
import {monitoringPanelModel,monitoringRecords,researchPanelModel,policyComparisonSummary,operationHistoryModel,experimentResultModel} from './panels.mjs';
import {reasoningPanelModel} from './reasoning.mjs';
import {infrastructureContextModel} from './infrastructure-context.mjs';
import {resourceAvailabilityModel} from './view-model.mjs';

const $=id=>document.getElementById(id);let receipt=null,busy=false,formDirty=false,policy,catalog,activeOperation=null,operationStartedAt=null,elapsedTicker=null,executionOptions,historyMessage='',savedMonitoring=[],monitorFlowId='',researchCases=[],lastActionError=null;
const node=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
const metric=(label,value,sub)=>{const e=node('div');e.append(node('span',label,'metric-label'),node('span',value,'metric-value'));if(sub)e.append(node('span',sub,'metric-sub'));return e;};
const replace=(id,...children)=>$(id).replaceChildren(...children);
const requestFeedback=setupActionFeedback(document),runAction=(label,work)=>requestFeedback.run(label,work);
const llmComparison=setupLlmComparison({api,isBusy:()=>busy,canChangeSelection:()=>!busy,onSelectionChange:()=>{if(receipt)formDirty=true;render();},onActivityChange:state=>{if(!state?.silent)requestFeedback.reflect('llm-comparison',state);render(false);}});
const prometheusMonitor=setupPrometheusMonitoring({api,runAction,getReceipt:()=>monitoringReceipt(),isBusy:()=>busy||formDirty||!!monitorFlowId,onFeedback:()=>action('수집한 관측값으로 운영 판단 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/appdeploy/prometheus-feedback`,{selection_version:receipt.selection_version});render();},'feedback')});
const agentsUi=setupAgents({api,runAction,getReceipt:()=>receipt,isBusy:()=>busy||formDirty,onReceipt:r=>{receipt=r;render();}});
function rememberFlow(id){
  if(id)localStorage.setItem('khu-flow-id',id);else localStorage.removeItem('khu-flow-id');
  const location=new URL(flowLocation(window.location.href,id??null),window.location.href);
  if(id)location.searchParams.delete('new');else location.searchParams.set('new','1');
  window.history.replaceState(null,'',location.pathname+location.search+location.hash);
}
const cloudUI=setupCloudUI({getReceipt:()=>receipt,api,runAction,onReceipt:r=>{const started=r.execution?.status==='RUNNING'&&receipt?.execution?.status!=='RUNNING';receipt=r;if(started)monitorFlowId='';render();if(started&&document.body.dataset.workspaceView==='deployment')navigation.navigate('#operation');},isBusy:()=>busy||formDirty});
setupCloudCampaignUI({api,runAction});
setupValidationUI({api,runAction});
const replicaUI=setupReplicaUI({getReceipt:()=>receipt,api,onReceipt:r=>{receipt=r;monitorFlowId='';render();},onError:e=>{lastActionError=operationErrorModel(e.message,{operation:'execution',receipt});$('replica-error').textContent=lastActionError.detail;renderWorkspaceGuidance();},isBusy:()=>busy||formDirty||!!monitorFlowId||!!lastActionError,run:work=>action('승인된 복제본 변경 적용 중…',work,'execution')});
const loadTestUI=setupLoadTestUI({getReceipt:()=>receipt,api,onReceipt:r=>{receipt=r;render();},isBusy:()=>busy||formDirty||!!monitorFlowId,runAction});
async function api(path,data){const response=await fetch('/api/v1'+path,data===undefined?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)});const result=await response.json();if(!response.ok)throw Object.assign(Error(result.error?.code??'request_failed'),{receipt:result});return result;}
function renderWorkspaceGuidance(){
  const page=document.body.dataset.workspaceView;
  const view=workspaceGuidanceModel(receipt,{page,target:$('execution-target').value,inputDirty:formDirty,operation:activeOperation,elapsedSeconds:operationStartedAt?Math.floor((Date.now()-operationStartedAt)/1000):0,monitorReceipt:monitoringReceipt()});
  $('workspace-guide').hidden=!view.visible;
  if(view.visible){
    $('workspace-guide-title').textContent=view.title;$('workspace-guide-route').textContent=view.route;
    for(const field of ['input','process','output'])$('workspace-guide-'+field).textContent=view.io[field];
    $('workspace-guide-status').textContent=view.status;$('workspace-guide-detail').textContent=view.detail;
    $('workspace-guide-next').hidden=!view.next;
    if(view.next){$('workspace-guide-next').href=view.next.href;$('workspace-guide-next').textContent=view.next.label+' →';}
  }
  $('workflow-error').hidden=!lastActionError||(!view.visible&&page!=='validation');
  if(lastActionError){
    $('workflow-error-title').textContent=lastActionError.title;$('workflow-error-detail').textContent=lastActionError.detail;
    $('workflow-error-next').href=lastActionError.href;$('workflow-error-next').textContent=lastActionError.label+' →';
  }
}
async function action(label,work,operation){
  if(busy)return;
  const focusToken=captureSelectionFocus(document);
  lastActionError=null;busy=true;activeOperation=operation;operationStartedAt=Date.now();
  elapsedTicker=setInterval(renderElapsed,1000);render();$('flow-status').textContent=label;
  try{await runAction(label,work);}
  catch(e){
    const recoveryFlowId=e.receipt?.flow_id??(['execution','reconciliation','feedback'].includes(operation)?receipt?.flow_id:null);
    if(recoveryFlowId){receipt=await api('/flows/'+recoveryFlowId).catch(()=>receipt);if(receipt)rememberFlow(receipt.flow_id);}
    lastActionError=operationErrorModel(e.message,{operation,receipt});
    $('flow-status').textContent=lastActionError.title;$('flow-status').classList.add('error-text');
  }finally{
    busy=false;activeOperation=null;clearInterval(elapsedTicker);elapsedTicker=null;
    render(false);restoreSelectionFocus(document,focusToken);
  }
}
function renderElapsed(){
  if(activeOperation==='analysis')renderWorkspaceGuidance();
  const start=activeOperation==='analysis'?operationStartedAt:Date.parse(receipt?.created_at);
  const timestamps=(receipt?.history??[]).map(e=>Date.parse(e.at)).filter(Number.isFinite);
  const end=busy?Date.now():timestamps.length?Math.max(...timestamps):start;
  const seconds=Math.max(0,Math.floor((end-start)/1000));
  $('pipeline-elapsed').textContent=Number.isFinite(seconds)?`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`:'—';
  if(activeOperation==='reasoning')$('reasoning-elapsed').textContent=Math.max(0,Math.floor((Date.now()-operationStartedAt)/1000))+'초';
  $('pipeline-elapsed').title='Flow 생성부터 마지막 기록까지의 시간입니다. 요청 중에는 현재 시각으로 갱신합니다.';
}
function renderPipeline(){
  const v=pipelineViewModel(receipt,{operation:activeOperation,inputDirty:formDirty,target:$('execution-target').value});
  replace('pipeline-stages',...v.stages.map((s,i)=>{
    const e=node('a',undefined,'pipeline-stage '+s.state);e.href=s.href;e.dataset.stage=s.id;
    const top=node('div',undefined,'stage-top');top.append(node('span',s.state==='done'?'✓':s.icon,'stage-icon'),node('span',({waiting:'대기',active:'진행 중',done:'확인',attention:'확인 필요',blocked:'대기'})[s.state],'stage-state'));
    e.append(top,node('span',`${String(i+1).padStart(2,'0')} / ${s.module}`,'stage-module'),node('strong',s.label),node('span',s.detail,'stage-detail'));return e;
  }));
  $('pipeline-count').textContent=`응답 확인 ${v.confirmedCount} / 5`;
  $('pipeline-current-stage').textContent=v.currentLabel;
  $('pipeline-flow-id').textContent=activeOperation==='analysis'?'새 Flow 응답 대기':receipt?.flow_id??'아직 생성되지 않음';
  $('pipeline-flow-id').title=$('pipeline-flow-id').textContent;
  renderElapsed();
  const events=pipelineEvents(receipt);
  const allEvents=pipelineEvents(receipt,{includeDeleted:true}),deletedCount=allEvents.length-events.length;
  const openIds=new Set([...$('event-log').querySelectorAll('details[open]')].map(e=>e.dataset.eventId)),scrollTop=$('event-log').scrollTop;
  $('event-count').textContent=`${events.length}건`;
  $('event-delete-all').disabled=busy||!events.length;
  $('event-restore').hidden=!deletedCount;$('event-restore').disabled=busy;$('event-restore').textContent=`삭제 기록 복원 (${deletedCount}건)`;
  $('event-message').hidden=!historyMessage;$('event-message').textContent=historyMessage;
  replace('event-log',...events.length?events.map(event=>{
    const row=node('li',undefined,event.state),entry=node('details',undefined,'event-item'),summary=node('summary'),time=node('time',new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(event.at)));
    entry.dataset.eventId=event.id;entry.open=openIds.has(event.id);
    time.dateTime=event.at;time.title=new Date(event.at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});
    const content=node('span',undefined,'event-content');content.append(node('strong',event.label));if(event.detail)content.append(node('small',event.detail));summary.append(time,content);
    const detail=node('div',undefined,'event-detail'),saved=historyEventDetails(receipt,event.id),info=node('dl',undefined,'event-info');
    for(const [label,value] of [['어플리케이션',receipt.application_profile?.app_id??receipt.input.application?.app_id],['기록 시각',new Date(event.at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' KST'],['처리 결과',event.detail??({requested:'요청 전달',done:'응답 확인',attention:'확인 필요'})[event.state]]]){if(value)info.append(node('dt',label),node('dd',value));}
    detail.append(info);
    if(saved.payload?.request&&typeof saved.payload.request==='string')detail.append(node('p',saved.payload.request,'event-explanation'));
    const requirements=saved.payload?.application_profile?.requirements;
    if(requirements)detail.append(node('p',`분석한 자원 · CPU ${requirements.compute.cpu_cores_min} vCPU · 메모리 ${requirements.compute.memory_mib_min/1024} GiB · ${requirements.accelerator.required?'GPU 필요':'CPU 추론'}`,'event-explanation'));
    if(saved.event_type==='selected'&&saved.payload?.name)detail.append(node('p','선택한 자원 · '+saved.payload.name,'event-explanation'));
    if(saved.payload?.decision?.reason)detail.append(node('p',reasonLabel(saved.payload.decision.reason),'event-explanation'));
    const scaling=saved.payload?.scaling_decision;
    if(scaling)detail.append(node('p',`운영 판단 · ${judgementLabel(scaling.action)} · 복제본 ${scaling.current_replicas} → ${scaling.desired_replicas}`,'event-explanation'));
    if(['reasoning_compared','reasoning_unavailable'].includes(saved.event_type)&&saved.payload){
      const compared=reasoningPanelModel({...receipt,selection_version:saved.payload.selection_version,reasoning_comparisons:[saved.payload]});
      detail.append(node('p',`${compared.summaryValue} · ${compared.summary}`,'event-explanation'));
      for(const trial of compared.rows)detail.append(node('p',`${trial.label} · ${trial.action} · ${trial.candidate} · ${trial.guard}`,'event-explanation'));
    }
    const raw=node('details',undefined,'event-raw');raw.append(node('summary','원본 기록·연결 자료 보기'),node('pre',JSON.stringify(saved,null,2)));detail.append(raw);
    entry.append(summary,detail);
    const remove=node('button','삭제','event-button event-delete');remove.type='button';remove.disabled=busy;remove.dataset.eventId=event.id;remove.setAttribute('aria-label',event.label+' 기록 삭제');remove.addEventListener('click',()=>changeHistory('delete',event.id));
    row.append(entry,remove);return row;
  }):[node('li',deletedCount?'현재 Flow의 실행 기록을 모두 삭제했습니다. 복원 버튼으로 다시 볼 수 있습니다.':receipt?'이 Flow에 저장된 요청 이력이 없습니다. 새 비교부터 단계별 기록이 표시됩니다.':'아직 기록된 요청이 없습니다.','empty')]);
  $('event-log').scrollTop=scrollTop;
}
function changeHistory(operation,eventId){
  if(busy||!receipt)return;
  const count=operation==='delete'?1:operation==='delete_all'?pipelineEvents(receipt).length:pipelineEvents(receipt,{includeDeleted:true}).length-pipelineEvents(receipt).length;
  return action('실행 기록 변경 중…',async()=>{
    receipt=await api(`/flows/${receipt.flow_id}/history`,{operation,event_id:eventId});
    historyMessage=operation==='restore'?`${count}건을 복원했습니다.`:`${count}건을 삭제했습니다. 삭제 기록 복원으로 되돌릴 수 있습니다.`;
    render();
  },'history').then(()=>{$('event-delete-all').disabled?$('event-restore').focus():$('event-delete-all').focus();});
}
$('event-delete-all').addEventListener('click',()=>changeHistory('delete_all'));
$('event-restore').addEventListener('click',()=>changeHistory('restore'));
function currentCandidate(){return infrastructureCandidates(receipt).find(c=>c.candidate_id===receipt?.selected_candidate_id);}
function monitoringReceipt(){return monitorFlowId?savedMonitoring.find(r=>r.flow_id===monitorFlowId):receipt;}
function renderExperimentResult(){
  const v=experimentResultModel(receipt,{inputDirty:formDirty,operation:activeOperation});
  $('experiment-result-refresh').disabled=busy||formDirty||!receipt;
  const header=node('div',undefined,'experiment-outcome'),heading=node('div');
  heading.append(node('h3',v.status),node('p',v.detail,'help'));header.append(heading);
  if(!v.empty)header.append(node('span',v.route,'module'));
  const content=[header];
  if(v.empty){const start=node('a','요구사항 입력으로 이동 →','text-button');start.href='#application';content.push(start);}
  else{
    content.push(node('p',`${v.app} · ${v.flowId}`,'experiment-identity'));
    const grid=node('div',undefined,'experiment-totals');
    grid.append(metric('확인된 실제 복제본',v.actualReplicas===null?'확인 전':v.actualReplicas+'개'),metric(receipt.execution?.status==='STOPPED'?'마지막 승인 목표':'승인된 목표',v.desiredReplicas==null?'—':v.desiredReplicas+'개'),metric('실제 적용 Revision',v.appliedRevision==null?'—':'Revision '+v.appliedRevision),metric('확인된 복제본 변화',v.replicaHistory||'변경 기록 없음'));
    content.push(grid);
  }
  const steps=node('ol',undefined,'experiment-steps');
  for(const [index,s] of v.stages.entries()){
    const row=node('li',undefined,'experiment-step '+s.state),title=node('div',undefined,'experiment-step-title'),body=node('div',undefined,'experiment-step-body'),link=node('a','상세 보기 →','text-button');link.href=s.href;
    title.append(node('span',String(index+1).padStart(2,'0'),'experiment-step-number'),node('strong',s.label));body.append(node('strong',s.status),node('p',s.detail));row.append(title,body,link);steps.append(row);
  }
  content.push(steps);if(!v.empty){const history=node('a','이 실험의 전체 실행 기록 →','text-button');history.href='#events';content.push(history);}
  replace('experiment-result',...content);
}
$('experiment-result-refresh').addEventListener('click',()=>action('현재 실험 결과 조회 중…',async()=>{receipt=await api('/flows/'+receipt.flow_id);render();},'result-refresh'));
$('experiment-result').addEventListener('click',event=>{
  const link=event.target.closest('a[href="#operation"]');
  if(!link||event.button>0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return;
  event.preventDefault();monitorFlowId='';render();navigation.navigate('#operation');
});
function renderReasoning(){
  const view=reasoningPanelModel(receipt),running=activeOperation==='reasoning';
  $('reasoning-run').disabled=busy||formDirty||!view.canRun;
  $('reasoning-run').textContent=running?'Qwen 비교 중…':view.entry?'다시 비교':'판단 방식 비교';
  $('reasoning-refresh').hidden=!view.canRefresh;$('reasoning-refresh').disabled=busy;
  $('reasoning-context').textContent=running?'같은 입력으로 Qwen 제안을 받아 조건을 검증하고 있습니다.':view.summary;
  $('reasoning-model').textContent=view.model;
  replace('reasoning-input',metric('이 실험의 어플리케이션',view.context.app),metric('실행할 모델·조건',view.context.workload),metric('선택한 자원',view.context.resource));
  $('reasoning-progress').hidden=!running;
  const cards=(running?[]:view.rows).map(row=>{
    const card=node('article',undefined,'reasoning-trial');
    card.append(node('h4',row.label),node('p',row.role,'reasoning-role'),node('strong',row.action,'reasoning-action'),node('p',row.candidate,'reasoning-candidate'),node('p',row.reason,'reasoning-reason'),node('span',row.guard,'metric-sub'),node('span',row.duration,'metric-sub'));return card;
  });
  replace('reasoning-results',...cards);
  $('reasoning-detail').hidden=!view.entry?.result;
  $('reasoning-json').textContent=view.entry?JSON.stringify(view.entry,null,2):'';
  replace('reasoning-originals',...view.rows.flatMap(row=>[node('h4',row.label),node('p',row.originalReason||'저장된 판단 설명이 없습니다.','reasoning-original')]));
  $('reasoning-execution-note').textContent=view.executionNote;
  $('reasoning-next').hidden=!view.nextHref||formDirty||busy;if(view.nextHref){$('reasoning-next').href=view.nextHref;$('reasoning-next').textContent=view.nextLabel+' →';}
  $('reasoning-time').textContent=view.entry?.finished_at?`저장 ${new Date(view.entry.finished_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} · 전체 요청 ${new Intl.NumberFormat('ko-KR').format(view.entry.elapsed_ms)} ms`:'';
}
let revisionBinding='',revisionFlowId='',revisionChoice={};
function saveRevisionChoice(){
  if(!receipt)return;
  const tab=document.querySelector('[data-planning-tab][aria-selected="true"]')?.dataset.planningTab;
  try{localStorage.setItem('khu-revisions-'+receipt.flow_id,JSON.stringify({...revisionChoice,currentSelectionVersion:receipt.selection_version,tab}));}catch{}
}
function selectPlanningTab(name){
  for(const button of document.querySelectorAll('[data-planning-tab]')){
    const active=button.dataset.planningTab===name;
    button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    $(button.getAttribute('aria-controls')).hidden=!active;
  }
}
const planningTabs=[...document.querySelectorAll('[data-planning-tab]')];
selectPlanningTab('reasoning');
for(const [index,button] of planningTabs.entries()){
  button.addEventListener('click',()=>{selectPlanningTab(button.dataset.planningTab);saveRevisionChoice();});
  button.addEventListener('keydown',event=>{
    const next=event.key==='ArrowRight'?(index+1)%planningTabs.length:event.key==='ArrowLeft'?(index+planningTabs.length-1)%planningTabs.length:event.key==='Home'?0:event.key==='End'?planningTabs.length-1:null;
    if(next===null)return;event.preventDefault();planningTabs[next].click();planningTabs[next].focus();
  });
}
function renderRevisions(){
  const binding=receipt?`${receipt.flow_id}:${receipt.selection_version}`:'';
  if(binding!==revisionBinding){
    let saved={};try{saved=JSON.parse(localStorage.getItem('khu-revisions-'+receipt?.flow_id)??'{}')??{};}catch{}
    revisionChoice=saved.currentSelectionVersion===receipt?.selection_version?saved:{};
    if(revisionFlowId!==receipt?.flow_id)selectPlanningTab(planningTabs.some(b=>b.dataset.planningTab===saved.tab)?saved.tab:'reasoning');
    revisionBinding=binding;revisionFlowId=receipt?.flow_id??'';
  }
  const view=revisionComparisonModel(receipt,revisionChoice);
  const options=(items,label)=>items.map(item=>{const option=node('option',label(item));option.value=item.value;return option;});
  replace('revision-selection',...view.groups.length?options(view.groups.map(g=>({...g,value:g.selectionVersion})),g=>`${g.isCurrent?'현재 선택':'이전 선택'} · ${g.candidateName} · 선택 #${g.selectionVersion} · 명세 ${g.revisions.length}개`):[node('option','자원 선택 전')]);
  $('revision-selection').disabled=!view.groups.length;
  const items=(view.group?.revisions??[]).map(item=>({...item,value:item.revision}));
  for(const [id,item] of [['revision-from',view.from],['revision-to',view.to]]){
    const pendingChange=id==='revision-to'&&items.length===1;
    replace(id,...items.length&&!pendingChange?options(items,r=>`R${r.revision} · ${judgementLabel(r.action)} · 복제본 ${r.replicas??'—'}개`):[node('option',pendingChange?'변경 명세 생성 대기':'명세 생성 전')]);
    $(id).disabled=id==='revision-to'?!view.canCompare:!view.from;
    if(item)$(id).value=item.revision;
  }
  if(view.group)$('revision-selection').value=view.group.selectionVersion;
  const context=view.group?`${view.group.isCurrent?'현재 VM 선택':'이전 VM 선택 조회'} · ${view.group.candidateName} · 선택 #${view.group.selectionVersion} · 저장 명세 ${items.length}개`:'배포 계획을 만들면 명세가 표시됩니다.';
  $('revision-context').textContent=context;
  replace('revision-timeline',...items.map(item=>{
    const row=node('li',undefined,'revision-entry'+(item.revision===view.from?.revision||item.revision===view.to?.revision?' compared':''));
    const title=node('div',undefined,'revision-entry-title');title.append(node('strong',`R${item.revision}`),node('span',`${item.phase==='INITIAL'?'최초 계획':'운영 변경'} · ${judgementLabel(item.action)}`));
    const date=new Date(item.at),time=node('time',Number.isFinite(date.getTime())?date.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' KST':'시각 미기록');if(Number.isFinite(date.getTime()))time.dateTime=item.at;
    row.append(title,time,node('p',`어플리케이션 복제본 ${item.replicas??'—'}개`,'revision-replicas'));
    if(item.reason)row.append(node('p',reasonLabel(item.reason),'revision-reason'));
    return row;
  }));
  const output=[];
  if(view.canCompare){
    const summary=node('div',undefined,'revision-summary');summary.append(node('strong',`R${view.from.revision} → R${view.to.revision}`),node('span',`변경 항목 ${view.changes.length}개`));output.push(summary);
    if(view.changes.length){
      const wrap=node('div',undefined,'revision-table-wrap'),table=node('table',undefined,'validation-table revision-table'),head=node('thead'),headers=node('tr'),body=node('tbody');table.setAttribute('aria-label','배포 명세 변경값');
      for(const title of ['변경 항목',`변경 전 · R${view.from.revision}`,`변경 후 · R${view.to.revision}`]){const cell=node('th',title);cell.scope='col';headers.append(cell);}head.append(headers);
      for(const change of view.changes){const row=node('tr'),label=node('th',change.label);label.scope='row';label.title=change.path;row.append(label,node('td',change.beforeLabel),node('td',change.afterLabel));body.append(row);}table.append(head,body);wrap.append(table);output.push(wrap);
    }else output.push(node('p','두 명세의 자원·실행 설정이 같습니다.','revision-empty'));
  }else output.push(node('p',view.from?'최초 계획이 저장됐습니다. 확장·축소 판단으로 변경 명세가 생성되면 비교할 수 있습니다.':'이 선택에는 아직 저장된 배포 명세가 없습니다. 배포 계획을 먼저 만드세요.','revision-empty'));
  replace('revision-comparison',...output);
  $('revision-origin').hidden=!view.from;$('revision-raw').hidden=!view.from;
  $('revision-from-title').textContent=view.from?`변경 전 · R${view.from.revision}`:'변경 전';
  $('revision-to-title').textContent=view.to?`변경 후 · R${view.to.revision}`:'변경 후';
  $('revision-from-json').textContent=view.from?JSON.stringify(view.from.raw,null,2):'';
  $('revision-to-json').textContent=view.to?JSON.stringify(view.to.raw,null,2):'';
  $('revision-to-original').hidden=!view.to;
  if(view.group){revisionChoice={selectionVersion:view.group.selectionVersion,fromRevision:view.from?.revision,toRevision:view.to?.revision};saveRevisionChoice();}
}
for(const [id,key] of [['revision-selection','selectionVersion'],['revision-from','fromRevision'],['revision-to','toRevision']])$(id).addEventListener('change',()=>{
  revisionChoice=id==='revision-selection'?{selectionVersion:Number($(id).value)}:{...revisionChoice,[key]:Number($(id).value)};
  renderRevisions();
});
$('reasoning-run').addEventListener('click',()=>action('규칙·Qwen·검증 결과 비교 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/reasoning-comparison`,{selection_version:receipt.selection_version});render();},'reasoning'));
$('reasoning-refresh').addEventListener('click',()=>action('저장된 판단 비교 결과 조회 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/reasoning-comparison`);render();},'reasoning-refresh'));
function comparisonTable(summary){
  const wrap=node('div',undefined,'validation-table-wrap'),table=node('table',undefined,'validation-table'),head=node('thead'),row=node('tr'),body=node('tbody');
  for(const title of ['비교 항목','예측 점수만 선택','조건·신뢰도 검증'])row.append(node('th',title));head.append(row);
  for(const [label,key] of [['선택 결정','decisions'],['측정 보상 기준과 일치','agreement'],['조건 미충족 선택','constraintViolations'],['판단 보류','abstentions']]){
    const r=node('tr');r.append(node('th',label),node('td',`${summary.baseline[key]}${key==='agreement'?' / '+summary.caseCount+' 사례':'건'}`),node('td',`${summary.validated[key]}${key==='agreement'?' / '+summary.caseCount+' 사례':'건'}`));body.append(r);
  }
  table.append(head,body);wrap.append(table);return wrap;
}
function renderResearch(phase,record=receipt){
  const select=$('research-'+phase+'-case'),current=select.value;
  const options=researchCases.filter(c=>c.phase===phase);
  const view=researchPanelModel(record,phase);
  select.replaceChildren(...options.length?options.map(c=>{const option=node('option',`${modelName(c.model_id)} · ${{NEW:'배포',HEALTHY:'유지',SLO_PRESSURE:'부하 증가',UNDERUTILIZED:'낮은 사용량'}[c.lifecycle_state]} · ${c.dataset==='placement'?'배포 후보':'복제본 실험'} · 학습 제외`);option.value=c.case_id;return option;}):[node('option','연구 사례 연결 대기')]);
  if(options.some(c=>c.case_id===current))select.value=current;
  else if(view.saved&&options.some(c=>c.case_id===view.saved.case_id))select.value=view.saved.case_id;
  select.disabled=busy||!options.length;for(const suffix of ['run','all'])$('research-'+phase+'-'+suffix).disabled=busy||formDirty||!record||!options.length;
  const elements=[];
  if(view.assessment){
    const grid=node('div',undefined,'output-grid');grid.append(metric('현재 기준 판단',judgementLabel(view.assessment.rule_action)),metric('학습 정책 제안',view.currentProposal));elements.push(grid);
    elements.push(node('p','학습 정책은 비교용이며 실제 실행 구성을 변경하지 않습니다.','help'));
  }
  if(view.saved){
    const c=view.saved,grid=node('div',undefined,'output-grid');
    if(c.comparison)grid.append(metric('예측 점수만 선택',judgementLabel(c.comparison.baseline.action),c.comparison.baseline.constraint_violation?'자원·배치 조건 미충족':'검증 전 선택'));
    grid.append(metric(c.comparison?'조건·신뢰도 검증 후':'기존 사례의 정책 선택',view.caseProposal),metric('측정 보상 기준 선택',view.oracle),metric('검증 후 비교 결과',view.agreement));elements.push(grid);
    elements.push(node('p',`${modelName(c.model_id)} · ${c.split==='test'?'테스트':'검증'} 그룹 · ${c.transition_evidence==='paired_static_measurements'?'복제본 설정별 실측 비교':'기존 추론 실측'} · 현재 어플리케이션 실측과 별도 사례`,'help'));
    const detail=node('details');detail.append(node('summary','선택한 후보·측정 결과 보기'));
    const list=node('div',undefined,'research-choices');
    for(const [label,item] of [['점수만 선택',c.comparison?.baseline],['검증 후 선택',c.proposal],['측정 기준',c.oracle]])if(item){list.append(node('p',`${label} · ${item.candidate_id??'판단 보류'} · 복제본 ${item.desired_replicas??'—'} · 측정 보상 ${item.reward??'—'}`));}
    detail.append(list,node('pre',JSON.stringify(c,null,2)));elements.push(detail);
  }
  const summary=policyComparisonSummary(record,phase);
  if(summary.caseCount){const detail=node('details');detail.append(node('summary',`이 단계 비교 요약 · ${summary.caseCount}사례`),node('p','같은 학습 제외 사례에서 예측 점수만 따른 선택과 자원 조건·신뢰도·학습 범위 검증을 포함한 선택을 비교합니다. 판단 보류는 성공으로 세지 않습니다.','help'),comparisonTable(summary));elements.push(detail);}
  if(!elements.length)elements.push(node('p','기존 실험 사례를 선택해 정책의 선택과 측정 보상 기준 선택을 비교하세요. 결과는 현재 Flow에 저장됩니다.','help'));
  replace('research-'+phase+'-output',...elements);
}
for(const phase of ['planning','operation'])$('research-'+phase+'-run').addEventListener('click',()=>action('기존 연구 실험으로 정책 선택 비교 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/research-policy`,{selection_version:receipt.selection_version,phase,case_id:$('research-'+phase+'-case').value});render();},'research'));
for(const phase of ['planning','operation'])$('research-'+phase+'-all').addEventListener('click',()=>action('이 단계 연구 사례 비교 중…',async()=>{const cases=researchCases.filter(c=>c.phase===phase);for(const c of cases)receipt=await api(`/flows/${receipt.flow_id}/research-policy`,{selection_version:receipt.selection_version,phase,case_id:c.case_id});if(cases.length)$('research-'+phase+'-case').value=cases.at(-1).case_id;render();},'research'));
function renderMonitoring(){
  const choices=savedMonitoring.filter(r=>r.flow_id!==receipt?.flow_id),selected=monitoringReceipt();
  const current=node('option',receipt?'현재 Flow · '+receipt.input.application.app_id:'현재 Flow · 어플리케이션 배포 전');current.value='';
  replace('monitor-record',current,...choices.map(r=>{const v=monitoringPanelModel(r),option=node('option',`${v.app} · ${v.source} · ${v.time?new Date(v.time).toLocaleString('ko-KR',{hour12:false}):r.flow_id}`);option.value=r.flow_id;return option;}));
  $('monitor-record').value=monitorFlowId;$('monitor-record').disabled=busy;
  $('monitor-return-current').hidden=!monitorFlowId||!receipt;$('monitor-return-current').disabled=busy;
  const v=monitoringPanelModel(selected),stored=!!monitorFlowId;
  $('monitor-preparation').hidden=!v.preparation;
  $('monitor-preparation-status').textContent=v.preparation?.status??'';
  $('monitor-preparation-detail').textContent=v.preparation?.detail??'';
  $('prometheus-panel').hidden=!['local','cloud'].includes(selected?.input?.execution_target);prometheusMonitor.render();
  $('monitor-context').textContent=`${stored?'저장된 실행 조회':'현재 Flow'} · ${v.app} · ${v.status}${selected?.replica_group?` · 적용 Revision ${selected.replica_group.applied_revision} · 실제 ${selected.replica_group.actual_replicas??'확인 중'}개`:''} · ${v.source}${v.time?' · '+new Date(v.time).toLocaleString('ko-KR',{hour12:false}):''}`;
  replace('app-measurements',...v.metrics.slice(0,4).map(m=>metric(m.label,m.value)));
  replace('app-measurement-extra',...v.metrics.slice(4).map(m=>metric(m.label,m.value)));
  const grid=node('div',undefined,'output-grid');grid.append(metric('운영 판단',v.decision),metric('복제본 판단',v.replicas),metric('판단에 사용한 관측',v.decisionSource));
  $('feedback-output').hidden=false;replace('feedback-output',grid,node('p',v.note,'output-reason'));
  const history=operationHistoryModel(selected);$('operation-history').hidden=!history.length;$('operation-history-title').textContent=`부하별 운영 판단 기록 · ${history.length}회`;
  const table=node('table',undefined,'validation-table'),head=node('thead'),headers=node('tr'),body=node('tbody');
  for(const title of ['관측','CPU · p95','운영 판단'])headers.append(node('th',title));head.append(headers);
  for(const item of history){const row=node('tr'),source=node('td',item.source),values=node('td',`${item.cpu} · ${item.latency}`),decision=node('td',`${item.decision} · ${item.replicas}`);source.title=new Date(item.time).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});row.append(source,values,decision);body.append(row);}
  table.append(head,body);replace('operation-history-table',table);
  const sample=selected?.measurement?.sample;$('inference-sample').hidden=!sample;
  if(sample)replace('inference-sample-output',...sample.texts.map((text,i)=>node('p',`${text}\n→ 연산 결과 클래스 ${sample.predictions[i]} · logits [${sample.logits[i].map(value=>value.toFixed(4)).join(', ')}]`)));
  renderResearch('operation',receipt);
}
async function loadMonitoringRecords(){
  savedMonitoring=monitoringRecords((await api('/flows')).flows??[]);
  if(!receipt&&!monitorFlowId)monitorFlowId=(savedMonitoring.find(r=>r.measurement)??savedMonitoring[0])?.flow_id??'';
  render();
}
$('monitor-record').addEventListener('change',()=>{monitorFlowId=$('monitor-record').value;render();});
$('monitor-reload').addEventListener('click',async()=>{try{await runAction('저장된 실행 기록 조회 중…',()=>loadMonitoringRecords());}catch{$('monitor-context').textContent='저장된 실행 기록을 다시 불러오지 못했습니다. 현재 화면의 기록은 보존됩니다.';}});
$('monitor-return-current').addEventListener('click',()=>{if(busy||!receipt)return;monitorFlowId='';render();prometheusMonitor.refresh({notify:true});});
$('resources-refresh').addEventListener('click',()=>action('같은 요구사항으로 현재 자원 다시 조회 중…',async()=>{
  receipt=resourceAvailabilityModel(receipt).canRefresh?await api(`/flows/${receipt.flow_id}/resource-refresh`,{selection_version:receipt.selection_version}):await api('/flows',reviewAnalysisInput(receipt));
  rememberFlow(receipt.flow_id);formDirty=false;
  const first=receipt.candidates.primary[0];if(first)await selectCandidate(first.candidate_id);render();
},'resources'));
async function selectCandidate(id){if(id===receipt.selected_candidate_id)return;receipt=analysisExecutionActive(receipt)?await reviewRunningCandidate(receipt,id,api):await api(`/flows/${receipt.flow_id}/selection`,{candidate_id:id,expected_selection_version:receipt.selection_version});rememberFlow(receipt.flow_id);formDirty=false;monitorFlowId='';render();}
function renderInfrastructureContext(){
  const v=infrastructureContextModel(receipt),detail=$('selected-resource-detail');detail.hidden=!v;$('selected-resource-details').hidden=!v;
  if(v){
    const grid=node('div',undefined,'resource-detail-grid');
    grid.append(metric('실행 조건',v.condition,`${receipt.input.model.runtime_engine} · ${receipt.input.model.precision}`),
      metric('요구사항의 최소 자원',v.minimumLabel,'VM 전체 사양과 어플리케이션의 최소 요구량을 구분합니다.'),
      metric('다음 진행',v.routeLabel,v.next));
    const provenance=node('details'),runIds=v.measurement?.runIds??[];
    provenance.append(node('summary','선택한 VM의 실험·사양 원본'),node('p',`모델 ${v.workload.modelId} · revision ${v.workload.revision}`,'help'));
    if(v.measurement){provenance.append(node('p',`${v.measurement.label}${Number.isFinite(v.measurement.cpuCoreLimit)?` · 실험 CPU 제한 ${v.measurement.cpuCoreLimit} vCPU`:''}`,'help'),node('p',runIds.length?`측정 회차: ${runIds.join(' · ')}`:'측정 회차 정보 없음','help'));}
    const selected=currentCandidate();if(selected?.source?.url?.startsWith('https://')){const link=node('a','VM 사양 출처','text-button');link.href=selected.source.url;link.target='_blank';link.rel='noopener noreferrer';provenance.append(link);}
    replace('selected-resource-detail',node('h3',`${v.name} · 선택한 인프라의 실험 내용`),node('p',v.role,'help'),grid,provenance);
  }
  for(const [id,stage] of [['planning-resource-context','planning'],['deployment-resource-context','deployment'],['operation-resource-context','operation']]){
    $(id).hidden=!v;if(!v)continue;
    const parts=[node('strong',`${v.name} · ${v.hardware}`),node('p',v.condition,'help')];
    if(stage==='planning')parts.push(node('p',`최소 요구량: ${v.minimumLabel} · ${v.next}`,'help'));
    else if(stage==='deployment')parts.push(node('p',`${v.routeLabel} · ${v.next}`,'help'));
    else if(v.route==='historical'){
      const metrics=node('div',undefined,'resource-detail-grid');metrics.append(metric('선택한 VM의 배치 p95',v.measurement?v.latency:'비교 측정 없음'),metric('선택한 VM의 입력 처리량',v.measurement?v.throughput:'비교 측정 없음'));
      parts.push(metrics,node('p','위 값은 선택한 VM의 저장된 벤치마크입니다. 아래 운영 판단은 데모 관측 입력으로 확인합니다.','help'));
    }else parts.push(node('p','아래 관측값과 운영 판단은 이 실행의 배포·측정 기록에 연결됩니다.','help'));
    replace(id,...parts);
  }
}
function render(resetStatus=true){
  renderWorkspaceGuidance();
  renderExperimentResult();
  agentsUi.render();
  const view=flowViewModel(receipt?{...receipt,input_dirty:formDirty}:null);if(resetStatus){$('flow-status').textContent=view.status;$('flow-status').classList.remove('error-text');}
  $('flow-context').textContent=`${view.analysis} · ${view.observation} · ${view.execution}`;
  renderPipeline();
  const cloud=(receipt?.input.execution_target??$('execution-target').value)==='cloud',formLocal=['local','cloud'].includes($('execution-target').value),local=['local','cloud'].includes(receipt?.input.execution_target??$('execution-target').value);$('local-app-info').textContent=$('execution-target').value==='cloud'?'비용 검토·승인을 거쳐 AWS VM에서 실행·측정하고 종료 결과를 기록합니다. 이 분석 버튼은 VM을 생성하지 않습니다.':$('execution-target').value==='local'?'선택한 BERT Tiny를 이 PC에서 실행·측정합니다. 인프라 추천과 배포 계획을 확인한 뒤 실행합니다.':'저장된 CPU·GPU VM 실측으로 후보를 비교합니다. 이후 Mock 실행과 예시 관측으로 판단 흐름을 확인합니다.';$('real-deployment').hidden=!local;$('historical-deployment').hidden=local;$('real-observation').hidden=!local||!!monitorFlowId;$('feedback-form').hidden=local||!!monitorFlowId;
  $('workspace-app').textContent=receipt?.application_profile?.app_id??$('app-id').value??'새 실험';
  $('workspace-model').textContent=receipt?`${modelName(receipt.input.model.model_id)} · 배치 ${receipt.input.model.batch_size} · 길이 ${receipt.input.model.max_length}`:cloud?'BERT Tiny · CPU/GPU VM 실행':formLocal?'BERT Tiny · 실제 로컬 CPU':'과거 VM 비교 · 조건 입력 전';
  $('workspace-resource').textContent=currentCandidate()?.name??'자원 선택 전';
  const live=analysisExecutionActive(receipt);
  $('analysis-lock-note').hidden=!live;$('analysis-lock-note').textContent='';
  if(live)$('analysis-lock-note').textContent='현재 AWS/앱 실행을 유지합니다. LLM과 요구사항을 바꿔 분석하거나 다른 자원을 선택하면 새 배포 검토로 저장됩니다. 실행 중인 VM과 앱은 바뀌지 않으며 상단의 기존 AWS 실험에서 계속 관측할 수 있습니다.';
  for(const id of ['cohort','objective','throughput','latency'])$(id).parentElement.hidden=formLocal;
  $('benchmark-conditions').hidden=formLocal;
  $('cost-conditions').hidden=formLocal;
  $('requirement-route').textContent=$('execution-target').value==='local'?'이 PC의 BERT Tiny CPU 실행으로 판단과 적용 결과를 확인합니다.':$('execution-target').value==='cloud'?'분석과 계획 생성은 VM을 만들지 않습니다. 실제 실행은 비용 검토와 별도 승인을 거칩니다.':'저장된 VM 실측으로 자원을 비교하며 이후 실행과 관측은 모의 경로입니다.';
  $('local-model').parentElement.hidden=!formLocal;
  $('infrastructure').querySelector('h2').textContent=local?'실제로 실행할 자원을 선택하세요':'실험한 VM 중에서 선택하세요';
  const natural=$('analysis-mode').value==='natural_language';$('natural-language-field').hidden=!natural;$('structured-conditions').hidden=natural;$('scenario-field').hidden=natural;$('natural-request').required=natural;
  const limits=requirementExecutionLimits({target:$('execution-target').value,scenario:$('scenario').value});
  const gpuOption=$('scenario').querySelector('option[value="gpu"]');gpuOption.hidden=!limits.gpuAvailable;gpuOption.disabled=!limits.gpuAvailable;
  $('max-replicas').max=limits.maxReplicas;$('max-replicas').readOnly=limits.fixedReplicas;
  if(limits.fixedReplicas)$('max-replicas').value=1;
  $('replica-limit-help').hidden=!limits.fixedReplicas;
  $('resource-stress-settings').hidden=formLocal||natural;
  $('scenario').querySelector('option[value="excessive"]').hidden=$('scenario').value!=='excessive';
  const serviceConditions=$('service-conditions');
  if(serviceConditions.classList.contains('service-primary')!==formLocal)serviceConditions.open=formLocal;
  serviceConditions.classList.toggle('service-primary',formLocal);serviceConditions.hidden=natural;
  $('performance-goals').hidden=natural&&formLocal;
  $('resource-stress').disabled=natural||busy;
  $('app-form').querySelectorAll('input,select,textarea').forEach(e=>e.disabled=analysisControlDisabled(e.id,{target:$('execution-target').value,mode:$('analysis-mode').value,busy,live}));$('analyze').disabled=busy;$('new-flow').disabled=analysisControlDisabled('new-flow',{busy,live});$('recording-start').disabled=busy;
  const p=receipt?.application_profile;$('profile-summary').hidden=!p||formDirty;if(p){
    const r=p.requirements,details=node('details'),slo=r.slo??{};
    details.append(node('summary','세부 조건·분석 정보'),node('div',`저장소 ${r.compute.storage_gib_min} GiB · 복제본 ${r.deployment.replicas_min}–${r.deployment.replicas_max}`),node('p',receipt.analysis?.mode==='llm'?`분석에 사용한 LLM · ${receipt.analysis.actual_model}`:'직접 지정한 조건을 분석했습니다.','help'));
    replace('profile-summary',node('strong','분석된 요구사항'),node('div',`CPU ${r.compute.cpu_cores_min} vCPU 이상 · 메모리 ${r.compute.memory_mib_min/1024} GiB 이상 · ${r.accelerator.required?'GPU 필요':'CPU 추론'}`),node('div',`서비스 성능 · ${slo.latency_p95_ms_max>0?`응답시간 p95 ${slo.latency_p95_ms_max} ms 이하`:'응답시간 미지정'} · ${slo.throughput_rps_min>0?`처리량 ${slo.throughput_rps_min} 요청/초 이상`:'처리량 미지정'}`),details);
  }
  const shownCandidates=infrastructureCandidates(receipt),selectableCount=shownCandidates.filter(c=>c.selectable).length;
  $('candidate-overview').hidden=!receipt;
  const availability=resourceAvailabilityModel(receipt);
  $('candidate-overview').textContent=!cloud&&local?`관측된 로컬 자원 ${availability.discovered}개 · 선택 가능 ${selectableCount}개`:`CPU ${shownCandidates.filter(c=>!c.shape.gpu_count).length}종 · GPU ${shownCandidates.filter(c=>c.shape.gpu_count).length}종 · 선택 가능 ${selectableCount}종`;
  $('resource-issues').hidden=!local||cloud||!availability.issues.length;replace('resource-issues',...availability.issues.map(issue=>node('p',issue,'help')));
  $('resources-refresh').hidden=!receipt?.application_profile;$('resources-refresh').disabled=busy||formDirty||!receipt?.application_profile;
  $('resources-refresh').textContent='같은 요구사항으로 자원 다시 추천';
  let candidateRank=0;
  const rows=shownCandidates.map(c=>{
    const v=candidateCardModel(c),e=node('tr',undefined,'candidate'+(c.candidate_id===receipt.selected_candidate_id?' selected':'')+(!c.selectable?' unavailable':''));
    e.dataset.device=v.kind;
    const radio=node('input');radio.type='radio';radio.name='candidate';radio.value=c.candidate_id;radio.checked=c.candidate_id===receipt.selected_candidate_id;radio.dataset.candidateControl='radio';radio.dataset.candidateId=c.candidate_id;radio.setAttribute('aria-label',c.name+' 선택');radio.disabled=busy||formDirty||!c.selectable;
    radio.addEventListener('change',()=>action('인프라 선택 기록 중…',()=>selectCandidate(c.candidate_id),'selection'));
    const cell=(label,...children)=>{const td=node('td');td.dataset.label=label;td.append(...children);return td;};
    const resource=node('label',undefined,'candidate-choice');
    const name=node('span');name.append(node('strong',c.name),node('span',v.title,'metric-sub'));
    resource.append(radio,name);
    const kind=node('div',undefined,'candidate-heading');kind.append(node('span',v.kind,'candidate-kind'),node('span',c.selectable?`후보 ${++candidateRank}`:'','candidate-rank'));
    const context=infrastructureContextModel(receipt,c),rationale=node('details',undefined,'candidate-rationale');
    rationale.append(node('summary','사양·실험 내용'),node('p',context.role,'reason'),node('p',context.condition,'help'),node('p',v.reason,'reason'),node('span',v.source,'source'));
    if(context.measurement){rationale.append(node('p',`${context.measurement.label}${Number.isFinite(context.measurement.cpuCoreLimit)?` · 실험 CPU ${context.measurement.cpuCoreLimit} vCPU`:''}`,'help'));}
    rationale.append(node('p',context.benchmarkTarget?`비교 목표: ${context.benchmarkTarget}`:context.routeLabel,'help'));
    e.append(cell('실행 자원',resource,kind),cell('CPU · 메모리 · GPU',node('span',v.hardware,'hardware')),cell('측정 성능',node('strong',!cloud&&local?'어플리케이션 실행 후 측정':v.latency),node('span',!cloud&&local?'현재 PC 관측 자원':v.throughput,'metric-sub')),cell('비용',node('span',receipt.input.workload.objective==='work'?v.workCost:v.cost)),cell('적합성',node('span',v.fit,'fit'),rationale));return e;
  });
  if(rows.length){const table=node('table',undefined,'candidate-table'),head=node('thead'),tr=node('tr'),body=node('tbody');table.setAttribute('aria-label',cloud?'AWS CPU·GPU VM 후보 비교':local?'로컬 실행 자원 비교':'CPU·GPU VM 후보 비교');for(const label of ['VM / 실행 자원','CPU · 메모리 · GPU','측정 성능','비용','적합성']){const th=node('th',label);th.scope='col';tr.append(th);}head.append(tr);body.append(...rows);table.append(head,body);replace('candidates',table);}else replace('candidates',node('div',receipt?(local?'현재 요구 자원을 만족하는 로컬 실행 대상이 없습니다.':'현재 요청에 맞는 자원이 없습니다. 요구사항을 조정해 다시 비교하세요.'):'요구사항 분석 후 CPU·GPU 후보가 여기에 표시됩니다.','empty'));
  const selected=currentCandidate();$('selected-summary').hidden=!selected;if(selected){const v=candidateCardModel(selected);replace('selected-summary',metric('선택한 인프라',selected.name,v.hardware),metric(!cloud&&local?'관측된 실행 자원':selected.measurement?.comparison==='historical_reference'?'과거 참고 측정':'동일 조건 벤치마크',!cloud&&local?'현재 CPU·메모리·디스크':v.latency,!cloud&&local?'어플리케이션 실행 뒤 별도 실측':v.throughput+(selected.measurement?.cpu_core_limit?' · 실험 CPU '+selected.measurement.cpu_core_limit+' vCPU':'')),metric('비용 비교',receipt.input.workload.objective==='work'?v.workCost:v.cost,cloud?'생성 전 현재 가격 별도 검토':local?'외부 클라우드 요금 없음':'유효한 가격 스냅샷 기준'));}
  renderInfrastructureContext();
  $('selection-note').textContent=!cloud&&local?(selected?'이 PC의 관측 자원을 선택했습니다.':'실제 로컬 자원 확인'):selected?`${selected.name} 선택 · ${modelName(receipt.input.model.model_id)} · 배치 ${receipt.input.model.batch_size}`:'다섯 VM 사양 · 동일 조건 측정값 비교';
  $('make-plan').disabled=busy||formDirty||!selected?.selectable||!!receipt?.execution;
  const shapes=receipt?shownCandidates:catalog?.entries.map(shape=>({name:shape.name,shape,reason:'과거 실험 사양',origin:'historical_shape'}))??[];
  replace('shape-list',...shapes.map(c=>{const e=node('div',undefined,'shape-row');e.append(node('strong',c.name),node('span',`${c.shape.cpu_cores} vCPU · ${c.shape.memory_mib/1024} GiB RAM${c.shape.gpu_count?' · L4 24 GB':''}`),node('span',c.reason));if(receipt){const choose=node('button',c.candidate_id===receipt.selected_candidate_id?'선택됨':live?'새 배포 검토로 선택':'선택','text-button');choose.setAttribute('aria-label',`${c.name} 선택`);choose.dataset.candidateControl='button';choose.dataset.candidateId=c.candidate_id;choose.disabled=busy||formDirty||!c.selectable;choose.addEventListener('click',()=>action('인프라 선택 기록 중…',()=>selectCandidate(c.candidate_id),'selection'));e.append(choose);}return e;}));
  const g=receipt?.geon;const plan=g?.deployment_plan;
  renderRevisions();
  $('plan-resource').hidden=!selected||formDirty;
  if(selected){const context=infrastructureContextModel(receipt,selected);replace('plan-resource',metric('선택한 인프라',context.name,context.hardware),metric('실행 조건',context.condition,context.routeLabel));}
  $('planning-spec').hidden=!g||formDirty;
  if(g&&!formDirty){
    const config=g.desired_deployment_spec?.inference_configuration??plan?.inference_configuration;
    const grid=node('div',undefined,'output-grid planning-result');
    grid.append(metric('배포 판단',judgementLabel(g.decision?.action??g.state)),metric('시작 복제본',Number.isInteger(config?.replicas)?`${config.replicas}개`:'계획 없음'));
    replace('plan-output',grid,node('p',reasonLabel(g.decision?.reason)??'배포 판단 응답을 기록했습니다.','output-reason'));$('plan-output').className='';
    const infrastructure=plan?.desired_infrastructure;
    replace('plan-configuration',metric('조건 검증',judgementLabel(g.guard?.status??'판단 대기')),metric('최소 요구 CPU·메모리',infrastructure?`${infrastructure.cpu_cores_per_node} vCPU · ${infrastructure.memory_mib_per_node/1024} GiB`:'계획 없음'),metric('노드·저장소',infrastructure?`${infrastructure.node_count}개 · 노드당 ${infrastructure.storage_gib_per_node} GiB`:'계획 없음'));
    $('plan-detail').textContent=JSON.stringify({decision:g.decision,guard:g.guard,manifest:g.desired_deployment_spec},null,2);
  }else{
    $('plan-output').className='empty';$('plan-output').textContent=formDirty?'요구사항이 바뀌었습니다. 변경한 조건을 다시 분석하세요.':selected?'배포 계획을 만들면 배포 판단과 시작 복제본 수가 표시됩니다.':receipt?.application_profile?'배포할 인프라를 먼저 선택하세요.':'요구사항을 분석하고 배포할 인프라를 선택하세요.';
    $('plan-detail').textContent='아직 생성된 계획이 없습니다.';
  }
  const planApproved=!formDirty&&g?.decision?.action==='DEPLOY'&&g?.guard?.status==='APPROVED';
  $('planning-make').hidden=formDirty||!selected||!!g||!!receipt?.execution;
  $('planning-make').disabled=$('make-plan').disabled;
  $('planning-make').textContent=receipt?.state==='PLAN_UNKNOWN'?'판단 결과 다시 확인 →':'배포 계획 만들기 →';
  $('plan-next').hidden=!planApproved;
  $('plan-prerequisite').hidden=!formDirty&&!!selected&&(!g||planApproved);
  $('plan-prerequisite').href=formDirty||!receipt?.application_profile?'#application':'#infrastructure';
  $('plan-prerequisite').textContent=formDirty?'변경한 요구사항 분석 →':!receipt?.application_profile?'요구사항 분석으로 이동 →':selected?'다른 인프라 선택 →':'인프라 선택으로 이동 →';
  $('plan-action-note').textContent=planApproved?(cloud?'다음 단계에서 비용 검토·승인과 VM 실행을 진행합니다.':local?'다음 단계에서 이 PC에 실제 프로세스를 배포합니다.':'다음 단계에서 승인된 계획의 Mock 실행 기록을 저장합니다.'):'계획 만들기는 배포 판단과 명세를 저장합니다. 실행은 배포 실행 단계에서 진행합니다.';
  $('execute').disabled=busy||formDirty||g?.decision?.action!=='DEPLOY'||g?.guard?.status!=='APPROVED'||!!receipt?.execution;
  $('app-prepare').disabled=busy||formDirty||!local||g?.guard?.status!=='APPROVED'||!!receipt?.appdeploy;
  const deployment=deploymentViewModel(receipt,{target:$('execution-target').value,busy,inputDirty:formDirty});
  $('app-submit').disabled=!deployment.canDeploy;$('app-submit').hidden=cloud;$('app-prepare').hidden=cloud;$('app-stop').textContent=cloud?'어플리케이션만 중지':'어플리케이션 중지';
  $('app-new').hidden=!deployment.canRestart;$('app-submit').hidden=cloud||deployment.canRestart;
  $('deployment-title').textContent=deployment.title;$('deployment-description').textContent=deployment.description;
  $('deployment-badge').textContent=deployment.status;$('deployment-badge').dataset.status=deployment.status;
  $('deployment-next').hidden=!deployment.nextHref||deployment.canRestart;if(deployment.nextHref){$('deployment-next').href=deployment.nextHref;$('deployment-next').textContent=deployment.nextLabel+' →';}
  $('start-local').disabled=busy||live;
  const deployModel=receipt?.input.model??executionOptions?.local.models[Number($('local-model').value)];
  replace('deployment-summary',metric('배포할 어플리케이션',receipt?.application_profile?.app_id??$('app-id').value),metric('모델',deployModel?modelName(deployModel.model_id):'BERT Tiny',deployModel?`배치 ${deployModel.batch_size} · 길이 ${deployModel.max_length}`:undefined),metric('실행 위치',selected?.name??'이 PC · 로컬 CPU',cloud?'Tumblebug · SSH 실행':local?'실제 프로세스':'과거 VM 비교'));
  replicaUI.render();loadTestUI.render();$('replica-details').hidden=!!monitorFlowId||$('replica-panel').hidden;cloudUI.render();
  $('app-refresh').disabled=busy||!receipt?.appdeploy||receipt.appdeploy.status==='PREPARED';
  $('app-stop').disabled=busy||(!receipt?.appdeploy?.group_schema&&(!receipt?.execution||receipt.execution.status!=='RUNNING'))||(receipt?.appdeploy?.group_schema&&receipt.appdeploy.status==='STOPPED');
  $('app-measure').disabled=busy||formDirty||!!monitorFlowId||receipt?.execution?.status!=='RUNNING'||replicaView(receipt).pendingRevision||['PENDING','UNKNOWN'].includes(receipt?.feedback_state)||!!receipt?.replica_group&&receipt.replica_group.operation_state!=='IDLE';
  $('app-status').textContent=receipt?.appdeploy?`${receipt.execution?.status??receipt.appdeploy.status} · ${receipt.execution?.target_profile_id??'이 PC'}${receipt.execution?.execution_id?' · '+receipt.execution.execution_id:''}`:deployment.canDeploy?'배포 버튼으로 어플리케이션 등록과 실제 배포를 진행하세요.':'승인된 배포 계획을 준비하면 배포 버튼이 활성화됩니다.';
  $('app-logs').textContent=JSON.stringify({request:receipt?.appdeploy?.delivery?.request,logs:receipt?.appdeploy?.logs},null,2)??'아직 실행 로그가 없습니다.';
  const x=receipt?.execution;$('feedback-submit').disabled=busy||formDirty||!x||['PENDING','UNKNOWN'].includes(receipt?.feedback_state);$('feedback-reconcile').hidden=!['PENDING','UNKNOWN'].includes(receipt?.feedback_state);$('feedback-reconcile').disabled=busy;
  $('execution-output').textContent=x?`${local?'실제 어플리케이션':'Mock 실행 기록'} · ${x.status} · ${x.execution_id}`:local?'계획 확인 후 실제 어플리케이션을 실행하세요.':'배포 계획 승인 후 Mock 실행을 기록해 운영 판단을 확인하세요.';
  const scaling=receipt?.feedback?.geon?.scaling_decision;renderMonitoring();
  renderResearch('planning');
  renderReasoning();
  llmComparison.render();
  $('validation-comparison-context').textContent=receipt?`비교를 기록할 현재 실험 · ${receipt.input.application.app_id} · ${receipt.flow_id}${formDirty?' · 요구사항을 다시 분석해야 비교할 수 있습니다.':''}`:'배포·운영 정책을 비교하려면 요구사항 화면에서 실험을 먼저 만드세요.';
  $('validation-current-operation').hidden=!receipt?.execution;
  $('result').hidden=!receipt?.resource_recommendation;
  if(receipt?.resource_recommendation){
    const comparison=policyComparisonSummary(receipt),reasoning=reasoningPanelModel(receipt),grid=node('div',undefined,'output-grid');
    grid.append(metric('Flow ID',receipt.flow_id),metric('어플리케이션',reasoning.context.app),metric(local?'실행 자원':'선택한 VM',selected?.name??'선택 없음'),metric('자원 선택 버전',receipt.selection_version??'—'));
    replace('result-summary',grid);
    $('receipt-detail').textContent=JSON.stringify({flow_id:receipt.flow_id,geon_flow_id:receipt.geon_flow_id,trace_id:receipt.trace_id,selection_version:receipt.selection_version,selected_candidate_id:receipt.selected_candidate_id,hashes:receipt.hashes,execution:receipt.execution,observation_origin:receipt.observation_origin,analysis_mode:receipt.analysis?.mode,reasoning_comparison:reasoning.entry,policy_comparison:comparison,scaling_judgement:scaling,scaling_guard:receipt.feedback?.geon?.operation_agent_execution?.scaling_guard},null,2);
  }
}
function collectInput(){const local=['local','cloud'].includes($('execution-target').value),chosen=local?executionOptions.local.models[Number($('local-model').value)]:null,cohort=local?policy.cohorts.find(c=>['model_id','model_revision','batch_size','max_length'].every(k=>c[k]===chosen[k])):policy.cohorts.find(c=>c.cohort_id===$('cohort').value),gpu=$('scenario').value==='gpu',app_id=$('app-id').value.trim();const spec={app_id,app_version:'1.0.0',workload_type:'CLASSIFICATION',request_pattern:'ONLINE',cpu_cores:Number($('cpu').value),memory_mib:Number($('memory').value)*1024,storage_gib:Number($('storage').value),replicas_min:1,replicas_max:Number($('max-replicas').value),latency_p95_ms_max:Number($('service-latency').value),throughput_rps_min:Number($('service-throughput').value),artifact:local?executionOptions.local.artifact:{type:'OCI_IMAGE',uri:'demo://khu/text-classifier:1.0.0'}};if(gpu)Object.assign(spec,{accelerator_type:'GPU',accelerator_count:1,accelerator_memory_mib_per_device:16000});return {replica_group_enabled:local,execution_target:$('execution-target').value,application:{app_id,app_version:spec.app_version},model:{model_id:cohort.model_id,model_revision:cohort.model_revision,runtime_engine:'PYTORCH',precision:'FP32',batch_size:cohort.batch_size,max_length:cohort.max_length,max_concurrency:1},analysis_input:buildAnalysisInput({mode:$('analysis-mode').value,request:$('natural-request').value,appSpec:spec,candidateId:llmComparison.candidateId()}),form_spec:spec,workload:{...cohort,min_throughput:Number($('throughput').value),max_batch_p95_ms:Number($('latency').value),input_count:1000000,objective:$('objective').value}};}
for(const event of ['input','change'])$('app-form').addEventListener(event,()=>{if(receipt)formDirty=true;render();});
$('app-form').addEventListener('invalid',e=>{
  for(let parent=e.target.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  const label=e.target.labels?.[0]?.textContent?.trim()??'입력';
  $('flow-status').textContent=`${label}: ${e.target.validationMessage}`;
  $('flow-status').classList.add('error-text');
},true);
$('app-form').addEventListener('submit',e=>{e.preventDefault();action('요구사항 분석 · 인프라 후보 비교 중…',async()=>{historyMessage='';monitorFlowId='';receipt=await api('/flows',collectInput());formDirty=false;rememberFlow(receipt.flow_id);const first=receipt.candidates.primary[0];if(first)await selectCandidate(first.candidate_id);render();navigation.navigate('#infrastructure');},'analysis');});
for(const id of ['make-plan','planning-make'])$(id).addEventListener('click',()=>action('배포 판단 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/plan`,{selection_version:receipt.selection_version});render();navigation.navigate('#planning');},'plan'));
$('execute').addEventListener('click',()=>action('Mock 실행 영수증 저장 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/mock-executions`,{selection_version:receipt.selection_version});render();navigation.navigate('#operation');},'execution'));
$('feedback-form').addEventListener('submit',e=>{e.preventDefault();action('운영 판단 중…',async()=>{const now=new Date(),acceptedAt=Date.parse(receipt.execution?.occurred_at),start=new Date(Math.max(now-60000,Number.isFinite(acceptedAt)?acceptedAt:0));const observation={source:'demo_input',started_at:start.toISOString(),ended_at:now.toISOString(),metrics:{resource:{cpu_average_percent:Number($('observed-cpu').value),memory_peak_mib:1024,accelerator_average_percent:receipt.application_profile.requirements.accelerator.required?Number($('observed-cpu').value):0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:Number($('observed-latency').value),throughput_rps:Number($('observed-throughput').value),error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};receipt=await api(`/flows/${receipt.flow_id}/feedback`,{selection_version:receipt.selection_version,observation});render();},'feedback');});
$('app-submit').addEventListener('click',()=>action('어플리케이션 등록·계획 전달·실제 배포 중…',async()=>{receipt=await deploySelectedApp(receipt,api,r=>{receipt=r;monitorFlowId='';render();});render();navigation.navigate(receipt.execution?.status==='RUNNING'?'#operation':'#deployment');},'execution').then(()=>{if(receipt?.execution?.status==='RUNNING')return prometheusMonitor.refresh();}));
for(const [id,operation,label] of [['app-prepare','prepare','어플리케이션 등록 명세 준비 중…'],['app-refresh','status','실제 어플리케이션 상태·로그 조회 중…'],['app-stop','stop','실제 어플리케이션 중지 확인 중…'],['app-measure','measure','BERT Tiny 실제 추론·측정·운영 판단 중…']])$(id).addEventListener('click',()=>action(label,async()=>{receipt=await api(`/flows/${receipt.flow_id}/appdeploy/${operation}`,operation==='status'?undefined:{selection_version:receipt.selection_version});render();if(operation==='measure'){monitorFlowId='';renderMonitoring();navigation.navigate('#operation');}},operation==='measure'?'feedback':'execution'));
$('start-local').addEventListener('click',()=>{if(busy)return;lastActionError=null;receipt=null;monitorFlowId='';formDirty=false;historyMessage='';rememberFlow(null);$('execution-target').value='local';$('scenario').value='cpu';$('memory').value=1;render();navigation.navigate('#application');});
$('execution-target').addEventListener('change',()=>{$('scenario').value=requirementExecutionLimits({target:$('execution-target').value,scenario:$('scenario').value}).scenario;if(!receipt&&$('execution-target').value==='local')$('memory').value=1;render();});
$('feedback-reconcile').addEventListener('click',()=>action('기존 운영 판단 결과 조회 중…',async()=>{receipt=await api(`/flows/${receipt.flow_id}/feedback-reconciliation`);render();if(receipt.feedback_state==='UNKNOWN')$('flow-status').textContent='같은 관측에 대한 운영 판단 요청의 수락 결과를 아직 확인하지 못했습니다.';},'reconciliation'));
$('observation-preset').addEventListener('change',()=>{const values={high:[85,80,100],normal:[50,10,400],low:[10,5,10]}[$('observation-preset').value];['observed-cpu','observed-latency','observed-throughput'].forEach((id,i)=>$(id).value=values[i]);});
$('analysis-mode').addEventListener('change',()=>render());
$('scenario').addEventListener('change',()=>{$('cpu').value=$('scenario').value==='excessive'?256:2;$('memory').value=$('scenario').value==='excessive'?1024:2;});
$('resource-stress').addEventListener('click',()=>{$('scenario').value='excessive';$('scenario').dispatchEvent(new Event('change',{bubbles:true}));$('structured-conditions').open=true;});
function newFlow(){lastActionError=null;receipt=null;monitorFlowId='';formDirty=false;historyMessage='';$('execution-target').value='cloud';$('analysis-mode').value='natural_language';$('scenario').value='cpu';$('memory').value=2;$('max-replicas').value=1;$('service-latency').value=5000;$('natural-request').value=$('natural-request').defaultValue;rememberFlow(null);render();navigation.navigate('#application');}
$('new-flow').addEventListener('click',newFlow);
$('recording-start').addEventListener('click',()=>action('직접 시연 시작 화면 준비 중…',async()=>{
 const preparation=await prepareAwsRecording(api,{confirmCleanup:message=>window.confirm(message),onProgress:message=>{$('flow-status').textContent=message;}});
 if(!preparation.ready)return;
 newFlow();$('flow-status').textContent='시연 준비 완료 · LLM 선택부터 직접 진행하세요. 아직 VM을 생성하지 않았습니다.';
},'recording'));
$('app-new').addEventListener('click',()=>{if(deploymentViewModel(receipt,{busy}).canRestart)newFlow();});
const navigation=setupNavigation(document,window,{onNavigate:(_active,hash)=>{
  const panel=document.getElementById(hash.slice(1))?.closest('[role="tabpanel"]');
  const tab=planningTabs.find(button=>button.getAttribute('aria-controls')===panel?.id);
  if(tab)selectPlanningTab(tab.dataset.planningTab);
  renderWorkspaceGuidance();
}});
try{
 executionOptions=await api('/execution-options');
 for(const [i,m] of executionOptions.local.models.entries()){const option=node('option',m.label);option.value=i;$('local-model').append(option);}
 [policy,catalog]=await Promise.all(['khu-cpu-policy.json','khu-vm-spec-catalog.json'].map(n=>fetch('data/'+n).then(r=>r.json())));
 for(const c of policy.cohorts){const option=node('option',`${modelName(c.model_id)} · 배치 ${c.batch_size} · 길이 ${c.max_length}`);option.value=c.cohort_id;$('cohort').append(option);}
 const activeRuns=(await api('/cloud/readiness').catch(()=>({pending_cleanup:[]}))).pending_cleanup;
 const saved=initialPlatformFlow(window.location.href,localStorage.getItem('khu-flow-id'),activeRuns);
 if(saved){
  receipt=await api('/flows/'+saved);rememberFlow(receipt.flow_id);const i=receipt.input;
  $('execution-target').value=i.execution_target??'historical';$('app-id').value=i.application.app_id;$('cohort').value=i.workload.cohort_id;
  const localModelIndex=executionOptions.local.models.findIndex(m=>['model_id','model_revision','batch_size','max_length'].every(k=>m[k]===i.model[k]));
  $('local-model').value=localModelIndex<0?0:localModelIndex;$('objective').value=i.workload.objective;$('throughput').value=i.workload.min_throughput;$('latency').value=i.workload.max_batch_p95_ms;
  $('analysis-mode').value=i.analysis_input.input_type;if(i.analysis_input.candidate_id)llmComparison.choose(i.analysis_input.candidate_id,false);
  $('natural-request').value=i.analysis_input.request??$('natural-request').defaultValue;
  const req=receipt.application_profile?.requirements,s=i.form_spec??i.analysis_input.app_spec??{cpu_cores:req?.compute.cpu_cores_min??2,memory_mib:req?.compute.memory_mib_min??2048,storage_gib:req?.compute.storage_gib_min??20,replicas_max:req?.deployment.replicas_max??1,accelerator_count:req?.accelerator.count_min??0,latency_p95_ms_max:req?.slo.latency_p95_ms_max??5000,throughput_rps_min:req?.slo.throughput_rps_min??0};
  $('scenario').value=s.accelerator_count?'gpu':s.cpu_cores>32?'excessive':'cpu';$('cpu').value=s.cpu_cores;$('memory').value=s.memory_mib/1024;$('storage').value=s.storage_gib;
  $('max-replicas').value=s.replicas_max;$('service-latency').value=s.latency_p95_ms_max??0;$('service-throughput').value=s.throughput_rps_min??0;
 }
 render();
}catch{$('flow-status').textContent='PoC 플랫폼과 현재 AWS 실행 상태를 확인해 주세요.';}

try{await loadMonitoringRecords();}catch{renderMonitoring();}
try{researchCases=(await api('/research-policy/cases')).cases??[];renderResearch('planning');renderResearch('operation',receipt);}catch{for(const phase of ['planning','operation'])$('research-'+phase+'-output').textContent='기존 연구 정책 서비스 연결을 확인해 주세요.';}
await llmComparison.refresh();
await prometheusMonitor.refresh();
navigation.restoreLocation();

await agentsUi.refresh();
