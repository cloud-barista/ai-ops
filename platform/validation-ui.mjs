const statusText={verified:'검증 통과',incomplete:'확인 필요',unavailable:'기록을 불러올 수 없음'};
const phaseText={single:'단일 VM',initial:'최초 1대',dual:'확장 후 2대',after_shrink:'축소 후 1대',SINGLE_RUNNING:'첫 VM 실행',DUAL_RUNNING:'두 번째 VM 추가',SINGLE_AFTER_SHRINK:'두 번째 VM 종료',CLEANED:'전체 종료'};
const number=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('ko-KR',{maximumFractionDigits:3}):'—';
const time=v=>typeof v==='string'&&Number.isFinite(Date.parse(v))?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(v))+' KST':'시각 미확인';
export function setupValidationUI({api,document=globalThis.document,runAction=(_label,work)=>work()}){
 const $=id=>document.getElementById(id);if(!$('validation-cards'))return;
 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 const link=(text,href)=>{const a=node('a',text);a.href=href;return a;};
 const detail=(title,...contents)=>{const d=node('details');d.append(node('summary',title),...contents);return d;};
 let busy=false;
 function summary(r,compact=false){
  const article=node('article',undefined,'validation-card');article.setAttribute('data-status',r.status);
  if(!compact)article.id='validation-'+r.id;
  const head=node('div',undefined,'validation-card-head');head.append(node('h3',r.title),node('span',statusText[r.status]??'확인 필요','validation-badge'));article.append(head);
  if(r.status==='unavailable'){article.append(node('p','저장된 결과 파일을 읽을 수 없습니다. 기록이 복원된 뒤 다시 불러오세요.','help'));return article;}
  article.append(node('p',r.id==='vm-count'?'실제 VM 1 → 2 → 1 → 0':`${r.candidate_id??'VM 사양 미확인'}${r.gpu_name?' / '+r.gpu_name:''}`,'validation-subtitle'));
  const metrics=node('div',undefined,'validation-stats');
  for(const [label,value] of [['실제 추론 요청',number(r.sample_count)+'회'],['오류율',number(r.error_rate_percent)+'%']]){
   const cell=node('div');cell.append(node('span',label),node('strong',value));metrics.append(cell);
  }
  article.append(metrics,node('p','검증 종료 '+time(r.ended_at),'help'));
  if(compact){article.append(link('기록 자세히 보기 →','#validation-'+r.id));return article;}
  article.append(node('p','저장된 실행 결과','validation-record-label'));
  if(r.device)article.append(node('p',`추론 장치: ${r.device}${r.gpu_name?' / '+r.gpu_name:''}`,'help'));
  const checks=node('ul',undefined,'validation-checks');for(const c of r.checks??[])checks.append(node('li',(c.passed?'✓ ':'확인 필요: ')+c.label,c.passed?'passed':'needs-check'));article.append(checks);
  if(r.id==='vm-count'){
   article.append(node('p','명시적인 확장·축소 요청을 검증했습니다. 자동 VM 증감 정책과 별도이며, 각 VM의 앱 복제본은 1개입니다.','help'));
   const timeline=node('ol',undefined,'validation-timeline');timeline.setAttribute('aria-label','저장된 AWS VM 수 변경 이력');
   for(const s of r.timeline??[]){const row=node('li');row.append(node('span',phaseText[s.phase]??s.phase),node('strong',number(s.running_vm_count)+'대'),node('time',time(s.checked_at)));
    const ids=node('p',s.children.filter(c=>c.provider_state==='running').map(c=>c.instance_id).join(' / ')||(s.running_vm_count===0?'두 VM 종료 확인':'실행 VM 확인 필요'),'help');row.append(ids);timeline.append(row);
   }article.append(timeline);
  }
  const wrap=node('div',undefined,'validation-measurement-wrap'),table=node('table',undefined,'validation-table validation-measurement-table');table.setAttribute('aria-label',r.title+' 실측값');
  const headRow=node('tr'),thead=node('thead'),tbody=node('tbody');for(const title of ['측정 단계','추론 요청','HTTP p95 (ms)','모델 연산 p95 (ms)','오류율','운영 판단'])headRow.append(node('th',title));thead.append(headRow);
  for(const w of r.windows??[]){const row=node('tr');const phase=node('td');phase.append(node('strong',phaseText[w.phase]??w.phase),node('small',w.instance_id??'VM ID 미확인'));row.append(phase);
   for(const v of [number(w.sample_count)+'회',number(w.http_p95_ms),number(w.model_p95_ms),number(w.error_rate_percent)+'%',(w.operation??'미확인')+' / '+(w.guard??'미확인')])row.append(node('td',v));tbody.append(row);
  }
  table.append(thead,tbody);wrap.append(table);
  const measurementNotes=node('div');measurementNotes.append(node('p','HTTP p95는 관리 연결과 프록시를 포함한 요청 시간이며, 모델 연산 p95와 다른 지표입니다. CPU와 GPU의 성능 우위를 비교하는 실험은 아닙니다.','help'));
  for(const w of r.windows??[])measurementNotes.append(node('p',`${phaseText[w.phase]??w.phase} / ${w.instance_id??'VM ID 미확인'}: 직접 추론 ${number(w.sample_count)}회, Prometheus 관측 ${number(w.prometheus_sample_count)}회. 수집 시간 구간이 달라 요청 수가 다를 수 있습니다.`,'help'));
  const measured=detail('실측값과 관측 구간',wrap,measurementNotes);measured.open=r.id==='vm-count';article.append(measured);
  const flowSection=node('div',undefined,'validation-flow-links');
  for(const [i,id] of (r.flow_ids??[]).entries()){
   const row=node('div',undefined,'validation-flow-row'),current=(r.current??[]).find(c=>c.flow_id===id);
   row.append(node('strong',r.flow_ids.length===1?'저장된 Flow':'VM '+(i+1)+'의 Flow'));
   row.append(node('p',current?.available?`저장된 Flow 상태: 앱 ${current.execution_status??'미확인'} / VM ${current.cloud_state??'미확인'} / 앱 복제본 ${number(current.replicas)}개`:'저장된 Flow 상태: 조회할 수 없음','help'));
   const links=node('div',undefined,'validation-links');for(const [label,hash] of [['배포 계획','planning'],['배포 실행','deployment'],['모니터링','operation'],['실행 기록','events']])links.append(link(label,'?flow='+encodeURIComponent(id)+'#'+hash));row.append(links);flowSection.append(row);
  }article.append(flowSection);
  const source=node('div');source.append(node('p','결과 파일: '+r.file,'help'),node('p','실행 ID: '+r.run_id,'help'));
  if(r.model)source.append(node('p',`모델: ${r.model.model_id} / ${r.model.model_revision}`,'help'),node('p',`배치 ${r.model.batch_size} / 길이 ${r.model.max_length} / ${r.model.precision}`,'help'));
  article.append(detail('기록 출처 및 모델 조건',source));return article;
 }
  async function refresh({notify=false}={}){
  if(busy)return;busy=true;$('validation-reload').disabled=true;$('validation-status').textContent='저장된 검증 기록을 불러오는 중입니다.';
  const records=()=>api('/validation-results');
  const [record,readiness]=await Promise.allSettled([notify?runAction('저장된 실험 결과 조회 중…',records):records(),api('/cloud/readiness')]);
  if(record.status==='fulfilled'){
   const data=record.value;$('validation-cards').replaceChildren(...data.results.map(r=>summary(r)));$('validation-overview')?.replaceChildren(...data.results.map(r=>summary(r,true)));
   renderPolicyValidation({document,record:data.local_policy});
   $('validation-loaded-at').textContent='기록 불러온 시각 '+time(data.loaded_at);
   $('validation-status').textContent='AWS 배포 기록과 로컬 운영 정책 비교를 구분해 표시합니다. 기록 조회는 새 VM이나 추론을 실행하지 않습니다.';
  }else{
   $('validation-cards').replaceChildren(node('p','검증 기록을 불러올 수 없습니다. 다시 불러오기를 눌러주세요.','empty'));
   $('validation-overview')?.replaceChildren(node('p','검증 결과 조회가 필요합니다.','help'));$('validation-status').textContent='기록 조회 실패';$('validation-loaded-at').textContent='';
   renderPolicyValidation({document});
  }
  $('validation-runtime').textContent=readiness.status==='fulfilled'?`현재 플랫폼 설정 — 클라우드 새 실행: ${readiness.value.enabled?'켜짐':'꺼짐'} / 정리 대기: ${Array.isArray(readiness.value.pending_cleanup)?readiness.value.pending_cleanup.length+'건':'확인 필요'}. AWS VM 상태는 실행 시점의 기록과 별도로 조회합니다.`:'현재 플랫폼의 실행 설정을 확인할 수 없음. 저장된 성공 기록과 구분해 주세요.';
  busy=false;$('validation-reload').disabled=false;
 }
  $('validation-reload').addEventListener('click',()=>refresh({notify:true}));const ready=refresh();return {ready,refresh};
}

