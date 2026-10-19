import {cloudPreparationFailed,cloudPreparationLabel} from './cloud-state.mjs';
import {cloudCandidatePolicy,cloudRuntimeIssue} from './cloud-types.mjs';
import {replicaView,measurementNeedsRefresh} from './replicas-ui.mjs';
const number=n=>Number.isFinite(n)?new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(n):null;
export const modelName=id=>{const name=String(id??'').split('/').at(-1);return ({'bert_uncased_L-2_H-128_A-2':'BERT Tiny','bert_uncased_L-4_H-256_A-4':'BERT Mini','bert_uncased_L-4_H-512_A-8':'BERT Small','bert-base-uncased':'BERT Base','albert-base-v2':'ALBERT Base','distilbert-base-uncased':'DistilBERT','electra-small-discriminator':'ELECTRA Small','roberta-base':'RoBERTa Base'})[name]??name;};
export function measurementEvidenceModel(receipt,selected){
  const local=receipt?.input?.execution_target==='local',raw=local?receipt?.measurement:selected?.measurement;
  const title=local?'이 Flow의 실제 어플리케이션 실측':'선택한 VM의 과거 벤치마크';
  if(!raw)return {title,metrics:[],raw:null,note:local?'어플리케이션 배포 후 실측을 실행하면 서비스 p95·처리량·요청 수가 여기에 기록됩니다.':'요구사항을 분석하고 VM을 선택하면 해당 조건의 벤치마크 근거가 표시됩니다.'};
  const display=(value,unit)=>Number.isFinite(value)?number(value)+unit:'—';
  const metrics=local?[
    {label:'서비스 응답 p95',value:display(raw.metrics?.inference?.latency_p95_ms,' ms')},
    {label:'처리량',value:display(raw.metrics?.inference?.throughput_rps,' 요청/초')},
    {label:'실측 요청',value:display(raw.sample_count,' 요청')},
    {label:'어플리케이션 RSS 메모리',value:display(raw.metrics?.resource?.memory_peak_mib,' MiB')},
  ]:[
    {label:'배치 p95',value:display(raw.batch_p95_ms,' ms')},
    {label:'입력 처리량',value:display(raw.throughput,' 입력/초')},
  ];
  return {title,metrics,raw,note:local?'이 PC에서 실행한 어플리케이션의 HTTP 측정 결과입니다.':'동일 모델·배치·입력 길이로 조회한 과거 측정 결과입니다.'};
}
export function captureSelectionFocus(document){const d=document.activeElement?.dataset;return d?.candidateControl?{kind:d.candidateControl,id:d.candidateId}:null;}
export function restoreSelectionFocus(document,token){if(!token)return;const control=[...document.querySelectorAll('[data-candidate-control]')].find(e=>e.dataset.candidateControl===token.kind&&e.dataset.candidateId===token.id);if(control&&!control.disabled)control.focus();}
export function infrastructureCandidates(record){
  const primary=record?.candidates?.primary??[],saved=record?.candidates?.all??primary;
  const all=record?.input?.execution_target==='cloud'?saved.map(c=>cloudCandidatePolicy(c,record.application_profile?.requirements)):saved;
  const rank=new Map(primary.map((c,i)=>[c.candidate_id,i]));
  return all.slice().sort((a,b)=>Number(!!b.selectable)-Number(!!a.selectable)||(rank.get(a.candidate_id)??all.length)-(rank.get(b.candidate_id)??all.length));
}
export const judgementLabel=code=>({DEPLOY:'배포 가능',REJECT:'배포 거절',RETRY:'재검토',APPROVED:'조건 확인 통과',REJECTED:'조건 미충족',KEEP:'유지',SCALE_OUT:'확장',SCALE_IN:'축소'})[code]??code;
export const reasonLabel=text=>({'The selected resource candidate satisfies the minimum deployment requirements.':'선택한 자원이 최소 배포 조건을 충족합니다.','SLO evidence requires one bounded replica increase.':'서비스 SLO 초과를 근거로 복제본을 한 개 늘리는 판단입니다.','Healthy SLO and sustained low utilization allow one bounded replica decrease.':'서비스 SLO를 충족하고 사용량이 낮아 복제본을 한 개 줄이는 판단입니다.','SLO and utilization remain within the configured operating range.':'서비스 SLO와 사용량이 현재 운영 범위에 있어 유지합니다.','SLO is violated, but replicas are already at the configured maximum.':'서비스 SLO를 초과했지만 최대 복제본 수에 도달해 현재 수를 유지합니다.'})[text]??text;
export function candidateCardModel(c){const m=c.measurement;return {title:c.display_name,kind:c.shape.gpu_count?'GPU':'CPU',hardware:`${c.shape.cpu_cores} vCPU · ${number(c.shape.memory_mib/1024)} GiB RAM${c.shape.gpu_count?' · '+c.shape.gpu_model+' '+c.shape.gpu_memory_gb+' GB':''}`,fit:c.execution_eligible===false?'실행 방식 불일치':c.selectable?'자원 조건 충족':c.feasible?'배포 정보 확인 필요':'조건 미충족',reason:c.execution_reason??c.reason,source:({demo_input:'데모 자원',observed:'관측 자원',historical_shape:'과거 실험 사양'})[c.origin],latency:m?`배치 p95 ${number(m.batch_p95_ms)} ms`:'비교 측정 없음',throughput:m?`${number(m.throughput)} 입력/초`:'비교 측정 없음',cost:c.origin==='observed'?'로컬 실행':c.price?.hourly_usd?`$${c.price.hourly_usd}/시간`:'가격 확인 필요',workCost:c.price?.work_usd?`$${number(c.price.work_usd)} / 100만 입력`:'작업 비용 확인 필요',detail:c};}
export function flowViewModel(r){
  const s=r?.state,failed=cloudPreparationFailed(r);
  const realExecution=['local_process','cloud_vm'].includes(r?.execution?.mode);
  const executionStatus=realExecution?'실제 어플리케이션 '+r.execution.status:null;
  const step=!r?1:s==='ANALYZED'?2:['SELECTED','PLAN_PENDING','PLAN_UNKNOWN'].includes(s)?3:r.execution||failed?4:r.geon?3:1;
  const phaseStatus=({ANALYSIS_PENDING:'요구사항 분석 중',RECOMMENDATION_PENDING:'인프라 추천 응답 확인 중',ANALYZED:'인프라 후보 준비',SELECTED:'선택한 인프라로 계획 준비',PLAN_PENDING:'배포 판단 중',PLAN_UNKNOWN:'배포 판단 응답 확인 필요',DEPLOY_APPROVED:'배포 계획 검증 통과',REJECTED:'조건 미충족',RETRY_REQUIRED:'배포 조건 재검토 요청',MOCK_RUNNING:'Mock 실행 영수증 RUNNING',FEEDBACK_RECORDED:'운영 피드백 판단 기록',STOPPED:'분석 단계 중단'})[s];
  const status=failed?cloudPreparationLabel(r):
    realExecution&&(s==='DEPLOY_APPROVED'||r.execution.status!=='RUNNING')?executionStatus:
    phaseStatus??executionStatus??'요구사항을 입력해 시작하세요';
  return {step:r?.input_dirty?1:step,needsAnalysis:!!r?.input_dirty,status:r?.input_dirty?'입력 조건이 변경되었습니다. 다시 분석해 주세요.':status,analysis:({structured:'구조화 입력',local_rule:'로컬 규칙',qwen:'Qwen 분석',llm:'LLM 분석'})[r?.analysis?.mode]??'분석 전',observation:failed?'어플리케이션 실측 전':r?.execution?.mode==='cloud_vm'?'VM 실행':r?.observation_origin==='observed'?'관측 입력':'데모 입력',execution:failed?'어플리케이션 배포 미완료':realExecution?(r.execution.mode==='cloud_vm'?'실제 VM 어플리케이션 · ':'실제 로컬 어플리케이션 · ')+r.execution.status:r?.execution?.mode==='mock'?'Mock 실행':'실행 전'};
}

