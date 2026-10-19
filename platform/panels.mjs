import {judgementLabel,reasonLabel} from './view-model.mjs';
import {measurementNeedsRefresh} from './replicas-ui.mjs';
import {replicaView} from './replicas-ui.mjs';
import {cloudPreparationFailed,cloudPreparationLabel} from './cloud-state.mjs';
const number=value=>new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(value);
const display=(value,unit)=>Number.isFinite(value)?number(value)+unit:'—';
const observationSource=observation=>observation?.source==='prometheus'?'Prometheus 수집':['local_probe','cloud_probe'].includes(observation?.source)?'실제 어플리케이션 실측':observation?.source==='demo_input'?'데모 관측 입력':observation?'기록된 관측':'실측 전';

// Summarize this receipt only. Separate research reports never determine its outcome.
export function experimentResultModel(receipt,{inputDirty=false,operation=null}={}){
 const r=operation==='analysis'?null:receipt;
 const stages=[['요구사항','#application'],['자원 추천과 선택','#infrastructure'],['배포 계획','#planning'],['배포 실행','#deployment'],['모니터링과 운영 판단','#operation']].map(([label,href])=>({label,href,state:'waiting',status:'대기',detail:'아직 기록된 결과가 없습니다.'}));
 const v={empty:!r,flowId:r?.flow_id??null,app:r?.input?.application?.app_id??'실험 시작 전',route:r?.input?.execution_target==='local'?'이 PC 실제 실행':r?.input?.execution_target==='cloud'?'AWS VM 실행':'과거 VM 비교 · 모의 실행',status:operation==='analysis'?'새 실험 분석 중':'실험 결과 대기',detail:'요구사항을 분석하면 이번 실험의 단계별 결과가 표시됩니다.',actualReplicas:null,desiredReplicas:null,appliedRevision:null,replicaHistory:'',stages};
 const set=(i,state,status,detail)=>Object.assign(stages[i],{state,status,detail});
 if(!r){if(operation==='analysis')set(0,'active','분석 중','새 분석 응답을 기다리고 있습니다.');return v;}
 const profile=r.application_profile,req=profile?.requirements,selected=r.candidates?.all?.find(c=>c.candidate_id===r.selected_candidate_id),plan=r.geon;
 const planMismatch=!!plan&&((plan.profile_id&&plan.profile_id!==profile?.profile_id)||(plan.trace_id&&plan.trace_id!==r.trace_id)||(plan.correlation_id&&plan.correlation_id!==`${r.flow_id}-s${r.selection_version}`)||(plan.decision?.selected_candidate_id&&plan.decision.selected_candidate_id!==r.selected_candidate_id));
 const approved=plan?.decision?.action==='DEPLOY'&&plan.guard?.status==='APPROVED'&&!planMismatch;
 const x=r.execution,g=r.replica_group,replicas=replicaView(r),real=['local','cloud'].includes(r.input?.execution_target)&&['local_process','cloud_vm'].includes(x?.mode),mock=x?.mode==='mock';
 const known=real&&['RUNNING','STOPPED','COMPLETED'].includes(x?.status)&&(!g||['IDLE','STOPPED'].includes(g.operation_state));
 const count=g?.actual_replicas??x?.actual_replicas;
 v.actualReplicas=known&&Number.isInteger(count)&&count>=0?count:null;
 v.appliedRevision=Number.isInteger(g?.applied_revision??x?.applied_revision)?g?.applied_revision??x.applied_revision:null;
 v.desiredReplicas=replicas.desired??null;
 const events=(r.history??[]).filter(e=>['group_running','revision_applied','group_stopped'].includes(e.event)&&(!g||e.group_id===g.group_id)&&(!e.selection_version||e.selection_version===r.selection_version)&&Number.isInteger(e.actual_replicas));
 v.replicaHistory=events.map(e=>e.actual_replicas).join(' → ');
 if(profile)set(0,'recorded','분석 결과 기록',`${r.analysis?.actual_model??(r.input?.analysis_input?.input_type==='natural_language'?'자연어 분석':'직접 지정 조건')} · CPU ${display(req?.compute?.cpu_cores_min,'코어')} · 메모리 ${display(req?.compute?.memory_mib_min,' MiB')}`);
 else if(r.state==='STOPPED')set(0,'attention','분석 중단','분석 응답과 입력 조건을 확인하세요.');
 if(r.resource_recommendation){const eligible=(r.candidates?.all??[]).filter(c=>c.selectable).length;set(1,selected?.selectable?'recorded':r.selected_candidate_id?'attention':eligible?'waiting':'attention',selected?'자원 선택 기록':eligible?'자원 선택 대기':'후보 확인 필요',selected?`${selected.name??selected.display_name??selected.candidate_id} · ${selected.selectable?'자원 조건 충족':'조건 충족 여부 확인 필요'}`:`선택 가능한 후보 ${eligible}개 · ${r.selected_candidate_id?'선택 자원 정보 확인 필요':'아직 선택한 자원이 없습니다.'}`);}
 if(plan)set(2,approved?'recorded':'attention',planMismatch?'연결 확인 필요':approved?'계획 승인':judgementLabel(plan.decision?.action??plan.guard?.status??'판단 확인 필요'),planMismatch?'계획의 실험·요구사항·선택 자원 연결이 일치하지 않습니다.':approved?'배포 가능 · Guard 검증 통과 · 실제 실행은 별도 요청':`Guard ${judgementLabel(plan.guard?.status??'확인 필요')} · 판단과 검증 결과를 확인하세요.`);
 else if(['PLAN_PENDING','PLAN_UNKNOWN'].includes(r.state))set(2,'attention','기존 판단 확인 필요','기존 요청의 응답을 확인하세요.');
 if(mock)set(3,'recorded','모의 실행 기록','Mock 실행 기록이며 실제 앱 배포가 아닙니다.');
 else if(real)set(3,x.status==='RUNNING'||x.status==='STOPPED'&&v.actualReplicas===0?'recorded':'attention',({RUNNING:'실행 확인',STOPPED:v.actualReplicas===0?'종료 확인':'종료 상태 확인 필요',UNKNOWN:'상태 확인 필요'})[x.status]??'실행 상태 확인 필요',`${x.status} · 실제 복제본 ${v.actualReplicas===null?'수 확인 필요':v.actualReplicas+'개'}${v.appliedRevision?' · 적용 Revision '+v.appliedRevision:''}`);
 else if(r.appdeploy)set(3,['UNKNOWN','PENDING'].includes(r.appdeploy.status)?'attention':'waiting',r.appdeploy.status==='PREPARED'?'등록 명세 준비':'배포 응답 확인 필요','실제 실행이 확인되기 전입니다.');
 const o=r.measurement??r.feedback?.observation,foreign=!!o&&((o.flow_id&&o.flow_id!==r.flow_id)||(g&&o.group_id&&o.group_id!==g.group_id)),s=r.feedback?.geon?.scaling_decision,guard=r.feedback?.geon?.operation_agent_execution?.scaling_guard?.status;
 const stale=measurementNeedsRefresh(r),stopped=x?.status==='STOPPED';
 if(foreign)set(4,'attention','관측 연결 확인 필요','다른 실험 또는 실행 그룹의 관측을 이번 결과로 사용할 수 없습니다.');
 else if(o){const origin=observationSource(o),metric=o.source==='prometheus'?'서버 처리 p95':'HTTP p95';set(4,stale||['PENDING','UNKNOWN'].includes(r.feedback_state)||s&&guard!=='APPROVED'?'attention':'recorded',stale?'변경 전 관측':s?`운영 판단 ${judgementLabel(s.action)}`:'관측 기록',`${stopped?'종료 전 기록 · ':stale?'변경 전 · 재측정 필요 · ':''}${origin} · ${metric} ${display(o.metrics?.inference?.latency_p95_ms,' ms')}${s?' · Guard '+judgementLabel(guard??'확인 필요'):''}`);}
 else if(s)set(4,'attention','관측 원본 확인 필요','판단에 사용한 관측 기록을 확인하세요.');
 if(cloudPreparationFailed(r)){v.status=cloudPreparationLabel(r);v.detail='응용 배포가 완료되지 않았습니다. VM 정리 결과와 실패 단계를 구분해서 확인하세요.';set(3,'attention',r.cloud.failure?.code==='cloud_cleanup_in_progress'?'종료 요청으로 준비 중단':'배포 준비 실패',v.detail);}
 else if(planMismatch||foreign){v.status='이번 실험의 연결 확인 필요';v.detail='서로 다른 실험의 기록을 한 결과로 표시하지 않습니다.';}
 else if(r.state==='STOPPED'&&!x){v.status='분석 또는 추천 중단';v.detail='기록이 남은 단계부터 원인을 확인하세요.';}
 else if(['UNKNOWN','PENDING'].includes(x?.status)||['UNKNOWN','PENDING','APPLYING','STOPPING'].includes(g?.operation_state)||['PENDING','UNKNOWN'].includes(r.feedback_state)){v.status='처리 결과 확인 필요';v.detail='기존 실행 또는 판단의 상태를 먼저 조회하세요.';}
 else if(real&&/FAILED/.test(x.status)){v.status='응용 실행 실패';v.detail='배포 실행에서 실패 단계와 현재 상태를 확인하세요.';}
 else if(stopped&&real){v.status=v.actualReplicas===0?'응용 종료 확인':'종료 상태 확인 필요';v.detail='측정값과 운영 판단은 종료 전 기록입니다. 자원 정리 여부는 실행 경로에서 확인하세요.';}
 else if(mock){v.status='모의 실험 기록';v.detail='실제 VM이나 모델을 실행한 결과와 구분합니다.';}
 else if(replicas.pendingRevision){v.status=replicas.canApply?'운영 변경 승인 · 적용 대기':'운영 변경 검증 확인 필요';v.detail=`현재 적용 Revision ${v.appliedRevision??'확인 필요'}와 승인 목표를 구분합니다. 실제 변경은 별도 적용 요청을 거칩니다.`;}
 else if(stale){v.status='변경 적용 완료 · 재측정 대기';v.detail='새 구성에서 다시 측정해야 다음 운영 판단으로 이어집니다.';}
 else if(real&&x.status==='RUNNING'){v.status=s?'응용 실행 중 · 운영 판단 기록':'응용 실행 중 · 실측 대기';v.detail='현재 앱의 관측과 판단을 확인하고, 승인된 변경만 별도로 적용하세요.';}
 else if(approved){v.status='계획 승인 · 실제 실행 전';v.detail='계획이 승인됐으며 아직 실제 앱을 실행하지 않았습니다.';}
 else if(plan){v.status='배포 판단 확인 필요';v.detail='조건 검증을 통과한 계획이 있어야 실제 배포할 수 있습니다.';}
 else if(profile){v.status=r.selected_candidate_id?'자원 선택 완료 · 계획 대기':'자원 추천과 선택 대기';v.detail='앞 단계에서 이어서 진행하면 같은 실험의 결과가 갱신됩니다.';}
 if(inputDirty){v.status='조건 변경 · 기존 실험 결과';v.detail='아래는 저장된 조건의 결과입니다. 바꾼 입력은 다시 분석해야 반영됩니다.';}
 if(operation&&operation!=='analysis'){v.status='이번 실험 요청 처리 중';v.detail='아래는 마지막으로 확인된 기록입니다. 응답 후 결과를 갱신합니다.';}
 return v;
}
export function campaignPanelModel(report){
  if(report?.schema_version!=='khu-campaign-evidence/v1')throw Error('invalid_evidence_schema');
  const groups=new Map();
  for(const run of report.runs??[]){
    const group=groups.get(run.spec_id)??{spec:run.spec_id.split('+').at(-1),device:run.device==='gpu'?'GPU':'CPU',runCount:0,executionCount:0,rawBytes:0};
    group.runCount++;group.executionCount+=run.execution_count;group.rawBytes+=run.canonical_raw_bytes;groups.set(run.spec_id,group);
  }
  return {runCount:report.verified_run_count,rawMiB:Math.round(report.totals.canonical_raw_bytes/1048576*100)/100,executionCount:report.totals.execution_count,modelCount:report.totals.unique_model_count,traceRecordCount:report.totals.trace_record_count,vms:[...groups.values()].sort((a,b)=>a.spec.localeCompare(b.spec))};
}
export function monitoringRecords(records){
  const time=r=>Date.parse(r.measurement?.ended_at??r.feedback?.observation?.ended_at??r.created_at)||0;
  return records.filter(r=>r.measurement?.metrics||r.feedback?.observation?.metrics).slice().sort((a,b)=>time(b)-time(a));
}
function monitoringPreparation(r){
 if(r?.input?.execution_target!=='cloud')return null;
 const next='#deployment',state=r.cloud?.state;
 if(cloudPreparationFailed(r))return {status:cloudPreparationLabel(r),detail:r.cloud.failure?.code==='cloud_cleanup_in_progress'?'VM 종료 요청으로 응용 준비가 중단됐습니다. 배포 실행에서 비용 검토부터 다시 진행할 수 있습니다.':'응용 배포 준비를 완료하지 못했습니다. 배포 실행에서 실패 단계와 VM 정리 결과를 확인하세요.',next};
 if(r.execution?.status==='RUNNING'||r.execution?.status==='STOPPED')return null;
 if(state==='CLEANUP_PENDING')return {status:'VM 종료·정리 확인 중',detail:'VM과 연결 자원을 정리 중입니다. 배포 실행에서 실제 종료 여부를 확인하세요.',next};
 if(state==='CREATE_UNKNOWN')return {status:'기존 VM 생성 결과 확인 중',detail:'기존 요청의 결과를 확인 중입니다. 실행 버튼을 다시 누르지 말고 배포 실행에서 상태를 확인하세요.',next};
 if(state==='CREATED'&&r.cloud.ssm?.online!==true)return {status:'VM 부팅·관리 연결 준비 중',detail:'VM은 생성됐지만 관리 연결과 응용 준비가 끝나지 않았습니다. 실행 버튼을 다시 누르지 마세요. 응용 RUNNING이 확인되면 실측 버튼이 활성화됩니다.',next};
 if(['CREATED','PREPARING','RUNNING'].includes(state))return {status:'모델·응용 배포 준비 중',detail:'VM에 모델과 응용을 준비하고 있습니다. 실행 버튼을 다시 누르지 마세요. 응용 RUNNING이 확인되면 실측 버튼이 활성화됩니다.',next};
 if(state==='CREATE_REQUESTED'||r.cloud_job_requested&&state==='READY')return {status:'VM 생성 요청 처리 중',detail:'VM 생성 요청을 처리하고 있습니다. 실행 버튼을 다시 누르지 마세요. VM과 응용 준비가 끝나면 실측할 수 있습니다.',next};
 return null;
}
export function monitoringPanelModel(record){
  const observation=record?.measurement??record?.feedback?.observation,metrics=observation?.metrics;
  const prometheus=observation?.source==='prometheus';
  const scaling=record?.feedback?.geon?.scaling_decision;
  const needsRemeasure=measurementNeedsRefresh(record);
  const preparation=monitoringPreparation(record);
  return {
    preparation,
    app:record?.input?.application?.app_id??'아직 선택한 실행 없음',
    source:needsRemeasure?'변경 전 실측 · 재측정 필요':observation?.source?observationSource(observation):record?.measurement?'실제 어플리케이션 실측':observationSource(observation),
    needsRemeasure,
    decisionSource:observationSource(record?.feedback?.observation??observation),
    status:preparation?.status??({RUNNING:'실행 중',STOPPED:'실행 종료',COMPLETED:'실행 완료'})[record?.execution?.status]??'어플리케이션 배포 전',
    time:observation?.ended_at??observation?.started_at??null,
    metrics:[
      {label:'CPU 평균 사용률',value:display(metrics?.resource?.cpu_average_percent,' %')},
      {label:'어플리케이션 RSS 메모리',value:display(metrics?.resource?.memory_peak_mib,' MiB')},
      {label:prometheus?'어플리케이션 처리 p95':'서비스 응답 p95',value:display(metrics?.inference?.latency_p95_ms,' ms')},
      {label:prometheus?'처리량 · 수집 구간':'처리량',value:display(metrics?.inference?.throughput_rps,' 요청/초')},
      {label:'요청 실패율',value:display(metrics?.inference?.error_rate_percent,' %')},
      {label:prometheus?'관측 요청 수':'실측 요청 수',value:display(observation?.sample_count,' 요청')},
    ],
    decision:scaling?judgementLabel(scaling.action):preparation?'응용 준비 대기':'실측 대기',
    replicas:scaling?`${scaling.current_replicas} → ${scaling.desired_replicas}`:'—',
    note:preparation?.detail??(needsRemeasure?'실행 구성이 변경됐습니다. 표시된 값은 변경 전 기록입니다. 현재 실행을 다시 측정하여 다음 운영 판단으로 이어가세요.':scaling?reasonLabel(scaling.reason??'운영 판단 기록입니다.'):'어플리케이션 배포 후 실측을 실행하거나 저장된 실측 결과를 선택하면 운영 판단을 볼 수 있습니다.'),
  };
}

