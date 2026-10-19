import {judgementLabel,reasonLabel,modelName} from './view-model.mjs';

const sameDecision=(a,b)=>a?.action===b?.action&&(a?.selected_candidate_id??'')===(b?.selected_candidate_id??'');
function explanation(key,trial,candidate){
  if(trial.execution_status!=='executed')return 'AI 응답을 확인하지 못했습니다. 원본 상태는 상세 기록에서 확인할 수 있습니다.';
  if(key==='validated_inference')return trial.guard_status==='REJECTED'?'AI 제안이 조건 검증에서 거부되어 기존 규칙 판단을 적용했습니다.':trial.guard_status==='APPROVED'?'AI 제안이 조건 검증을 통과했습니다.':'조건 검증 결과는 상세 기록에서 확인할 수 있습니다.';
  if(key==='simple_inference')return trial.action==='DEPLOY'?`${candidate}에 배포하자는 AI 제안입니다.`:trial.action==='REJECT'?'배포를 거절하자는 AI 제안입니다.':'조건을 다시 검토하자는 AI 제안입니다.';
  const translated=reasonLabel(trial.reason??'');
  return /[가-힣]/.test(translated)?translated:`설정한 조건에 따른 ${judgementLabel(trial.action)} 판단입니다.`;
}

export function reasoningPanelModel(record){
  const entry=(record?.reasoning_comparisons??[]).findLast(e=>e.selection_version===record.selection_version);
  const result=entry?.result;
  const uncertain=entry&&['PENDING','UNKNOWN'].includes(entry.status);
  const selectedId=entry?.selected_candidate_id??record?.selected_candidate_id;
  const context={app:record?.application_profile?.app_id??record?.input?.application?.app_id??'어플리케이션 선택 전',resource:record?.candidates?.all?.find(c=>c.candidate_id===selectedId)?.name??selectedId??'자원 선택 전',workload:record?.input?.model?`${modelName(record.input.model.model_id)} · 배치 ${record.input.model.batch_size} · 길이 ${record.input.model.max_length}`:'모델 조건 미지정'};
  const rows=result?[
    ['규칙 기반','rule_based'],['Qwen','simple_inference'],['Qwen + 검증','validated_inference']
  ].map(([label,key])=>{
    const trial=result[key]??{execution_status:'skipped'},executed=trial.execution_status==='executed';
    const candidate=record.candidates?.all?.find(c=>c.candidate_id===trial.selected_candidate_id)?.name??trial.selected_candidate_id??'—';
    const rejected=key==='validated_inference'&&trial.guard_status==='REJECTED';
    return {label,role:({rule_based:'자원 조건에 따른 기준 판단',simple_inference:'Qwen이 제안한 배포 판단',validated_inference:'AI 제안에 조건 검증 적용'})[key],action:executed?judgementLabel(trial.action):trial.execution_status==='skipped'?'비교 미실행':'응답 없음',candidate:executed?candidate:'—',reason:explanation(key,trial,candidate),originalReason:trial.reason??'',guard:rejected?'제안 거부 · 규칙 판단 적용':({APPROVED:'조건 확인 통과',NOT_APPLIED:'검증 적용 전'})[trial.guard_status]??'—',duration:key==='rule_based'?'별도 미측정':key==='validated_inference'&&executed?'같은 AI 제안 검증':executed&&Number.isFinite(trial.latency_ms)?`AI 응답 ${new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(trial.latency_ms/1000)}초`:'—'};
  }):[];
  const completed=entry?.status==='COMPLETED'&&['rule_based','simple_inference','validated_inference'].every(k=>result?.[k]?.execution_status==='executed');
  const agreement=completed&&sameDecision(result.rule_based,result.simple_inference)&&sameDecision(result.rule_based,result.validated_inference);
  const summaryValue=!entry?'비교 전':entry.status==='PENDING'?'비교 중':entry.status==='UNKNOWN'?'결과 확인 대기':!completed?'비교 미완료':result.validated_inference.guard_status==='REJECTED'?'AI 제안 거부':agreement?'세 판단 일치':'판단 차이 있음';
  const summary=entry?.status==='PENDING'?'같은 입력으로 AI 응답을 기다리고 있습니다. 기존 결과 조회로 확인할 수 있습니다.':entry?.status==='UNKNOWN'?'비교 결과를 아직 확인하지 못했습니다. 기존 결과 조회는 AI를 다시 호출하지 않습니다.':entry&&!completed?'AI 비교를 완료하지 못했습니다. 기존 배포 판단을 유지합니다.':completed?summaryValue==='AI 제안 거부'?'조건 검증에서 AI 제안이 거부되어 기존 기준 판단을 유지합니다.':agreement?'기준 판단과 AI 제안·검증 결과가 일치합니다.':'판단 방식마다 선택 결과가 다릅니다. 세부 결과를 확인하세요.':record?.geon?`${context.resource}에 대한 배포 판단을 세 방식으로 비교할 수 있습니다.`:'요구사항 분석과 자원 선택 후 배포 계획을 먼저 만드세요.';
  return {entry,rows,context,summary,summaryValue,pending:!!uncertain,canRun:!!record?.geon?.decision&&!uncertain,canRefresh:!!uncertain,model:result?.simple_inference?.actual_model??'로컬 Qwen',executionNote:'이 비교는 연구 결과로 저장됩니다. 어플리케이션 실행은 검증을 통과한 배포 계획을 따릅니다.',nextHref:record?.geon?'#deployment':null,nextLabel:record?.execution?'어플리케이션 실행 기록 확인':'다음 실행 단계로 이동'};
}