const workspaceSteps={
  application:{title:'01 요구사항 분석',input:'자연어 요청 또는 직접 지정한 자원·성능 조건',process:'요구사항 구조화와 자원 후보 평가',output:'분석된 요구사항과 인프라 추천 결과'},
  infrastructure:{title:'02 자원 추천과 선택',input:'분석된 요구사항과 자원 후보',process:'조건 충족 여부·추천 순위·근거 확인',output:'배포 판단에 사용할 선택 자원'},
  planning:{title:'03 배포 판단과 검증',input:'요구사항과 선택한 자원',process:'배포 가능성 판단 후 별도 조건 검증',output:'승인된 최초 배포 명세'},
  deployment:{title:'04 실제 실행',input:'승인된 명세와 실행할 응용',process:'별도 실행 요청을 실행 모듈에 전달',output:'실제 배포 상태·실행 수·로그'},
  operation:{title:'05 관측과 운영 판단',input:'선택한 실행의 실측값과 실행 상태',process:'유지·확장·축소 판단과 변경 조건 검증',output:'운영 판단과 승인 목표 · 변경은 별도 적용'},
};
export function resourceAvailabilityModel(r){
 const candidates=r?.candidates?.all??[];
 const excluded=r?.candidates?.excluded??(r?.resource_recommendation?.excluded_resources??[]).filter(e=>e.resource_id==='khu-local-pc').map(e=>({candidate_id:e.resource_id,failures:e.failures??[]}));
 const issues=excluded.flatMap(e=>(e.failures??[]).map(f=>{
  const [label,unit]=f.field?.includes('memory')?['메모리','MiB']:f.field?.includes('storage')?['디스크','GiB']:f.field?.includes('cpu')?['CPU','코어']:['자원 조건',''];
  const format=value=>Number.isFinite(value)?number(value):String(value??'확인 필요');
  return `${label}: 필요 ${format(f.required)} ${unit} / 현재 가용 ${format(f.actual)} ${unit}`.trim();
 }));
 return {discovered:r?.candidates?.discovered_count??new Set([...candidates.map(c=>c.candidate_id),...excluded.map(c=>c.candidate_id)]).size,selectable:candidates.filter(c=>c.selectable).length,issues,canRefresh:!!(r?.input?.execution_target==='local'&&r.application_profile&&['ANALYZED','SELECTED'].includes(r.state)&&!r.geon&&!r.execution&&!r.appdeploy&&!r.cloud_job_requested)};
}
export function workspaceGuidanceModel(r,{page='application',target='local',inputDirty=false,operation=null,elapsedSeconds=0,monitorReceipt=null}={}){
  const io=workspaceSteps[page];
  if(!io)return {visible:false};
  const savedMonitoring=page==='operation'&&monitorReceipt&&monitorReceipt.flow_id!==r?.flow_id;
  const route=(savedMonitoring?monitorReceipt:r)?.input?.execution_target??target;
  const v={visible:true,title:io.title,io,route:route==='local'?'이 PC 실제 실행':route==='cloud'?'AWS VM 실제 실행':'과거 VM 비교 · 모의 실행',status:'요구사항 입력 대기',detail:'입력한 조건을 분석하면 자원 추천과 배포 판단으로 이어집니다.',next:page==='application'?null:{href:'#application',label:'요구사항 입력으로 이동'}};
  const set=(status,detail,next=null)=>({...v,status,detail,next});
  if(operation==='analysis')return set(`요구사항 분석·추천 처리 중 · ${Math.max(0,Math.floor(elapsedSeconds))}초`,'자연어 분석은 모델 준비와 응답 생성에 시간이 걸릴 수 있습니다. 처리 중에는 같은 요청을 다시 보내지 않습니다.');
  if(operation)return set('요청 처리 중',({plan:'배포 판단과 조건 검증 결과를 기다리고 있습니다.',execution:'실행 요청의 처리 결과를 기다리고 있습니다. 상태가 확인되기 전에는 다시 요청하지 않습니다.',feedback:'실측값에 대한 운영 판단과 검증 결과를 기다리고 있습니다.'})[operation]??'현재 요청의 결과를 확인하고 있습니다.');
  if(inputDirty)return set('조건 변경 · 다시 분석 필요','화면의 입력이 저장된 판단과 달라졌습니다. 다시 분석한 뒤 새 결과로 진행하세요.',{href:'#application',label:'변경한 조건 분석하기'});
  if(savedMonitoring)return set('다른 실행의 저장 기록 조회','선택한 과거 실행의 관측과 판단을 보고 있습니다. 현재 실험의 변경 적용은 이 기록으로 실행하지 않습니다.');
  if(!r)return v;
  const runtimeIssue=route==='cloud'&&!r.cloud_run_id&&cloudRuntimeIssue(r.application_profile?.requirements,r.candidates?.all?.find(c=>c.candidate_id===r.selected_candidate_id));
  if(runtimeIssue)return set('실행 대상 다시 선택',runtimeIssue.message,{href:'#infrastructure',label:'실행 가능한 자원 선택'});
  if(['PLAN_PENDING','PLAN_UNKNOWN'].includes(r.state))return set('기존 배포 판단 확인 필요','같은 판단을 다시 요청하지 말고 기존 판단 결과 조회로 응답을 확인하세요.',{href:'#planning',label:'기존 판단 결과 확인'});
  if(['PENDING','UNKNOWN'].includes(r.feedback_state))return set('기존 운영 판단 확인 필요','운영 판단을 다시 요청하지 말고 기존 피드백 결과 조회로 확인하세요.',{href:'#feedback-reconcile',label:'기존 운영 판단 확인'});
  if(['APPLYING','STOPPING'].includes(r.replica_group?.operation_state))return set(r.replica_group.operation_state==='APPLYING'?'복제본 변경 중':'응용 종료 중','실행 모듈이 변경을 처리하고 있습니다. 상태가 확인될 때까지 새 작업을 요청하지 않습니다.');
  if(r.replica_group?.operation_state==='PENDING')return set('복제본 변경 결과 확인 중','기존 변경을 다시 요청하지 말고 상태·로그 조회로 결과를 확인하세요.',{href:'#deployment',label:'기존 변경 상태 확인'});
  if(['PENDING','UNKNOWN'].includes(r.execution?.status)||['PENDING','UNKNOWN'].includes(r.appdeploy?.status)||r.replica_group?.operation_state==='UNKNOWN')return set('실제 실행 상태 확인 필요','실행을 다시 요청하지 말고 상태·로그 조회로 기존 요청의 처리 결과를 확인하세요.',{href:'#deployment',label:'실행 상태 조회로 이동'});
  if(cloudPreparationFailed(r))return set(r.cloud.failure?.code==='cloud_cleanup_in_progress'?'종료 요청으로 응용 준비 중단':'VM 응용 준비 실패',r.cloud.failure?.code==='cloud_cleanup_in_progress'?'분석과 계획은 남아 있습니다. 배포 실행에서 현재 비용 검토부터 다시 진행하세요.':'실행 상세에서 실패 단계와 VM 종료·정리 확인 결과를 확인하세요.',{href:'#deployment',label:'실행·정리 상태 확인'});
  if(route==='cloud'&&r.cloud){
    const state=r.cloud.state;
    const preparing={CREATE_REQUESTED:['VM 생성 중','VM 생성 응답을 기다리고 있습니다. 새 생성 요청을 보내지 않습니다.'],CREATED:['VM 확인 · 응용 준비 중','생성된 VM에 모델과 응용을 준비하고 있습니다.'],PREPARING:['모델·응용 준비 중','VM 준비와 응용 실행은 별도 상태입니다. 실제 실행 응답을 확인할 때까지 기다리세요.'],CREATE_UNKNOWN:['기존 VM 생성 결과 확인 필요','VM 생성을 다시 요청하지 말고 기존 VM 상태를 조회하세요.'],CLEANUP_PENDING:['VM 종료·정리 확인 중','실제 VM 종료와 부속 자원 정리가 확인되기 전에는 새 실험을 시작하지 않습니다.']};
    if(preparing[state])return set(...preparing[state],{href:'#cloud-panel',label:'VM 실행·정리 상태 확인'});
    if(state==='RUNNING'&&r.execution?.status==='STOPPED')return set('응용 중지 · VM 종료 확인 필요','응용 중지만으로 VM은 종료되지 않습니다. VM 종료와 연결 자원 정리를 확인하세요.',{href:'#cloud-panel',label:'VM 종료·정리 확인'});
    if(state==='CLEANED'&&r.cloud.cleanup?.confirmed===true)return set('VM 종료·정리 확인 완료','저장된 VM 종료와 부속 자원 정리 결과입니다. 추론과 관측값은 해당 실행의 기록으로 보관됩니다.',{href:'#events',label:'실행 기록 확인'});
    if(state==='RUNNING'&&!r.execution)return set('VM 준비 확인 · 응용 실행 상태 확인 필요','VM 준비 상태만으로 응용 실행을 완료로 표시하지 않습니다. 실제 배포 상태를 조회하세요.',{href:'#deployment',label:'응용 실행 상태 확인'});
  }
  if(r.execution?.status?.endsWith('_FAILED')||r.execution?.status==='FAILED')return set('응용 실행 실패 · 상태 확인 필요','실행 상태와 로그에서 실패 결과를 확인하세요. 새 실행은 기존 작업의 종료를 확인한 뒤 진행합니다.',{href:'#deployment',label:'실행 상태·로그 확인'});
  if(['STOPPED','COMPLETED'].includes(r.execution?.status)||r.replica_group?.operation_state==='STOPPED'){
    const actual=r.replica_group?r.replica_group.actual_replicas:r.execution.actual_replicas;
    const count=Number.isInteger(actual)?`마지막 확인된 실제 ${actual}개입니다.`:'실제 실행 수는 확인되지 않았습니다. 상태 조회로 확인하세요.';
    return set('저장된 실행 · 종료됨',`${count} 승인 목표와 실측값은 종료 전 기록입니다. 다시 실행하려면 새 실험을 시작하세요.`,{href:'#events',label:'이 실험의 실행 기록 보기'});
  }
  if(['ANALYSIS_PENDING','RECOMMENDATION_PENDING'].includes(r.state))return set('기존 분석 응답 확인 필요','저장된 분석이 처리 중 상태입니다. 실행 기록에서 결과를 확인하세요.',{href:'#events',label:'분석 요청 기록 확인'});
  if(r.state==='STOPPED'&&r.error)return set('분석·추천 단계 중단','분석 또는 추천 응답을 받지 못했습니다. 입력과 로컬 모듈 상태를 확인한 뒤 새로 분석하세요.',{href:'#application',label:'입력 조건 확인'});
  if(!r.application_profile)return v;
  if(!r.selected_candidate_id){
    const candidates=r.candidates?.all??[];
    if(!candidates.some(c=>c.selectable)){
      const availability=resourceAvailabilityModel(r);
      return set('실행 가능한 후보 확인 필요',availability.issues.length?availability.issues.join(' · ')+'. 자원 상태가 바뀌면 같은 요구사항으로 자원을 다시 조회하세요.':'요구사항을 충족하는 선택 가능 자원이 없습니다. 제외 사유를 확인하고 입력 조건을 조정하세요.',availability.canRefresh?{href:'#resources-refresh',label:'같은 요구사항으로 자원 다시 조회'}:{href:'#application',label:'요구사항 확인'});
    }
    return set('추천 결과 확인 · 자원 선택 대기','후보의 적합성과 추천 근거를 확인하고 실행할 자원을 선택하세요.',{href:'#infrastructure',label:'추천 자원 확인'});
  }
  if(!r.geon)return set('자원 선택 완료 · 배포 판단 대기','선택한 자원에 배포할 수 있는지 판단하고 조건을 검증하세요.',{href:'#planning',label:'배포 판단으로 이동'});
  if(r.geon.decision?.action!=='DEPLOY'||r.geon.guard?.status!=='APPROVED')return set('배포 조건 확인 필요','배포 판단 또는 검증이 통과하지 않았습니다. 판단 근거를 확인하고 조건을 수정하세요.',{href:'#planning',label:'판단·검증 결과 확인'});
  if(!r.execution){
    const detail=route==='cloud'?'승인된 계획으로 비용 검토와 별도 승인을 거쳐 VM 실행을 요청합니다. 계획 승인만으로 VM이 생성되지는 않습니다.':route==='historical'?'승인된 계획의 모의 실행을 별도로 기록합니다. 실제 VM은 생성하지 않습니다.':'승인된 계획이 준비됐습니다. 별도 배포 버튼을 눌러야 실제 응용이 실행됩니다.';
    return set('계획 승인 · 실행 전',detail,{href:'#deployment',label:route==='historical'?'모의 실행 단계로 이동':'별도 실행 요청으로 이동'});
  }
  const replica=replicaView(r);
  if(replica.canApply)return set('운영 변경 승인 · 변경 적용 대기',`마지막 확인된 실제 ${replica.actual}개, 승인 목표 ${replica.desired}개입니다. 승인된 변경 적용을 눌러야 프로세스 수가 바뀝니다. VM 대수는 이 버튼으로 변경하지 않습니다.`,{href:'#replica-panel',label:'승인된 변경 적용으로 이동'});
  if(replica.pendingRevision)return set('이전 변경의 적용 상태 확인 필요','명세와 마지막 확인된 실행 버전이 다릅니다. 새 운영 판단 전에 기존 변경의 검증 결과와 실제 적용 상태를 확인하세요.',{href:'#deployment',label:'기존 변경 상태 확인'});
  if(measurementNeedsRefresh(r))return set('변경 적용 완료 · 재측정 대기',`Revision ${r.replica_group.applied_revision} 구성을 적용해 실제 ${r.replica_group.actual_replicas}개가 실행 중입니다. 이전 구성의 실측과 구분하여 다시 측정하고 다음 운영 판단으로 이어가세요.`,{href:'#app-measure',label:'변경된 실행을 다시 측정'});
  if(r.feedback){
    const action=judgementLabel(r.feedback.geon?.scaling_decision?.action??'판단 확인 필요');
    return set(`운영 판단 기록 · ${action}`,'현재 판단과 실제 적용 상태를 확인하세요. 추가 관측을 진행하거나 배포 화면에서 응용을 종료할 수 있습니다.',{href:page==='operation'?'#deployment':'#operation',label:page==='operation'?'실행 상태·종료로 이동':'관측과 운영 판단 보기'});
  }
  return set(route==='historical'?'모의 실행 기록 생성':'응용 실행 중 · 실측 대기',route==='historical'?'예시 관측을 입력해 판단을 확인합니다. 실제 성능 측정 결과와 구분하세요.':'실제 추론 측정 또는 Prometheus 관측값으로 운영 판단을 요청하세요.',{href:'#operation',label:'관측과 운영 판단으로 이동'});
}

