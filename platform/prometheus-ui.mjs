import {replicaView} from './replicas-ui.mjs';
const format=(v,unit)=>Number.isFinite(v)?new Intl.NumberFormat('ko-KR',{maximumFractionDigits:2}).format(v)+unit:'—';
const labels={IDLE:'어플리케이션 배포 후 자동 수집',UNAVAILABLE:'수집 서버 연결 대기',DOWN:'어플리케이션 지표 연결 대기',WAITING_SAMPLES:'수집 구간 준비 중',WAITING_REQUESTS:'CPU·메모리 수집 중 · 최근 60초 추론 요청 없음',COLLECTING:'Prometheus 자동 수집 중',IDENTITY_MISMATCH:'현재 어플리케이션의 수집 값 대기',STALE:'새 수집 값 대기',COUNTER_RESET:'새 수집 구간 준비 중'};
export function prometheusPanelModel(result={},record){
  const saved=[record?.measurement,record?.prometheus_measurement,record?.feedback?.observation].find(o=>o?.source==='prometheus'&&(!record?.flow_id||o.flow_id===record.flow_id)&&(!record?.replica_group||o.group_id===record.replica_group.group_id));
  const archived=result.state==='IDLE'&&saved,observation=archived||result.observation,metrics=observation?.metrics??result.metrics;
  return {archived:!!archived,status:archived?'저장된 Prometheus 수집 결과':labels[result.state]??'어플리케이션 배포 후 자동 수집',server:result.server_ready?'Prometheus 연결됨':'Prometheus 준비 중',time:observation?.ended_at??result.collected_at,canFeedback:!!result.can_feedback&&!archived,metrics:[
    {label:'CPU 평균 사용률',value:format(metrics?.resource?.cpu_average_percent,' %')},
    {label:'어플리케이션 RSS 메모리',value:format(metrics?.resource?.memory_peak_mib,' MiB')},
    {label:'어플리케이션 처리 p95',value:format(metrics?.inference?.latency_p95_ms,' ms')},
    {label:'처리량 · 수집 구간',value:format(metrics?.inference?.throughput_rps,' 요청/초')},
    {label:'요청 실패율',value:format(metrics?.inference?.error_rate_percent,' %')},
    {label:'관측 요청 수',value:format(observation?.sample_count,' 요청')},
  ]};
}
export function setupPrometheusMonitoring({api,getReceipt,isBusy,onFeedback,document:doc=document,runAction=(_label,work)=>work()}={}){
  const $=id=>doc.getElementById(id),node=(tag,text,className)=>{const e=doc.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
  let latest=null,inFlight=false;
  function plot(points,key,label,color){
    const wrap=node('div',undefined,'prometheus-chart');wrap.append(node('span',label,'metric-label'));
    const valid=points.filter(p=>Number.isFinite(p[key]));if(valid.length<2){wrap.append(node('p','2회 이상 수집하면 최근 변화가 표시됩니다.','help'));return wrap;}
    const min=key==='cpu_percent'?0:Math.min(...valid.map(p=>p[key]))*.95,max=Math.max(min+1,...valid.map(p=>p[key]))*1.05;
    const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 400 90');svg.setAttribute('role','img');svg.setAttribute('aria-label',label+' 최근 수집 변화');
    const line=doc.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('fill','none');line.setAttribute('stroke',color);line.setAttribute('stroke-width','2');
    line.setAttribute('points',valid.map((p,i)=>`${8+i/(valid.length-1)*384},${78-(p[key]-min)/(max-min)*65}`).join(' '));svg.append(line);wrap.append(svg);
    wrap.append(node('span',`최근 ${valid.length}개 수집 값 · ${format(valid.at(-1)[key],key==='cpu_percent'?' %':' MiB')}`,'metric-sub'));return wrap;
  }
  function render(){
    const r=getReceipt(),bound=latest?.flow_id===r?.flow_id?latest:{state:'IDLE',server_ready:latest?.server_ready};
    const result=r?.execution?.status!=='RUNNING'?{...bound,state:'IDLE',observation:null,metrics:null,can_feedback:false,points:[]}:bound;
    const v=prometheusPanelModel(result,r);
    $('prometheus-status').textContent=v.status;$('prometheus-server').textContent=v.server;
    $('prometheus-context').textContent=`${v.archived?'저장된 수집 구간':'5초마다 자동 수집 · 최근 최대 60초'}${v.time?' · '+new Date(v.time).toLocaleTimeString('ko-KR',{hour12:false}):''}`;
    $('prometheus-metrics').replaceChildren(...v.metrics.map(m=>{const e=node('div');e.append(node('span',m.label,'metric-label'),node('span',m.value,'metric-value'));return e;}));
    $('prometheus-feedback').disabled=isBusy()||replicaView(r).pendingRevision||['PENDING','UNKNOWN'].includes(r?.feedback_state)||!v.canFeedback;
    $('prometheus-refresh').disabled=inFlight||isBusy();
    $('prometheus-charts').replaceChildren(plot(result.points??[],'cpu_percent','CPU 사용률 (%)','#1d6772'),plot(result.points??[],'memory_mib','어플리케이션 RSS 메모리 (MiB)','#304b75'));
  }
  async function refresh({notify=false}={}){
    const r=getReceipt();if(inFlight||isBusy()||!r||!['local','cloud'].includes(r.input?.execution_target)){render();return;}
    const work=async()=>{const result=await api(`/flows/${r.flow_id}/prometheus`);if(getReceipt()?.flow_id===r.flow_id&&getReceipt()?.execution?.execution_id===r.execution?.execution_id)latest=result;};
    inFlight=true;render();try{await (notify?runAction('Prometheus 수집값 조회 중…',work):work());}catch{latest={flow_id:r.flow_id,state:'UNAVAILABLE',server_ready:false};}finally{inFlight=false;render();}
  }
  $('prometheus-refresh').addEventListener('click',()=>refresh({notify:true}));
  $('prometheus-feedback').addEventListener('click',async()=>{const r=getReceipt();if(isBusy()||replicaView(r).pendingRevision||['PENDING','UNKNOWN'].includes(r?.feedback_state))return;await onFeedback();await refresh();});
  setInterval(()=>{if(!doc.hidden&&doc.body.dataset.workspaceView==='operation')refresh();},5000);
  return {render,refresh};
}