export function renderPolicyValidation({document,record}){
 const box=document.getElementById('policy-validation'),overview=document.getElementById('policy-validation-overview');if(!box&&!overview)return;
 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 const link=(text,href)=>{const a=node('a',text);a.href=href;return a;};
 if(record?.status!=='verified'){
  const message=record?.status==='incomplete'?'로컬 비교 기록의 무결성을 확인할 수 없습니다. 원본 결과를 확인해 주세요.':'로컬 성능 비교 기록을 불러올 수 없습니다. 서버 버전과 원본 기록을 확인한 뒤 다시 불러오세요.';
  box?.replaceChildren(node('h3','로컬 실행 안정성과 운영 정책 비교'),node('p',message,'help'));
  overview?.replaceChildren(node('p',message,'help'));return;
 }
 const r=record,heading=node('div',undefined,'section-head');
 heading.append(node('h3','로컬 실행 안정성과 운영 정책 비교'),node('span','저장된 로컬 실행 기록','validation-badge'));
 const stats=node('div',undefined,'validation-stats policy-validation-stats');
 for(const [label,value] of [['실제 추론 요청',number(r.requests)+'건'],['요청 오류',number(r.errors)+'건'],['비교 구성','3개 방식 × 3회']]){
  const cell=node('div');cell.append(node('span',label),node('strong',value));stats.append(cell);
 }
 const conclusion=node('p','확장 중 요청 처리를 유지했습니다. 현재 운영 정책의 성능 개선은 확인하지 못했습니다.','policy-validation-conclusion');
 const conditions=node('p',`단일 PC의 BERT Tiny CPU 실행 / 배치 ${r.model.batch_size} / 길이 ${r.model.max_length} / 워커당 CPU 스레드 ${r.cpu_threads}. VM 수가 아닌 같은 PC의 추론 프로세스 수를 비교했습니다.`,'help');
 const trace=node('p',r.phases.map(p=>`${p.rps}요청/초 ${p.duration_seconds}초`).join(' → ')+`. 각 실험은 같은 부하를 사용하며 응답 목표는 ${r.slo_ms}ms입니다.`,'help');
 function table(caption,headers,rows){
  const wrap=node('div',undefined,'validation-measurement-wrap'),table=node('table',undefined,'validation-table policy-comparison-table');table.setAttribute('aria-label',caption);
  const thead=node('thead'),tr=node('tr'),tbody=node('tbody');table.append(node('caption',caption));
  for(const h of headers){const cell=node('th',h);cell.setAttribute('scope','col');tr.append(cell);}thead.append(tr);
  for(const cells of rows){const row=node('tr');for(const c of cells){const cell=node('td');cell.append(typeof c==='string'?node('span',c):c);row.append(cell);}tbody.append(row);}
  table.append(thead,tbody);wrap.append(table);return wrap;
 }
 const aggregate=table('각 방식 3회의 중앙값과 HTTP p95 범위',['실행 방식','요청 / 오류','HTTP p95 중앙값 (ms)','p95 최소~최대 (ms)','SLO 실패율 중앙값','준비 복제본 시간 (replica·s)'],
  r.methods.map(m=>[m.label,`${number(m.requests)} / ${number(m.errors)}`,number(m.http_p95_ms),`${number(m.http_p95_min_ms)} ~ ${number(m.http_p95_max_ms)}`,number(m.slo_failure_percent)+'%',number(m.ready_replica_seconds)]));
 const notes=node('div',undefined,'policy-validation-notes');
 notes.append(node('p','SLO 실패율은 50ms를 넘기거나 실패한 요청의 비율입니다. 중앙값은 각 방식의 3회 결과에서 계산했으며, 전체 요청을 합친 p95가 아닙니다.','help'),
  node('p','2개 고정 방식도 한 반복에서는 p95가 50ms를 넘었습니다. 준비 복제본 시간은 측정 중 준비된 프로세스 수의 시간 합이며 실제 클라우드 비용이나 전체 시작 비용이 아닙니다.','help'));
 const individual=node('details');individual.append(node('summary','9개 개별 실험과 실행 기록'));
 individual.append(table('모든 반복의 개별 결과',['반복 / 방식','HTTP p95 (ms)','SLO 실패율','요청 / 오류','측정 중 프로세스 수','실행 기록'],r.trials.map(t=>[
  `${t.round}회 / ${t.label}`,number(t.http_p95_ms),number(t.slo_failure_percent)+'%',`${number(t.requests)} / ${number(t.errors)}`,t.replicas.join(' → '),link('종료된 Flow 보기','?flow='+encodeURIComponent(t.flow_id)+'#events')])));
 individual.append(node('p',`모든 실험은 종료 후 프로세스 0개를 확인했습니다. 잘못된 입력 거절과 상태 보존은 별도 ${number(r.guard_checks)}사례에서 확인했습니다.`,'help'));
 const provenance=node('details');provenance.append(node('summary','실험 범위와 원본 기록'),
  node('p','각 방식 3회, 단일 모델과 단일 PC의 짧은 부하 실험입니다. 장시간 안정성, 다른 모델, 실제 CPU/GPU VM의 부하 성능과 분류 정확도는 이 결과의 검증 범위가 아닙니다.','help'),
  node('p','관측값을 운영 판단에 전달한 뒤 승인된 변경을 별도 적용했습니다. 이 비교는 API 기반 시험이며 전체 화면 시연 검증과 구분합니다.','help'),
  node('p','종료 '+time(r.ended_at)+' / 원본 '+r.record_file,'help'),node('p','원본 SHA-256: '+r.record_sha256,'help'));
 box?.replaceChildren(heading,stats,conclusion,conditions,trace,aggregate,notes,individual,provenance);
 if(overview){const compact=node('div',undefined,'policy-validation-overview-card');compact.append(node('h3','로컬 운영 정책 비교'),node('p',`${number(r.requests)}건 / 요청 오류 ${number(r.errors)}건. 자동 제어의 성능 개선은 추가 검증이 필요합니다.`,'help'),link('로컬 비교 결과 보기 →','#policy-validation'));overview.replaceChildren(compact);}
}