export function operationErrorModel(code,{operation,receipt}={}){
  if(code==='recording_scope_conflict')return {title:'기존 AWS 실행 상태 확인 필요',detail:'준비 중인 VM이나 여러 VM의 실험은 일괄 종료하지 않습니다. 배포 실행에서 현재 실행을 확인하고 정리한 뒤 시연 준비를 다시 누르세요.',href:'#deployment',label:'AWS 실행 상태 확인'};
  if(code==='recording_reset_timeout')return {title:'기존 VM 종료 확인 대기',detail:'종료 요청을 보냈지만 VM과 연결 자원의 정리 완료를 아직 확인하지 못했습니다. 새 VM은 만들지 않았습니다. 배포 실행에서 종료 상태를 확인하세요.',href:'#deployment',label:'종료·정리 상태 확인'};
  if(code==='replica_capacity_insufficient')return {title:'확장할 자원 부족',detail:'추가 복제본에 필요한 CPU·메모리·디스크가 등록된 자원의 남은 할당량을 초과했습니다. 기존 복제본은 유지됩니다. 상태를 확인한 뒤 자원 여유를 확보하거나 더 작은 요구사항으로 새 실험을 시작하세요.',href:'#deployment',label:'유지 중인 실행 상태 확인'};
  if(operation==='resources')return {title:'자원 재조회 결과 확인 필요',detail:'기존 요구사항과 분석 결과는 보관했습니다. 자원 상태와 입력 조건을 확인한 뒤 다시 조회하세요.',href:'#infrastructure',label:'자원 상태 확인'};
  if(['deployment_unknown','feedback_unknown','reasoning_result_unknown'].includes(code))return {title:'기존 요청의 처리 결과 확인 필요',detail:'같은 작업을 다시 요청하지 말고 기존 결과를 조회하세요. 응답이 없어도 실행 모듈이 요청을 수락했을 수 있습니다.',href:code==='deployment_unknown'?'#deployment':code==='feedback_unknown'?'#feedback-reconcile':'#reasoning-refresh',label:'기존 요청 결과 확인'};
  if(['stale_selection','agent_selection_stale'].includes(code))return {title:'선택 정보가 변경됨',detail:'현재 자원 선택과 에이전트 정보를 다시 확인하세요. 이전 선택으로 만든 계획은 새 실행에 사용하지 않습니다.',href:'#infrastructure',label:'현재 자원 선택 확인'};
  if(operation==='analysis')return {title:receipt?.application_profile?'인프라 추천 응답 확인 실패':'요구사항 분석 응답 확인 실패',detail:'입력 내용과 연결된 로컬 모듈 상태를 확인하세요. 자연어 분석을 다시 시도하거나 직접 조건 지정으로 분석 경로를 확인할 수 있습니다.',href:'#application',label:'입력 조건 확인'};
  if(operation==='execution'||['local_worker_busy','stop_local_worker_first'].includes(code))return {title:'실행 상태 확인 필요',detail:'현재 배포 상태와 로그를 먼저 조회하세요. 기존 실행을 확인한 뒤 종료하거나 다음 실험을 진행하세요.',href:'#deployment',label:'실행 상태·로그 확인'};
  return {title:'작업 결과 확인 필요',detail:'입력 조건과 저장된 실행 기록을 확인하세요. 같은 요청을 자동으로 반복하지 않습니다.',href:'#events',label:'실행 기록 확인'};
}

