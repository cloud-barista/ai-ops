import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

const evidenceDirectory=fileURLToPath(new URL('../../output/khu-platform-validation/policy-runtime-fix-20261017/',import.meta.url));
// Frozen historical evidence only. Neither callers nor stored JSON select files to read.
const resultFile='evaluation-2026-10-17T13-56-48.004Z/result.json';
const resultHash='a11f3d89b1fc56020311779e6f06e79431c76d772a3c3f5dc79fd12a3e8f2f91';
const protocolHash='6f5c147361fbdc6904e05f9bd1e870bbe395e9e5395f82e5cf8a3bd84e93b081';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const strategies=['fixed1','fixed2','adaptive'];
const labels={fixed1:'프로세스 1개 고정',fixed2:'프로세스 2개 고정',adaptive:'현재 운영 정책'};
const median=values=>[...values].sort((a,b)=>a-b)[Math.floor(values.length/2)];
const empty=status=>({status,read_only:true,historical:true,execution_verified:false,cloud_verified:false,model_accuracy_evaluated:false,policy_effect:'not_demonstrated',methods:[],trials:[]});

export async function loadPolicyValidation({directory=evidenceDirectory}={}){
 let report,protocol;
 try{
  const [raw,rule]=await Promise.all([readFile(resolve(directory,resultFile)),readFile(resolve(directory,'protocol.json'))]);
  if(hash(raw)!==resultHash||hash(rule)!==protocolHash)return empty('incomplete');
  report=JSON.parse(raw);protocol=JSON.parse(rule);
 }catch{return empty('unavailable');}
 const trials=report.trials??[],guard=trials.flatMap(t=>t.guard_checks??[]);
 const valid=report.passed===true&&report.protocol_sha256===protocolHash&&report.active_local_groups_after?.length===0&&
  trials.length===9&&protocol.orders.length===3&&guard.length===9&&guard.every(g=>g.rejected===true&&g.state_preserved===true)&&
  trials.every((t,i)=>t.passed===true&&t.completed===true&&t.round===Math.floor(i/3)+1&&t.strategy===protocol.orders[Math.floor(i/3)][i%3]&&
   t.cleanup?.verified===true&&t.cleanup.actual_replicas===0&&t.validated_successes===t.summary?.successes&&
   t.summary.requests===5136&&t.summary.errors===0&&t.summary.successes===5136&&t.summary.generator_timing_valid===true&&
   t.summary.dispatch_lag_p95_ms<=protocol.maximum_dispatch_lag_p95_ms&&/^flow-[\w-]+$/.test(t.flow_id));
 if(!valid)return empty('incomplete');
 const methods=strategies.map(strategy=>{
  const own=trials.filter(t=>t.strategy===strategy),p95=own.map(t=>t.summary.http_p95_ms);
  return {strategy,label:labels[strategy],requests:own.reduce((n,t)=>n+t.summary.requests,0),errors:own.reduce((n,t)=>n+t.summary.errors,0),
   http_p95_ms:median(p95),http_p95_min_ms:Math.min(...p95),http_p95_max_ms:Math.max(...p95),
   slo_failure_percent:median(own.map(t=>t.summary.slo_failure_percent)),ready_replica_seconds:median(own.map(t=>t.summary.ready_replica_seconds))};
 });
 return {...empty('verified'),execution_verified:true,scope:'local_process',started_at:report.started_at,ended_at:report.ended_at,
  rounds:3,requests:trials.reduce((n,t)=>n+t.summary.requests,0),errors:0,slo_ms:protocol.slo_ms,guard_checks:guard.length,
  model:trials[0].model,cpu_threads:protocol.cpu_threads_per_worker,phases:protocol.phases,methods,
  trials:trials.map(t=>({round:t.round,strategy:t.strategy,label:labels[t.strategy],flow_id:t.flow_id,http_p95_ms:t.summary.http_p95_ms,
   requests:t.summary.requests,errors:t.summary.errors,slo_failure_percent:t.summary.slo_failure_percent,ready_replica_seconds:t.summary.ready_replica_seconds,
   cleanup_verified:t.cleanup.verified&&t.cleanup.actual_replicas===0,replicas:t.membership.map(m=>m.replicas),
   apply_ms:t.control.filter(c=>typeof c.apply_ms==='number').map(c=>c.apply_ms)})),
  record_file:resultFile,record_sha256:resultHash,protocol_sha256:protocolHash};
}
