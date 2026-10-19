import {judgementLabel} from './view-model.mjs';
import {cloudPreparationFailed,cloudPreparationLabel} from './cloud-state.mjs';

const definitions=[
  {id:'llmop',module:'요구사항 분석',label:'요구사항 분석',icon:'L',href:'#application'},
  {id:'resource',module:'인프라 추천',label:'인프라 추천',icon:'R',href:'#infrastructure'},
  {id:'geon',module:'배포 계획',label:'배포 계획 판단',icon:'G',href:'#planning'},
  {id:'mock',module:'모의 실행',label:'실행 기록',icon:'M',href:'#deployment'},
  {id:'feedback',module:'운영 판단',label:'운영 판단',icon:'O',href:'#operation'},
];
export function pipelineViewModel(receipt,{operation=null,inputDirty=false,target='historical'}={}){
  const r=operation==='analysis'?null:receipt;
  const stages=definitions.map(d=>({...d,state:'waiting',detail:'대기'}));
  const local=['local','cloud'].includes(r?.input?.execution_target??target);if(local)Object.assign(stages[3],{id:'appdeploy',module:'배포 실행',label:'어플리케이션 배포',icon:'A'});
  const set=(index,state,detail)=>Object.assign(stages[index],{state,detail});
  if(r?.application_profile)set(0,'done','ApplicationProfile 확인');
  if(r?.resource_recommendation)set(1,'done',r.candidates?.primary?.length?'추천 결과 확인':'추천 결과 확인 · 후보 없음');
  if(r?.state==='ANALYSIS_PENDING')set(0,'active','분석 응답 확인 중');
  if(r?.state==='RECOMMENDATION_PENDING')set(1,'active','추천 응답 확인 중');
  if(r?.state==='STOPPED')set(r.application_profile?1:0,'attention','응답 확인 필요');
  if(r?.state==='PLAN_PENDING'||operation==='plan')set(2,'active','배포 판단 응답 확인 중');
  if(r?.state==='PLAN_UNKNOWN'){set(2,'attention','기존 판단 조회 필요');set(3,'blocked','판단 확인 대기');}
  if(r?.geon){
    const approved=r.geon.decision?.action==='DEPLOY'&&r.geon.guard?.status==='APPROVED';
    set(2,approved?'done':'attention',judgementLabel(r.geon.decision?.action??r.geon.state));
    if(!approved)set(3,'blocked','승인된 계획 필요');
  }
  if(operation==='execution')set(3,'active',local?'배포 응답 확인 중':'Mock 영수증 저장 중');
  if(local&&r?.appdeploy&&!r.execution)set(3,['UNKNOWN','PENDING'].includes(r.appdeploy.status)?'attention':'active',r.appdeploy.status==='PREPARED'?'어플리케이션 등록·요청 확인 · 실행 대기':'기존 배포 상태 확인 필요');
  if(r?.execution)set(3,local&&(r.execution.status.includes('FAILED')||['UNKNOWN','PENDING'].includes(r.execution.status))?'attention':'done',`${local?'실제 어플리케이션':'Mock 기록'} · ${r.execution.status}`);
  if(r?.input?.execution_target==='cloud'&&r.cloud&&!r.execution)set(3,['CREATE_UNKNOWN','CLEANUP_PENDING','PREFLIGHT_FAILED'].includes(r.cloud.state)?'attention':['CREATE_REQUESTED','CREATED','PREPARING'].includes(r.cloud.state)?'active':'waiting','Tumblebug · '+r.cloud.state);
  if(cloudPreparationFailed(r))set(3,'attention',cloudPreparationLabel(r));
  if(r?.feedback)set(4,r.feedback.geon?.scaling_decision?'done':'attention',judgementLabel(r.feedback.geon?.scaling_decision?.action??'판단 확인 필요'));
  if(r?.feedback_state==='PENDING'||operation==='feedback'||operation==='reconciliation')set(4,'active','운영 판단 응답 확인 중');
  if(r?.feedback_state==='UNKNOWN'&&operation!=='reconciliation')set(4,'attention','기존 피드백 조회 필요');
  const reasoning=(r?.reasoning_comparisons??[]).findLast(e=>e.selection_version===r.selection_version);
  if(operation==='reasoning'||reasoning?.status==='PENDING')set(2,'active','규칙·AI·검증 비교 중');
  else if(reasoning?.status==='UNKNOWN')set(2,'attention','기존 AI 비교 결과 조회 필요');
  if(operation==='analysis')set(0,'active','분석·추천 응답 확인 중');
  if(inputDirty&&operation!=='analysis'){
    stages.forEach((_,i)=>set(i,'waiting','새 입력 분석 대기'));set(0,'attention','입력 변경 · 다시 분석');
  }
  const current=stages.find(s=>s.state==='active')??stages.find(s=>s.state==='attention');
  const last=[...stages].reverse().find(s=>s.state==='done');
  const currentLabel=current?`${current.module} · ${current.detail}`:!r?'어플리케이션 요구사항 입력':r.state==='SELECTED'?'VM 선택됨 · 배포 계획 대기':last?.id==='feedback'?'운영 판단 기록됨':last?.id==='appdeploy'?'실제 어플리케이션 상태 확인됨':last?.id==='mock'?'Mock 기록됨 · 운영 피드백 대기':last?.id==='geon'?`배포 계획 확인됨 · ${local?'어플리케이션 실행':'Mock 실행'} 대기`:'인프라 후보 확인 · VM 선택 대기';
  return {stages,currentLabel,confirmedCount:stages.filter(s=>s.state==='done').length};
}

