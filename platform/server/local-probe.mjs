import {localHostSnapshot} from './local-host.mjs';
import {assertLocalModel,aiPython} from './local-ai.mjs';
const texts=['The movie was enjoyable and the acting was excellent.','The service was slow and the food was disappointing.','The product works well and arrived on time.','The software was unreliable and difficult to use.','The team delivered a clear and useful report.','The application response was fast and easy to understand.','The customer support experience was frustrating.','The experiment produced consistent measurement results.'];
const processBound=(v,id)=>id==='local-process-'+v?.pid||(id==='local-process-'+v?.launcher_pid&&v?.launcher_executable?.toLowerCase()===aiPython.toLowerCase());
const sameIdentity=(v,runtimeId,context)=>v?.pid!==undefined&&processBound(v,runtimeId)&&v.app==='khu-bert-tiny'&&v.model_inference===true&&v.flow_id===context.flow_id&&['model_id','model_revision','batch_size','max_length','runtime_engine','precision'].every(k=>v[k]===context.model[k]);
export async function measureLocalWorker(runtimeId,context){
  if(context)return measureModel(runtimeId,context);
  const call=async path=>{const response=await fetch('http://127.0.0.1:18160'+path,{signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error('worker_unavailable');return response.json();};
  const health=await call('/healthz');if(runtimeId!=='local-process-'+health.pid||health.app!=='khu-local-execution-worker')throw Error('worker_identity_mismatch');
  const host=await localHostSnapshot();await call('/metrics');
  const started_at=new Date().toISOString(),start=performance.now(),latencies=[];
  for(let i=0;i<64;i++){const at=performance.now();await call('/work');latencies.push(performance.now()-at);}
  const duration=(performance.now()-start)/1000,metrics=await call('/metrics');latencies.sort((a,b)=>a-b);
  return {source:'local_probe',started_at,ended_at:new Date().toISOString(),sample_count:64,runtime_id:runtimeId,scope:'HTTP execution validation worker; no model inference',metrics:{resource:{cpu_average_percent:metrics.cpu_core_percent/host.cpu_cores,memory_peak_mib:metrics.memory_rss_mib,accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:latencies[Math.ceil(64*.95)-1],throughput_rps:64/duration,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
}

async function measureModel(runtimeId,context){
  assertLocalModel(context.model);
  const base=context.base??'http://127.0.0.1:18160';
  const url=new URL(base);if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password)throw Error('invalid_worker_url');
  const call=async(path,body)=>{const response=await fetch(base+path,{signal:AbortSignal.timeout(5000),...(body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{})});if(!response.ok)throw Error('inference_failed');return response.json();};
  let health;const readyBy=Date.now()+25000;
  while(!health){try{health=await call('/healthz');}catch(error){if(error.message==='inference_failed'||Date.now()>=readyBy)throw Error('worker_not_ready');await new Promise(r=>setTimeout(r,250));}}
  if(!sameIdentity(health,runtimeId,context)||health.status!=='ready')throw Error('worker_identity_mismatch');
  const host=await localHostSnapshot(),before=await call('/metrics');
  const started_at=new Date().toISOString(),start=performance.now(),latencies=[],modelLatencies=[];
  let inputCount=0,sample;
  for(let i=0;i<64;i++){
    const batch=Array.from({length:context.model.batch_size},(_,j)=>texts[(i+j)%texts.length]);
    const at=performance.now(),result=await call('/infer',{texts:batch});latencies.push(performance.now()-at);
    if(!sameIdentity(result,runtimeId,context)||result.input_count!==batch.length||!Number.isFinite(result.inference_ms)||result.inference_ms<0||result.logits?.length!==batch.length||result.logits.some(row=>!Array.isArray(row)||row.length!==2||row.some(v=>!Number.isFinite(v)))||result.predictions?.length!==batch.length||result.predictions.some(v=>v!==0&&v!==1)||result.probabilities?.length!==batch.length||result.probabilities.some(row=>!Array.isArray(row)||row.length!==2||row.some(v=>!Number.isFinite(v)||v<0||v>1)))throw Error('inference_result_mismatch');
    modelLatencies.push(result.inference_ms);inputCount+=result.input_count;
    if(i===0)sample={texts:batch,predictions:result.predictions,logits:result.logits,probabilities:result.probabilities,token_counts:result.token_counts};
  }
  const duration=(performance.now()-start)/1000,after=await call('/metrics');
  if(!sameIdentity(before,runtimeId,context)||!sameIdentity(after,runtimeId,context)||after.completed-before.completed!==64||![before.cpu_seconds,after.cpu_seconds,after.memory_rss_mib].every(Number.isFinite))throw Error('inference_result_mismatch');
  const p95=values=>values.sort((a,b)=>a-b)[Math.ceil(values.length*.95)-1];
  return {source:'local_probe',started_at,ended_at:new Date().toISOString(),sample_count:64,input_count:inputCount,runtime_id:runtimeId,worker_pid:health.pid,launcher_pid:health.launcher_pid,model:{...context.model,seed:health.seed,threads:health.threads,head_kind:health.head_kind,versions:health.versions},flow_id:context.flow_id,scope:'64 sequential HTTP requests with actual pinned BERT Tiny CPU inference; warmed model; repeated benchmark inputs',sample,metrics:{resource:{cpu_average_percent:Math.min(100,Math.max(0,(after.cpu_seconds-before.cpu_seconds)/duration/host.cpu_cores*100)),memory_peak_mib:Math.max(before.memory_rss_mib,after.memory_rss_mib),accelerator_average_percent:0,accelerator_memory_peak_mib:0},inference:{latency_p95_ms:p95(latencies),model_inference_p95_ms:p95(modelLatencies),throughput_rps:64/duration,input_throughput:inputCount/duration,error_rate_percent:0},cost:{currency:'USD',estimated_cost:0}}};
}