const fields = {
  operation: ['계획 동작'], target_runtime: ['실행 환경'],
  'application.app_id': ['어플리케이션 이름'], 'application.app_version': ['어플리케이션 버전'],
  'application.artifact.type': ['어플리케이션 패키지 형식'], 'application.artifact.uri': ['어플리케이션 패키지 위치'],
  'desired_infrastructure.node_count': ['VM 노드 수', '개'],
  'desired_infrastructure.cpu_cores_per_node': ['노드당 CPU', 'vCPU'],
  'desired_infrastructure.memory_mib_per_node': ['노드당 메모리', 'MiB'],
  'desired_infrastructure.storage_gib_per_node': ['노드당 저장소', 'GiB'],
  'desired_infrastructure.accelerator.type': ['가속기 종류'],
  'desired_infrastructure.accelerator.count': ['노드당 가속기', '개'],
  'desired_infrastructure.accelerator.memory_mib_min_per_device': ['가속기당 최소 메모리', 'MiB'],
  'desired_infrastructure.isolation': ['어플리케이션 격리 방식'],
  'desired_infrastructure.placement.anti_affinity': ['분산 배치'],
  'inference_configuration.runtime_engine': ['추론 엔진'],
  'inference_configuration.precision': ['연산 정밀도'],
  'inference_configuration.replicas': ['어플리케이션 복제본', '개'],
  'inference_configuration.max_batch_size': ['최대 배치 크기'],
  'inference_configuration.max_concurrency': ['최대 동시 실행'],
  'inference_configuration.tensor_parallel_size': ['텐서 병렬 크기'],
  'runtime.restart_policy': ['재시작 정책'], 'runtime.args': ['실행 인자'],
  policy_hints: ['배치 조건']
};
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function leaves(value, prefix = '', result = new Map()) {
  if (object(value)) {
    for (const [key, item] of Object.entries(value)) leaves(item, prefix ? prefix + '.' + key : key, result);
  } else if (prefix) result.set(prefix, value);
  return result;
}
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (object(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])]));
  return value;
}
function valueLabel(value, path) {
  if (value === undefined) return '미지정';
  if (value === null) return 'null';
  if (typeof value === 'boolean') return value ? '사용' : '미사용';
  if (typeof value === 'number') return new Intl.NumberFormat('ko-KR').format(value) + (fields[path]?.[1] ? (fields[path][1] === '개' ? '' : ' ') + fields[path][1] : '');
  if (typeof value === 'object') return JSON.stringify(value);
  const labels = path === 'operation' ? {DEPLOY:'배포', SCALE_OUT:'확장', SCALE_IN:'축소', KEEP:'유지', UPDATE:'변경'} :
    path === 'runtime.restart_policy' ? {ON_FAILURE:'실패 시 재시작', ALWAYS:'항상 재시작', NEVER:'재시작 안 함'} :
    path === 'desired_infrastructure.isolation' ? {ONE_MAJOR_APP_PER_VM:'VM당 주요 어플리케이션 1개'} : null;
  return labels && Object.hasOwn(labels, value) ? labels[value] : String(value);
}
function differences(before, after) {
  const left = leaves(before), right = leaves(after);
  return [...new Set([...left.keys(), ...right.keys()])].sort()
    .filter(path => path !== 'decision_id' && !path.startsWith('metadata.') &&
      JSON.stringify(stable(left.get(path))) !== JSON.stringify(stable(right.get(path))))
    .map(path => ({path, label:fields[path]?.[0] ?? path, before:left.get(path), after:right.get(path),
      beforeLabel:valueLabel(left.get(path), path), afterLabel:valueLabel(right.get(path), path)}));
}
function selectionGroup(record, branch, isCurrent) {
  const expected = `${record.flow_id}-s${branch.selection_version}`, items = new Map();
  const snapshots = [branch.geon, ...(branch.feedback_history ?? []).map(item => item.geon), branch.feedback?.geon];
  for (const snapshot of snapshots) {
    if (snapshot?.correlation_id !== expected || snapshot.trace_id !== record.trace_id ||
      snapshot.decision?.selected_candidate_id !== branch.selected_candidate_id) continue;
    const list = snapshot.manifest_revisions ?? [];
    const latest = Math.max(0, ...list.map(item => item.revision));
    for (const raw of list) {
      if (!Number.isInteger(raw.revision) || raw.revision < 1 || !object(raw.desired_deployment_spec) || items.has(raw.revision)) continue;
      const initial = raw.phase === 'INITIAL';
      const reason = initial ? snapshot.decision?.reason : raw.revision === latest &&
        snapshot.scaling_decision?.action === raw.trigger_action ? snapshot.scaling_decision.reason : '';
      items.set(raw.revision, {revision:raw.revision, phase:raw.phase, action:raw.trigger_action,
        at:raw.created_at, replicas:raw.desired_deployment_spec.inference_configuration?.replicas,
        reason:reason ?? '', raw});
    }
  }
  return {selectionVersion:branch.selection_version, candidateId:branch.selected_candidate_id,
    candidateName:record.candidates?.all?.find(item => item.candidate_id === branch.selected_candidate_id)?.name ?? branch.selected_candidate_id ?? '자원 선택 전',
    isCurrent, revisions:[...items.values()].sort((a,b) => a.revision-b.revision)};
}

// Revision numbers are scoped to one GEON Flow and one resource selection.
export function revisionComparisonModel(record, options = {}) {
  if (!record) return {groups:[], group:null, from:null, to:null, changes:[], canCompare:false};
  const current = selectionGroup(record, record, true);
  const groups = [current, ...(record.selection_history ?? [])
    .filter(branch => branch.selection_version !== record.selection_version)
    .map(branch => selectionGroup(record, branch, false)).filter(group => group.revisions.length)
    .sort((a,b) => b.selectionVersion-a.selectionVersion)];
  const group = groups.find(item => item.selectionVersion === Number(options.selectionVersion)) ?? current;
  const from = group.revisions.find(item => item.revision === Number(options.fromRevision)) ?? group.revisions[0] ?? null;
  const to = group.revisions.length > 1 ? group.revisions.find(item => item.revision === Number(options.toRevision)) ?? group.revisions.at(-1) : null;
  return {groups, group, from, to, canCompare:!!from && !!to,
    changes:from && to ? differences(from.raw.desired_deployment_spec, to.raw.desired_deployment_spec) : []};
}
