import { evaluateBenchmarkPolicy } from '../policy.mjs';
import {cloudCandidatePolicy} from '../cloud-types.mjs';

const tupleMatch = (a,b) => ['model_id','model_revision','batch_size','max_length'].every(k=>a?.[k]===b?.[k]);
export function buildCandidates({applicationProfile,recommendation,workload,catalog,cpuPolicy,evidence,nowUtc=new Date().toISOString(),resourceOrigin='demo_input',executionTarget='historical'}) {
  const req=applicationProfile.requirements;
  if(executionTarget==='local'){
    const all=(recommendation.recommendations??[]).filter(r=>r.resource.resource_id==='khu-local-pc'&&r.resource.provider==='local').map(({resource:r})=>{
      const selectable=r.healthy&&!req.accelerator.required&&req.deployment.replicas_min===1&&r.cpu.available_cores>=req.compute.cpu_cores_min&&r.memory.available_mib>=req.compute.memory_mib_min&&r.storage.available_gib>=req.compute.storage_gib_min;
      return {candidate_id:r.resource_id,spec_id:'local+windows+pc',name:'이 PC · 로컬 실행',display_name:'실제 로컬 자원',origin:'observed',shape:{cpu_cores:r.cpu.logical_cores,memory_mib:r.memory.total_mib,gpu_count:0},feasible:selectable,selectable,fit_state:selectable?'confirmed':'unsuitable',reason:'실시간 CPU·메모리·디스크 관측 · AppDeploy local_process',measurement:null,benchmark_qualified:false,price:{hourly_usd:null,work_usd:null},source:{origin:'local_os',observed_at:recommendation.observed_at}};
    });
    const excluded=(recommendation.excluded_resources??[]).filter(r=>r.resource_id==='khu-local-pc').map(r=>({candidate_id:r.resource_id,failures:structuredClone(r.failures??[])}));
    const discovered_count=new Set([...all.map(c=>c.candidate_id),...excluded.map(c=>c.candidate_id)]).size;
    return {primary:all.filter(c=>c.selectable),all,excluded,discovered_count,provenance:{resource_origin:'observed',execution_target:'local'}};
  }
  const matching=cpuPolicy.cohorts.filter(c=>tupleMatch(c,workload) && (!workload.cohort_id || c.cohort_id===workload.cohort_id));
  const cohort=matching.length===1?matching[0]:null;
  const policy=cohort?evaluateBenchmarkPolicy(cpuPolicy,{cohortId:cohort.cohort_id,minThroughput:workload.min_throughput??null,maxBatchP95Ms:workload.max_batch_p95_ms??null,inputCount:workload.input_count??1000000,nowUtc}):null;
  const historical=evidence.paired_workloads.filter(c=>tupleMatch(c,workload));
  const rows=recommendation.recommendations??[];
  const all=catalog.entries.filter(spec=>!spec.validation_only||executionTarget==='cloud').map(spec=>{
    const upstream=rows.find(r=>r.resource.labels?.spec_id===spec.spec_id || (resourceOrigin==='demo_input'&&r.resource.resource_id==='demo-'+spec.name.replaceAll('.','-')));
    const shape={...spec};
    const failures=[];
    if(shape.cpu_cores<req.compute.cpu_cores_min) failures.push('CPU 조건 미충족');
    if(shape.memory_mib<req.compute.memory_mib_min) failures.push('메모리 조건 미충족');
    if(req.accelerator.required && (req.accelerator.type!=='GPU' || !Number.isFinite(shape.gpu_count) || shape.gpu_count<(req.accelerator.count_min??1) || !Number.isFinite(shape.gpu_memory_mib) || shape.gpu_memory_mib<(req.accelerator.memory_mib_min_per_device??0))) failures.push('GPU 조건 미충족 또는 메모리 사양 확인 필요');
    const option=policy?.options.find(o=>o.spec_id===spec.spec_id);
    const pairs=historical.flatMap(c=>c.options.filter(o=>o.spec_id===spec.spec_id));
    const past=pairs.length===1?pairs[0]:null;
    const measurement=option?{throughput:option.median_throughput,batch_p95_ms:option.p95_max_ms,independent_runs:option.independent_run_count,run_ids:option.run_ids,origin:'measured',comparison:'qualified_cohort'}:past?{throughput:past.median_throughput_examples_per_second,batch_p95_ms:past.median_latency_p95_ms,independent_runs:past.independent_run_count,run_ids:past.run_ids,cpu_core_limit:past.cpu_core_limit,origin:'measured',comparison:'historical_reference'}:null;
    const benchmark_qualified=!!option?.eligible && failures.length===0;
    const origin=upstream?resourceOrigin:'historical_shape';
    const feasible=failures.length===0;
    const selectable=feasible && !!upstream && req.deployment.replicas_min===1;
    const candidate={candidate_id:upstream?.resource.resource_id??spec.spec_id,spec_id:spec.spec_id,name:spec.name,display_name:spec.display_name,origin,shape,
      feasible,selectable,fit_state:!feasible?'unsuitable':upstream?'confirmed':'storage_unconfirmed',reason:failures.join(' · ') || (upstream?'요청한 CPU·메모리·GPU 자원 조건 충족':'과거 실험 사양 · 저장소 구성 확인 필요'),
      measurement,benchmark_qualified,price:{hourly_usd:option?.hourly_usd??null,work_usd:option?.estimated_compute_cost_usd??null,processing_seconds:option?.estimated_processing_seconds??null,checked_at_utc:cpuPolicy.price_snapshot?.checked_at_utc},
      source:{...spec.source,checked_at_utc:spec.checked_at_utc??catalog.checked_at_utc},recommended_hourly:policy?.hourlySelection?.spec_id===spec.spec_id,recommended_work:policy?.workSelection?.spec_id===spec.spec_id};
    return executionTarget==='cloud'?cloudCandidatePolicy(candidate,req):candidate;
  });
  const available=all.filter(c=>c.selectable);
  const objective=workload.objective==='work'?'recommended_work':'recommended_hourly';
  available.sort((a,b)=>Number(b[objective])-Number(a[objective]) || Number(b.benchmark_qualified)-Number(a.benchmark_qualified) || a.shape.cpu_cores-b.shape.cpu_cores || a.name.localeCompare(b.name));
  return {primary:available,all,provenance:{evidence_hash:evidence.source_state_sha256,policy_hash:cpuPolicy.source_report_sha256,resource_origin:resourceOrigin,measured_at:evidence.campaign_id,prices_fresh:policy?.pricesFresh??false,cohort_id:cohort?.cohort_id??null}};
}
