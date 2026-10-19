import {profileLlmCandidates,summarizeProfileComparison} from './profile-llm.mjs';

export function setupLlmComparison({api,onSelectionChange,onActivityChange,isBusy,canChangeSelection=()=>true}){
  const $=id=>document.getElementById(id);
  const element=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  let options=null,runs=[],run=null,timer=null,message='',loading=false;
  const select=$('profile-llm');
  for(const candidate of profileLlmCandidates){const option=element('option',candidate.label);option.value=candidate.candidate_id;select.append(option);}
  try{const saved=localStorage.getItem('khu-profile-llm');if(profileLlmCandidates.some(c=>c.candidate_id===saved))select.value=saved;}catch{}
  function choose(id,notify=true){
    if(notify&&(isBusy()||!canChangeSelection()))return;
    if(!profileLlmCandidates.some(c=>c.candidate_id===id))return;
    select.value=id;try{localStorage.setItem('khu-profile-llm',id);}catch{}
    if(notify){$('analysis-mode').value='natural_language';onSelectionChange();}
    render();
  }
  function running(){return run?.state==='RUNNING'||loading;}
  function notifyActivity(overrides={}){
    onActivityChange({busy:running(),label:loading?'LLM 비교 기록 확인 중…':'두 모델 비교 실행 중…',detail:run?.state==='RUNNING'?`${run.completed_count} / ${run.total_count} 호출 완료`:'',failed:!!message||['FAILED','INTERRUPTED'].includes(run?.state)||run?.benchmark_status==='no_valid_llm_response',...overrides});
  }
  function render(){
    const busy=isBusy(),active=running();
    for(const option of select.options)option.disabled=!options?.candidates.find(c=>c.candidate_id===option.value)?.available;
    select.disabled=select.disabled||active||!options;
    const selected=options?.candidates.find(c=>c.candidate_id===select.value);
    if($('analysis-mode').value==='natural_language'&&(active||!selected?.available))$('analyze').disabled=true;
    $('llm-eval-run').disabled=busy||active||!options||options.candidates.some(c=>!c.available);
    $('llm-eval-reload').disabled=loading;
    $('llm-eval-record').disabled=loading||!runs.length;
    $('llm-eval-record').replaceChildren(...runs.length?runs.map(record=>{const option=element('option',`${new Date(record.created_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})} · ${{COMPLETED:'비교 완료',RUNNING:'비교 중',INTERRUPTED:'실행 중단',FAILED:'실행 종료'}[record.state]??record.state}`);option.value=record.run_id;return option;}):[element('option','아직 비교 기록이 없습니다')]);
    if(run)$('llm-eval-record').value=run.run_id;
    $('llm-eval-status').textContent=message||(run?.state==='RUNNING'?`두 모델 순차 분석 중 · ${run.completed_count} / ${run.total_count} 호출 완료`:run?.state==='COMPLETED'?(run.benchmark_status==='no_valid_llm_response'?'비교 실행 종료 · 유효한 LLM 응답 없음 · 사례별 결과를 확인하세요.':`실제 비교 완료 · 모델 2개 × 사례 3개 · ${new Date(run.completed_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})} KST`):run?`저장된 비교 · ${run.state==='INTERRUPTED'?'실행 중단':'실행 종료'} · ${run.completed_count} / ${run.total_count} 호출 기록`:options?'동일한 3가지 요구사항으로 두 모델을 실제 호출합니다.':'사용 가능한 로컬 모델을 확인합니다.');
    const summary=run?.summary??(run?summarizeProfileComparison(run.results,run.candidates,3):null);
    const rows=summary?.candidates??profileLlmCandidates;
    const body=$('llm-eval-rows');body.replaceChildren(...rows.map(candidate=>{
      const row=element('tr'),name=element('th',candidate.label);name.scope='row';
      if(run?.state==='COMPLETED'&&summary?.recommended_candidate_id===candidate.candidate_id)name.append(element('small','정확성 우선 추천','llm-recommended'));
      row.append(name,element('td',candidate.total_fields?`${candidate.field_accuracy_percent}% · ${candidate.matched_fields}/${candidate.total_fields}`:'—'),element('td',candidate.completed_count?`${candidate.contract_count}/${candidate.completed_count}`:'—'),element('td',candidate.completed_count?`${candidate.exact_count}/${candidate.completed_count}`:'—'),element('td',Number.isFinite(candidate.average_duration_ms)?`${(candidate.average_duration_ms/1000).toFixed(2)}초`:'—'));
      const cell=element('td'),button=element('button',select.value===candidate.candidate_id?'선택됨':'이 모델 선택','event-button');button.type='button';button.setAttribute('aria-label',candidate.label+' 선택');button.setAttribute('aria-pressed',String(select.value===candidate.candidate_id));button.disabled=busy||active||!canChangeSelection()||!options?.candidates.find(c=>c.candidate_id===candidate.candidate_id)?.available;button.title=canChangeSelection()?'요구사항 분석에 사용할 모델 선택':'실행 중인 실험의 분석 모델은 새 실험에서 변경하세요.';button.addEventListener('click',()=>choose(candidate.candidate_id));cell.append(button);row.append(cell);return row;
    }));
    const details=$('llm-eval-cases');details.hidden=!run?.results.length;
    $('llm-eval-case-list').replaceChildren(...(run?.results??[]).map(result=>{
      const item=element('details'),title=profileLlmCandidates.find(c=>c.candidate_id===result.candidate_id)?.label??result.actual_model;
      item.append(element('summary',`${title} · ${result.scenario_label} · ${result.exact_match?'조건 일치':result.contract_valid?'조건 차이':'유효 응답 없음'} · ${result.matched_fields}/${result.total_fields}`));
      const explanation=element('p',result.contract_valid?'자원 조건을 정답과 필드별로 비교한 결과입니다.':'요구사항 분석 모델의 유효한 응답을 받지 못해 성공으로 집계하지 않았습니다.','help');item.append(explanation);
      const pre=element('pre',JSON.stringify({duration_ms:result.duration_ms,fields:result.fields,response:result.response,error_code:result.error_code},null,2));item.append(pre);return item;
    }));
    $('llm-eval-protocol').hidden=!run;
    if(run)$('llm-eval-protocol').textContent=`고정 사례 3개 · 사례당 1회 · temperature 0 · 최종 JSON·Profile 계약 검사 · 시간은 모델 로딩과 재시도를 포함한 전체 호출 시간입니다. ${run.suite_sha256.slice(0,12)}`;
    $('llm-eval-panel').dataset.runId=run?.run_id??'';
  }
  function schedule(){clearTimeout(timer);if(run?.state==='RUNNING')timer=setTimeout(()=>poll(),1500);}
  async function poll(){
    try{run=await api('/llm-comparisons/'+run.run_id);runs=runs.map(r=>r.run_id===run.run_id?run:r);message='';render();notifyActivity();schedule();}
    catch{message='결과 조회를 잠시 멈췄습니다. 저장된 비교 불러오기로 이어서 확인하세요.';render();notifyActivity({busy:false,failed:true});}
  }
  async function refresh({notify=false}={}){
    if(loading)return;loading=true;render();notifyActivity({silent:!notify});
    try{
      const [available,saved]=await Promise.all([api('/llm-candidates'),api('/llm-comparisons')]);options=available;runs=saved.runs;
      let id=run?.run_id;try{id??=localStorage.getItem('khu-llm-comparison');}catch{}
      run=runs.find(r=>r.run_id===available.active_run_id)??runs.find(r=>r.run_id===id)??runs[0]??null;
      if(run){run=await api('/llm-comparisons/'+run.run_id);try{localStorage.setItem('khu-llm-comparison',run.run_id);}catch{}}
      message='';
    }catch{message='로컬 모델과 저장된 비교 서비스 연결을 확인해 주세요.';}
    finally{loading=false;render();notifyActivity({busy:!message&&running(),silent:!notify&&run?.state!=='RUNNING'});schedule();}
  }
  $('llm-eval-run').addEventListener('click',async()=>{
    if(isBusy()||running())return;
    loading=true;message='두 모델의 비교 실행을 준비합니다.';render();notifyActivity({label:'두 모델 비교 준비 중…'});
    const request_id=crypto.randomUUID();
    try{run=await api('/llm-comparisons',{request_id});runs=[run,...runs.filter(r=>r.run_id!==run.run_id)];try{localStorage.setItem('khu-llm-comparison',run.run_id);}catch{}message='';}
    catch{message='실행 결과를 확인하지 못했습니다. 저장된 비교 불러오기로 확인하세요.';}
    finally{loading=false;render();notifyActivity({busy:!message&&running()});schedule();}
  });
  $('llm-eval-reload').addEventListener('click',()=>refresh({notify:true}));
  $('llm-eval-record').addEventListener('change',async()=>{if(loading)return;clearTimeout(timer);run=runs.find(r=>r.run_id===$('llm-eval-record').value);loading=true;render();notifyActivity();try{localStorage.setItem('khu-llm-comparison',run.run_id);run=await api('/llm-comparisons/'+run.run_id);message='';}catch{message='저장된 결과를 불러오지 못했습니다.';}finally{loading=false;render();notifyActivity({busy:!message&&running()});schedule();}});
  select.addEventListener('change',()=>choose(select.value));
  return {render,refresh,choose,isRunning:running,candidateId:()=>select.value};
}