export function researchPanelModel(record,phase){
  const flow=phase==='operation'?record?.feedback?.geon:record?.geon;
  const assessment=flow?.shadow_policy_assessments?.filter(a=>a.phase===phase).at(-1);
  const saved=record?.research_validations?.filter(r=>r.phase===phase&&r.selection_version===record.selection_version).at(-1)?.result;
  return {
    assessment, saved,
    currentProposal:assessment?assessment.proposed_action==='ABSTAIN'?'규칙 판단 유지':judgementLabel(assessment.proposed_action):'판단 대기',
    caseProposal:saved?judgementLabel(saved.proposal.action):'—',
    oracle:saved?.oracle?judgementLabel(saved.oracle.action):'—',
    agreement:saved?saved.agreement===null?'추가 판단 보류':saved.agreement?'일치':'차이 확인':'—',
  };
}

export function policyComparisonSummary(record,phase){
  const saved=(record?.research_validations??[]).filter(r=>r.selection_version===record.selection_version&&(!phase||r.phase===phase)&&r.result?.comparison?.schema_version==='khu-policy-comparison/v1');
  const artifactSha256=saved.at(-1)?.result.artifact_sha256;
  const cases=new Map();
  for(const r of saved)if(r.result.artifact_sha256===artifactSha256)cases.set(r.result.case_id,r.result.comparison);
  const counts=key=>{
    const rows=[...cases.values()].map(c=>c[key]);
    return {decisions:rows.filter(r=>r.action!=='ABSTAIN').length,abstentions:rows.filter(r=>r.action==='ABSTAIN').length,agreement:rows.filter(r=>r.agreement===true).length,constraintViolations:rows.filter(r=>r.constraint_violation===true).length};
  };
  return {caseCount:cases.size,artifactSha256,baseline:counts('baseline'),validated:counts('validated')};
}

export function operationHistoryModel(record){
  const rows=new Map();
  for(const entry of [...(record?.feedback_history??[]),...(record?.feedback?[record.feedback]:[])]){
    const feedback=entry.messages?.optimizationFeedback?.data?.optimization_feedback;
    const decision=entry.geon?.scaling_decision,observation=entry.observation;
    if(!decision||!observation||!feedback?.feedback_id||feedback.deployment_id!==record?.execution?.execution_id)continue;
    rows.set(feedback.feedback_id,{id:feedback.feedback_id,time:observation.ended_at,source:observation.source==='prometheus'?'Prometheus 수집':['local_probe','cloud_probe'].includes(observation.source)?'실제 어플리케이션 실측':observation.source==='demo_input'?'데모 관측 입력':'기록된 관측',cpu:display(observation.metrics?.resource?.cpu_average_percent,' %'),latency:display(observation.metrics?.inference?.latency_p95_ms,' ms'),decision:judgementLabel(decision.action),replicas:`${decision.current_replicas} → ${decision.desired_replicas}`});
  }
  return [...rows.values()];
}
