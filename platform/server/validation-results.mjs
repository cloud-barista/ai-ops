import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {loadPolicyValidation} from './policy-validation.mjs';

const evidenceDirectory=fileURLToPath(new URL('../../output/khu-platform-validation/cloud-live-validation/',import.meta.url));
// Deliberately fixed selections: callers cannot request an arbitrary local file.
const sources=[
 {id:'cpu',title:'CPU VM 배포',file:'cpu-result-2026-10-16T08-59-14-944Z.json'},
 {id:'gpu',title:'GPU VM 배포',file:'gpu-result-2026-10-16T16-22-47-089Z.json'},
 {id:'vm-count',title:'CPU VM 수 변경',file:'campaign-result-2026-10-16T17-15-32-712Z.json'},
];
const array=v=>Array.isArray(v)?v:[];
const finite=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0;
const timestamp=v=>typeof v==='string'&&Number.isFinite(Date.parse(v));
const flowId=v=>typeof v==='string'&&/^flow-[\w-]+$/.test(v);
const instanceId=v=>typeof v==='string'&&/^i-[a-f0-9]{8}(?:[a-f0-9]{9})?$/.test(v);
const check=(key,label,passed)=>({key,label,passed:passed===true});
const diskClean=v=>v?.confirmed===true&&v.attachments?.verified===true&&Array.isArray(v.attachments.remaining)&&v.attachments.remaining.length===0;
function windowView(m,{phase,instance_id,operation,guard,prometheus}={}){
 const i=m?.metrics?.inference;
 return {phase,flow_id:m?.flow_id,instance_id,started_at:m?.started_at,ended_at:m?.ended_at,sample_count:m?.sample_count??null,
  http_p95_ms:i?.latency_p95_ms??null,model_p95_ms:i?.model_inference_p95_ms??null,error_rate_percent:i?.error_rate_percent??null,
  throughput_rps:i?.throughput_rps??null,prometheus_sample_count:prometheus?.observation?.sample_count??null,
  operation:operation?.action??null,guard:guard?.status??null};
}
function measured(m){const i=m?.metrics?.inference;return m?.source==='cloud_probe'&&Number.isInteger(m.sample_count)&&m.sample_count>0&&i?.error_rate_percent===0&&finite(i.latency_p95_ms)&&finite(i.model_inference_p95_ms)&&timestamp(m.ended_at);}
function deviceProof(proof,m,gpu=false){
 const expected=gpu?'cuda:0':'cpu';
 return proof?.forward_finite===true&&proof.model_device===expected&&proof.input_device===expected&&
  m?.sample?.model_device===expected&&m.sample.input_device===expected&&(!gpu||proof.cuda_available===true);
}
function single(raw,source){
 const checks=[check('deployment','실제 VM 배포',raw.cloud_deployment_verified===true&&flowId(raw.flow_id)&&instanceId(raw.provider_instance_id)&&raw.measurement?.flow_id===raw.flow_id&&raw.bootstrap_proof?.provider_instance_id===raw.provider_instance_id),
  check('device',source.id==='gpu'?'CUDA 추론 확인':'CPU 추론 확인',deviceProof(raw.bootstrap_proof,raw.measurement,source.id==='gpu')),
  check('inference','실제 추론 요청',measured(raw.measurement)),
  check('observation','Prometheus 관측',raw.prometheus_verified===true&&raw.prometheus?.observation?.source==='prometheus'&&raw.prometheus.observation.flow_id===raw.flow_id&&raw.prometheus.observation.sample_count>0),
  check('operation','운영 판단 및 검증',!!raw.operation?.action&&raw.operation_guard?.status==='APPROVED'),
  check('cleanup','VM 종료 및 디스크 정리',raw.cleanup_verified===true&&instanceId(raw.provider_instance_id)&&diskClean(raw.cleanup)&&raw.provider_status?.instance_id===raw.provider_instance_id&&raw.provider_status?.state==='Terminated')];
 return {...source,historical:true,status:raw.passed===true&&timestamp(raw.ended_at)&&checks.every(c=>c.passed)?'verified':'incomplete',checks,
  run_id:raw.run_id,flow_ids:flowId(raw.flow_id)?[raw.flow_id]:[],started_at:raw.started_at,ended_at:raw.ended_at,candidate_id:raw.candidate_id,
  device:raw.measurement?.sample?.model_device??null,gpu_name:raw.bootstrap_proof?.gpu_name??null,model:raw.measurement?.model,
  sample_count:raw.measurement?.sample_count??null,error_rate_percent:raw.measurement?.metrics?.inference?.error_rate_percent??null,
  windows:[windowView(raw.measurement,{phase:'single',instance_id:raw.provider_instance_id,operation:raw.operation,guard:raw.operation_guard,prometheus:raw.prometheus})],timeline:[]};
}
function campaign(raw,source){
 const snapshots=array(raw.snapshots),measurements=array(raw.measurements),ids=array(raw.flow_ids),children=array(raw.child_cleanup);
 const expectedPhases=['SINGLE_RUNNING','DUAL_RUNNING','SINGLE_AFTER_SHRINK','CLEANED'];
 const expectedCounts=[1,2,1,0];
 const expectedStates=[['running','NOT_CREATED'],['running','running'],['running','Terminated'],['Terminated','Terminated']];
 const timeline=snapshots.map(s=>({phase:s.phase,checked_at:s.checked_at,running_vm_count:s.running_vm_count,verified:s.verified===true,
  children:array(s.children).map(c=>({flow_id:c.flow_id,instance_id:c.instance_id,provider_state:c.provider_state}))}));
 const known=new Map(children.map(c=>[c.flow_id,c.cloud?.resource?.provider_instance_id]));
 const identity=ids.length===2&&new Set(ids).size===2&&ids.every(flowId)&&ids.every(id=>instanceId(known.get(id)))&&new Set(known.values()).size===2;
 const trace=identity&&snapshots.length===4&&snapshots.every((s,index)=>s.verified===true&&s.phase===expectedPhases[index]&&s.running_vm_count===expectedCounts[index]&&timestamp(s.checked_at)&&
  (index===0||Date.parse(s.checked_at)>Date.parse(snapshots[index-1].checked_at))&&array(s.children).length===2&&new Set(s.children.map(c=>c.flow_id)).size===2&&
  s.children.every(c=>ids.includes(c.flow_id)&&c.provider_state===expectedStates[index][ids.indexOf(c.flow_id)]&&(c.instance_id===known.get(c.flow_id)||index===0&&c.instance_id===null&&c.provider_state==='NOT_CREATED'))&&s.children.filter(c=>c.provider_state==='running').length===s.running_vm_count);
 const windows=measurements.map(m=>windowView(m.measurement,{phase:m.phase,instance_id:m.instance_id,operation:m.operation,guard:m.guard,prometheus:array(raw.prometheus).find(p=>p.phase===m.phase&&p.flow_id===m.flow_id)}));
 const pairs=measurements.map(m=>m.phase+':'+m.flow_id);
 const inference=identity&&measurements.length===4&&['initial:'+ids[0],'dual:'+ids[0],'dual:'+ids[1],'after_shrink:'+ids[0]].every(p=>pairs.includes(p))&&
  measurements.every(m=>m.measurement?.flow_id===m.flow_id&&m.instance_id===known.get(m.flow_id)&&m.bootstrap_proof?.provider_instance_id===m.instance_id&&measured(m.measurement)&&deviceProof(m.bootstrap_proof,m.measurement));
 const checks=[check('vm-count','AWS VM 수 1 → 2 → 1 → 0',raw.vm_count_verified===true&&trace),check('inference','각 VM 추론 및 축소 후 재추론',inference),
  check('observation','각 단계 Prometheus 관측',measurements.length===4&&measurements.every(m=>{
   const p=array(raw.prometheus).find(p=>p.phase===m.phase&&p.flow_id===m.flow_id),o=p?.observation;
   return p?.server_ready===true&&o?.source==='prometheus'&&o.flow_id===m.flow_id&&o.group_id===m.measurement?.group_id&&o.membership_epoch===m.measurement?.membership_epoch&&o.sample_count>0;
  })),
  check('operation','관측 후 운영 판단 및 검증',measurements.length===4&&measurements.every(m=>!!m.operation?.action&&m.guard?.status==='APPROVED')),
  check('cleanup','두 VM 종료 및 디스크 정리',raw.cleanup_verified===true&&identity&&raw.final_campaign?.state==='CLEANED'&&raw.final_campaign.cleanup?.confirmed===true&&children.length===2&&children.every(c=>c.cloud?.state==='CLEANED'&&diskClean(c.cloud.cleanup))),
  check('connections','관리 연결 정리',raw.connection_audit?.verified===true&&['active_owned_sessions','cli_processes','plugin_processes','forward_listeners'].every(k=>raw.connection_audit[k]===0))];
 const sample_count=windows.every(w=>Number.isInteger(w.sample_count)&&w.sample_count>0)?windows.reduce((n,w)=>n+w.sample_count,0):null;
 return {...source,historical:true,status:raw.passed===true&&timestamp(raw.ended_at)&&checks.every(c=>c.passed)?'verified':'incomplete',checks,
  run_id:raw.campaign_id,flow_ids:ids.filter(flowId),started_at:raw.started_at,ended_at:raw.ended_at,candidate_id:raw.final_campaign?.intent?.candidate_id,
  sample_count,error_rate_percent:sample_count&&windows.every(w=>finite(w.error_rate_percent))?windows.reduce((n,w)=>n+w.sample_count*w.error_rate_percent,0)/sample_count:null,
  model:measurements[0]?.measurement?.model,automatic_vm_scaling:false,windows,timeline};
}
export async function loadValidationResults({directory=evidenceDirectory,readFlow}={}){
 const results=await Promise.all(sources.map(async source=>{
  let result;try{const raw=JSON.parse((await readFile(resolve(directory,source.file),'utf8')).replace(/^\uFEFF/,''));result=source.id==='vm-count'?campaign(raw,source):single(raw,source);}
  catch{return {...source,historical:true,status:'unavailable',checks:[],flow_ids:[],current:[],windows:[],timeline:[]};}
  result.current=await Promise.all(result.flow_ids.map(async flow_id=>{
   try{if(!readFlow)throw Error('no current store');const r=await readFlow(flow_id);if(r.flow_id!==flow_id)throw Error('identity mismatch');
    return {flow_id,available:true,execution_status:r.execution?.status??null,cloud_state:r.cloud?.state??null,replicas:r.execution?.actual_replicas??null};
   }catch{return {flow_id,available:false};}
  }));return result;
 }));
 return {schema_version:1,read_only:true,loaded_at:new Date().toISOString(),results,local_policy:await loadPolicyValidation()};
}
