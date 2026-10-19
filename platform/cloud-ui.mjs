import {isCloudGpuType,cloudRuntimeIssue} from './cloud-types.mjs';
import {cloudPreparationLabel} from './cloud-state.mjs';
const active=['CREATE_REQUESTED','CREATE_UNKNOWN','CREATED','PREPARING','RUNNING','CLEANUP_PENDING'];
export async function requestCloudCleanup(api,receipt,{confirm=message=>window.confirm(message)}={}){
 if(!confirm('현재 실험의 VM과 응용을 실제로 종료합니다. 촬영을 모두 마쳤나요? 계속 실행하려면 취소를 누르세요.'))return null;
 return api('/flows/'+receipt.flow_id+'/cloud/cleanup',{});
}
export async function prepareAwsRecording(api,{pause=ms=>new Promise(resolve=>setTimeout(resolve,ms)),clock=Date.now,timeoutMs=120000,onProgress=()=>{},confirmCleanup=()=>true}={}){
 const pending=(await api('/cloud/readiness')).pending_cleanup??[];
 if(!pending.length)return {ready:true,previous_flow_id:null};
 const previous=pending[0];
 if(pending.length!==1||previous.kind||previous.state!=='RUNNING'||!/^flow-[\w-]+$/.test(previous.flow_id??''))throw Error('recording_scope_conflict');
 const path='/flows/'+previous.flow_id;
 const assertOwned=r=>{if(r.flow_id!==previous.flow_id||r.input?.execution_target!=='cloud'||r.cloud?.run_id!==previous.run_id||r.cloud.intent?.flow_id!==previous.flow_id||r.cloud.resource?.provider_instance_id!==previous.instance_id||!/^i-[a-f0-9]{8,17}$/.test(previous.instance_id??''))throw Error('recording_scope_conflict');};
 const current=await api(path);assertOwned(current);
 if(current.cloud.state!=='RUNNING'||current.execution?.status!=='RUNNING')throw Error('recording_scope_conflict');
 if(!confirmCleanup('새 시연을 준비하면 현재 실행 중인 VM과 응용을 종료합니다. 촬영 중이면 취소를 누르세요.'))return {ready:false,cancelled:true};
 onProgress('기존 AWS 시연 VM 종료·정리 요청 중…');
 // A lost reply is reconciled through reads, never a repeated delete request.
 try{await api(path+'/cloud/cleanup',{});}catch{}
 const started=clock();
 while(clock()-started<timeoutMs){
  const next=await api(path+'/cloud/status');assertOwned(next);
  if(next.cloud.state==='CLEANED'&&next.cloud.cleanup?.confirmed===true&&next.execution?.status==='STOPPED')return {ready:true,previous_flow_id:previous.flow_id};
  onProgress('AWS VM과 연결 자원의 실제 종료 확인 중…');await pause(1500);
 }
 throw Error('recording_reset_timeout');
}
export function cloudView(r,connection={}){
 const c=r?.cloud,state=c?.state==='READY'&&r?.cloud_job_requested?'CREATE_REQUESTED':c?.state??'PLAN_REQUIRED';
 const blockedByOtherRun=(connection.pending_cleanup??[]).some(run=>run.run_id!==c?.run_id);
 const runtimeIssue=r?.input?.execution_target==='cloud'?cloudRuntimeIssue(r.application_profile?.requirements,r.candidates?.all?.find(candidate=>candidate.candidate_id===r.selected_candidate_id)):null;
 return {visible:r?.input?.execution_target==='cloud',state,blockedByOtherRun,runtimeIssue,executionAvailability:connection.enabled===true?'enabled':connection.connection_configured&&connection.provider_audit_configured?'disabled':'unconfigured',canReview:!(state==='CLEANED'&&c?.resource?.provider_instance_id)&&!runtimeIssue&&!!r?.geon?.guard&&r.geon.guard.status==='APPROVED'&&!active.includes(state),canApprove:state==='REVIEWED'&&connection.enabled===true,canPreflight:!blockedByOtherRun&&['APPROVED','PREFLIGHT_FAILED'].includes(state)&&!c.approval?.consumed_at&&Date.parse(c.approval?.expires_at)>Date.now(),canCreate:!blockedByOtherRun&&state==='READY'&&connection.enabled===true&&c.preflight?.ready===true&&Date.parse(c.approval?.expires_at)>Date.now()&&!r.cloud_job_requested,canCleanup:active.includes(state),canStatus:!!c};
}
export function cloudProgress(r){const c=r?.cloud??{},state=c.state??'PLAN_REQUIRED',current=({PLAN_REQUIRED:0,REVIEW_INCOMPLETE:1,REVIEWED:1,APPROVED:2,PREFLIGHT_FAILED:2,READY:3,CREATE_REQUESTED:3,CREATE_UNKNOWN:3,CREATED:3,PREPARING:3,RUNNING:4,CLEANUP_PENDING:4,CLEANED:4})[state]??0;return ['비용 검토','실행 승인','준비 검사','어플리케이션 배포','종료·정리'].map((label,i)=>({label,done:[!!c.review,!!c.approval,c.preflight?.ready===true,!!c.app?.group_id,c.cleanup?.confirmed===true][i],current:i===current}));}
export function setupCloudUI({api,getReceipt,onReceipt,isBusy=()=>false,runAction=(_label,work)=>work()}){
 let connection={enabled:false},inFlight=false,displayedReviewId=null;const $=id=>document.getElementById(id),panel=$('cloud-panel');
 const labels={PLAN_REQUIRED:'배포 계획을 먼저 준비하세요',REVIEWED:'비용 검토 완료',REVIEW_INCOMPLETE:'예상 비용 확인 · 연결 설정 대기',APPROVED:'이번 실행 비용 승인됨',READY:'준비 검사 통과',PREFLIGHT_FAILED:'준비 검사 결과 확인 필요',CREATE_REQUESTED:'VM 준비 중',CREATE_UNKNOWN:'기존 생성 요청 확인 중',CREATED:'VM 확인 · 어플리케이션 준비 중',PREPARING:'모델·어플리케이션 준비 중',RUNNING:'클라우드 어플리케이션 실행 중',CLEANUP_PENDING:'실제 종료·부속 자원 확인 중',CLEANED:'실제 종료·정리 확인 완료'};
 const buttons={review:'cloud-review',approve:'cloud-approve',preflight:'cloud-preflight',create:'cloud-create',status:'cloud-status',cleanup:'cloud-cleanup'};
 const actionLabels={review:'현재 VM 비용 조회 중…',approve:'이번 실행 승인 요청 중…',preflight:'VM 실행 준비 검사 중…',create:'VM·어플리케이션 실행 요청 중…',status:'VM 실행 상태 조회 중…',cleanup:'VM·응용 종료 요청 중…'};
 for(const [action,id] of Object.entries(buttons))$(id).addEventListener('click',async()=>{if(inFlight||isBusy())return;inFlight=true;render();$('cloud-error').textContent='';try{await runAction(actionLabels[action],async()=>{const r=getReceipt(),value=action==='approve'?{cost_review_id:r.cloud.review.cost_review_id,intent_sha256:r.cloud.intent.intent_sha256}:action==='status'?undefined:action==='review'?{selection_version:r.selection_version,max_lifetime_seconds:Number($('cloud-lifetime').value)}:{};const next=action==='cleanup'?await requestCloudCleanup(api,r):await api('/flows/'+r.flow_id+'/cloud/'+action,value);if(next)onReceipt(next);});}catch(e){$('cloud-error').textContent=({cloud_fresh_experiment_required:'이 실험은 종료됐습니다. 요구사항 화면에서 같은 조건을 다시 분석하면 새 실행용 계획이 만들어집니다.',previous_cleanup_required:'이전 실행의 종료 확인을 먼저 완료하세요.',cleanup_watcher_unavailable:'정리 감시를 시작한 뒤 준비 검사를 진행하세요.',live_cloud_disabled:'클라우드 연결 설정을 완료해야 실제 실행할 수 있습니다.',cloud_plan_changed:'계획이 바뀌었습니다. 비용을 다시 검토하세요.',cloud_cpu_gpu_requirements_mismatch:cloudView(getReceipt(),connection).runtimeIssue?.message??'요구사항의 CPU/GPU 추론 방식과 선택한 VM이 다릅니다. 자원을 다시 선택하세요.',cloud_gpu_replica_bounds:'GPU 실행은 최대 복제본 1개로 요구사항을 다시 분석하세요.'})[e.message]??'연결 또는 준비 검사 결과를 확인해 주세요. VM 생성 여부는 상태 조회에서 확인할 수 있습니다.';const r=getReceipt();try{onReceipt(await api('/flows/'+r.flow_id));}catch{}}finally{inFlight=false;render();}});
 function render(){const r=getReceipt(),v=cloudView(r,connection),owned=(connection.pending_cleanup??[]).filter(run=>/^flow-[\w-]+$/.test(run.flow_id??''));
  const notice=$('active-cloud-notice'),links=$('active-cloud-links');if(notice&&links){notice.hidden=!owned.length;links.replaceChildren();for(const run of owned){const link=document.createElement('a');link.href='/?flow='+encodeURIComponent(run.flow_id)+'#deployment';link.className='text-button';link.textContent=(run.flow_id===r?.flow_id?'현재 AWS 실험':'기존 AWS 실험')+' · '+(labels[run.state]??run.state)+' →';links.append(link);}}
  panel.hidden=!v.visible;if(!v.visible)return;$('cloud-state').textContent=v.state==='PLAN_REQUIRED'&&v.canReview?'현재 비용을 검토하세요':labels[v.state]??v.state;$('cloud-connection').textContent=({enabled:'Tumblebug 실행 어댑터',disabled:'AWS 실제 실행 꺼짐',unconfigured:'연결 설정 대기'})[v.executionAvailability];const busy=inFlight||isBusy();for(const [action,id] of Object.entries(buttons))$(id).disabled=busy||!v['can'+action[0].toUpperCase()+action.slice(1)]||action==='approve'&&!$('cloud-cost-check').checked;
  const stages=$('cloud-progress');stages.replaceChildren();for(const [i,stage] of cloudProgress(r).entries()){const item=document.createElement('li');item.textContent=(stage.done?'✓':String(i+1).padStart(2,'0'))+' '+stage.label;item.className=stage.done?'complete':stage.current?'current':'';if(stage.current)item.setAttribute('aria-current','step');stages.append(item);}
  $('cloud-next').textContent=({PLAN_REQUIRED:v.canReview?'먼저 현재 요금을 확인하세요. 비용 조회는 VM을 만들지 않습니다.':'배포 계획 페이지에서 실행 조건을 확인하세요.',REVIEW_INCOMPLETE:'아래 연결 설정을 준비한 뒤 비용을 다시 검토하세요. 현재는 VM 실행 버튼이 잠겨 있습니다.',REVIEWED:'검토 비용과 실행 시간을 확인하고 이번 실행을 승인하세요.',APPROVED:'준비 검사에서 연결·저장 공간·정리 감시를 확인하세요.',PREFLIGHT_FAILED:'연결·실행·정리 상세에서 통과하지 못한 항목을 확인하세요.',READY:'VM·어플리케이션 실행을 누르면 한 대의 VM 준비부터 실제 어플리케이션 배포까지 이어집니다.',CREATE_REQUESTED:'VM 생성 결과를 확인하고 있습니다.',CREATE_UNKNOWN:'기존 생성 요청의 결과를 확인하고 있습니다. 새 생성 요청은 보내지 않습니다.',CREATED:'확인된 VM에 모델과 어플리케이션을 준비하고 있습니다.',PREPARING:'모델 설치와 어플리케이션 준비가 끝나면 실제 실행 상태가 표시됩니다.',RUNNING:'모니터링 페이지에서 성능을 측정하세요. VM·응용 종료 요청은 시연을 모두 마친 뒤 누르세요.',CLEANUP_PENDING:'AWS의 실제 종료와 연결 자원 정리를 확인한 뒤 다음 실험을 진행합니다.',CLEANED:'이 실험의 VM 종료와 정리가 확인됐습니다. 기록을 보관하고 새 실험을 시작할 수 있습니다.'})[v.state]??'현재 실행 상태를 확인하세요.';
  if(v.state==='CLEANED'&&r.cloud?.failure){$('cloud-state').textContent=cloudPreparationLabel(r);$('cloud-next').textContent=r.cloud.failure.code==='cloud_cleanup_in_progress'?'VM 종료 요청으로 응용 준비가 중단됐습니다. 분석과 계획은 보관돼 있습니다. 현재 비용 검토부터 다시 실행할 수 있습니다.':'어플리케이션 배포 준비가 완료되지 않았습니다. VM과 연결 자원은 정리됐으며 실패 단계는 연결·실행·정리 상세에 보관돼 있습니다.';}
  if(v.state==='CREATE_REJECTED'){$('cloud-state').textContent='GPU 용량 부족으로 VM 생성 거절';$('cloud-next').textContent='AWS 생성 요청 거절 기록과 생성 자원 없음이 확인됐습니다. 새 비용 검토 후 다른 실행 조건을 선택할 수 있습니다.';}
  if(v.executionAvailability==='disabled'&&['PLAN_REQUIRED','REVIEWED','REVIEW_INCOMPLETE'].includes(v.state))$('cloud-next').textContent=(v.canReview?'현재 비용 조회는 가능합니다.':'먼저 위의 «배포 계획 만들기로 이동»에서 계획을 준비하세요.')+' 연결 설정은 등록돼 있지만 실제 AWS 실행은 꺼져 있어 실행 승인과 VM 생성 버튼은 잠겨 있습니다.';
  if(v.blockedByOtherRun&&!active.includes(v.state))$('cloud-next').textContent='기존 AWS 실험을 유지 중입니다. 위의 기존 실험 링크에서 촬영과 관측을 계속할 수 있습니다. 새 요구사항 분석·추천·비용 검토는 가능하며, 새 VM 실행은 기존 실험의 종료·정리 확인 후 진행합니다.';
  const changeResource=$('cloud-change-resource');if(changeResource)changeResource.hidden=!v.runtimeIssue;
  if(v.runtimeIssue&&!active.includes(v.state)){$('cloud-state').textContent='요구사항과 실행 대상 확인 필요';$('cloud-next').textContent=v.runtimeIssue.message;}
  const candidate=r.cloud?.intent?.candidate_id??r.candidates?.all?.find(c=>c.candidate_id===r.selected_candidate_id)?.name??'',gpu=isCloudGpuType(candidate),checks=connection.checks??{},configItems=[['ssh_key','연구실 SSH 키'],[gpu?'gpu_image':'cpu_image',gpu?'GPU 실행 이미지':'CPU 실행 이미지'],['provider_audit','AWS 종료·부속 자원 조회'],['execution_enabled','실제 실행 사용 설정']];
  const setup=$('cloud-setup');setup.hidden=connection.enabled===true&&configItems.every(([key])=>checks[key]===true);const list=$('cloud-setup-checks');list.replaceChildren();for(const [key,label] of configItems){const item=document.createElement('li');item.textContent=label+' · '+(checks[key]===true?'등록됨':'설정 필요');list.append(item);}
  const q=r?.cloud?.review;if(q&&q.cost_review_id!==displayedReviewId){$('cloud-lifetime').value=String(q.lifetime_seconds);displayedReviewId=q.cost_review_id;}else if(!q)displayedReviewId=null;$('cloud-cost').textContent=q?`시간당 $${Number(q.hourly_usd).toFixed(4)} · 최대 ${q.lifetime_seconds/60}분 컴퓨트 $${q.max_compute_usd.toFixed(4)} · 비컴퓨트 계획 여유 $${q.non_compute_margin_usd}`:'선택한 VM의 현재 요금을 조회합니다.';
  $('cloud-preflight').textContent=v.state==='PREFLIGHT_FAILED'?'03 준비 검사 다시 실행':'03 준비 검사';
  $('cloud-resource').textContent=r.cloud?.resource?`${r.cloud.intent.candidate_id} · ${r.cloud.resource.provider_instance_id} · ${r.cloud.state}`:'VM 생성 전';$('cloud-lifetime').disabled=busy||!!r.cloud_job_requested;
  $('cloud-details').textContent=r.cloud?JSON.stringify({run_id:r.cloud.run_id,checks:r.cloud.preflight?.checks,deadline:r.cloud.deadline_at,provider:r.cloud.last_provider_status,cleanup:r.cloud.cleanup,failure:r.cloud.failure,error_code:r.cloud.error_code},null,2):'';
 }
 $('cloud-cost-check').addEventListener('change',render);
 api('/cloud/readiness').then(x=>{connection=x;render();}).catch(()=>render());
 // Only GET status is polled. No inference, approval, bootstrap or mutation.
 const timer=setInterval(async()=>{if(inFlight||isBusy())return;try{connection=await api('/cloud/readiness');render();}catch{}const r=getReceipt();if(!r?.cloud_run_id||!(r.cloud_job_requested||active.includes(r.cloud?.state)))return;try{const next=await api('/flows/'+r.flow_id+'/cloud/status');if(getReceipt()?.flow_id===r.flow_id)onReceipt(next);}catch{}},10000);
 return {render,dispose:()=>clearInterval(timer)};
}