const eventLabels={
  reasoning_requested:'판단 방식 비교 요청',reasoning_compared:'판단 방식 비교 결과',reasoning_unavailable:'판단 방식 비교 미완료',
  research_policy_compared:'KHU 연구 정책 · 기존 실험 비교',
  analysis_requested:'요구사항 분석 요청',profile_received:'요구사항 분석 결과 확인',
  resource_received:'인프라 추천 결과 확인',analysis_stopped:'분석·추천 응답 확인 필요',
  plan_requested:'배포 계획 요청',plan_received:'배포 판단 결과 확인',
  plan_unknown:'기존 배포 판단 조회 필요',mock_recorded:'모의 실행 · RUNNING 기록 저장',
  feedback_requested:'운영 판단 요청',feedback_received:'운영 판단 결과 확인',
  feedback_unknown:'기존 운영 판단 조회 필요',
  appdeploy_prepared:'어플리케이션 등록·전송 요청 확인',appdeploy_requested:'실제 어플리케이션 배포 요청',appdeploy_status:'실제 프로세스 상태 확인',
  resources_refreshed:'같은 요구사항의 자원 상태 재조회',group_requested:'복제본 배포 요청',group_running:'실제 복제본 실행 확인',
  replica_apply_requested:'승인된 Revision 적용 요청',revision_applied:'새 Revision 적용 확인',group_stopped:'실제 복제본 종료 확인',
};
export function pipelineEvents(receipt,{includeDeleted=false}={}){
  const deleted=new Set(receipt?.history_view?.deleted_event_ids??[]);
  return (receipt?.history??[]).map((e,index)=>({...e,id:`event-${index}`})).filter(e=>Number.isFinite(Date.parse(e.at))&&(eventLabels[e.event]||e.event==='selected')&&(includeDeleted||!deleted.has(e.id))).map(e=>({
    id:e.id,
    at:e.at,label:e.event==='selected'?`VM 선택 · ${e.candidate_id}`:eventLabels[e.event],
    detail:Number.isInteger(e.actual_replicas)?`Revision ${e.revision} · 실제 복제본 ${e.actual_replicas}개`:e.revision?`Revision ${e.revision}`:e.action?judgementLabel(e.action):e.selection_version?`선택 버전 ${e.selection_version}`:null,
    state:e.event.endsWith('unknown')||e.event==='analysis_stopped'||e.event==='reasoning_unavailable'?'attention':e.event.endsWith('requested')?'requested':'done',
  }));
}
