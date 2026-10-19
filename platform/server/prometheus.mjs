const localError=code=>Object.assign(Error(code),{status:409,code});
const finite=v=>Number.isFinite(Number(v));
export function createPrometheusClient({base=process.env.KHU_PROMETHEUS_URL??'http://127.0.0.1:18190',now=Date.now}={}){
  const url=new URL(base);if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.pathname!=='/'||url.username||url.password||url.search||url.hash)throw Error('local_endpoint_required');
  const get=async(path,params)=>{const u=new URL(path,base);for(const [k,v] of Object.entries(params??{}))u.searchParams.set(k,v);const response=await fetch(u,{signal:AbortSignal.timeout(3000)});if(!response.ok)throw Error('prometheus_unavailable');if(path==='/-/ready')return true;const body=await response.json();if(body.status!=='success'||!Array.isArray(body.data?.result))throw Error('prometheus_unavailable');return body.data.result;};
  async function observe(r){
    const result={state:'IDLE',server_ready:false,flow_id:r?.flow_id??null,can_feedback:false,observation:null,metrics:null,points:[],scrape_interval_seconds:5,window_seconds:60};
    try{await get('/-/ready');result.server_ready=true;}catch{return {...result,state:'UNAVAILABLE'};}
    if(!['local','cloud'].includes(r?.input?.execution_target)||!['local_process','cloud_vm'].includes(r.execution?.mode)||r.execution.status!=='RUNNING')return result;
    if(r.replica_group){
      const g=r.replica_group,children=g.replicas.filter(c=>c.status==='RUNNING'),end=Math.floor(now()/1000);
      if(g.operation_state!=='IDLE'||children.length!==g.actual_replicas)return {...result,state:'IDENTITY_MISMATCH'};
      try{
        const [up,rows]=await Promise.all([get('/api/v1/query',{query:'up{job="khu-app",khu_group_id='+JSON.stringify(g.group_id)+'}',time:end}),get('/api/v1/query_range',{query:'{__name__=~"khu_app_.*",flow_id='+JSON.stringify(r.flow_id)+',group_id='+JSON.stringify(g.group_id)+'}',start:Math.max(end-60,Math.floor(Date.parse(g.membership_changed_at)/1000)+1),end,step:5})]);
        if(up.length!==children.length||up.some(row=>Number(row.value?.[1])!==1||end-Number(row.value?.[0])>15))return {...result,state:'DOWN'};
        if(rows.some(row=>{const c=children.find(c=>c.replica_id===row.metric.replica_id),b=r.worker_bindings?.[row.metric.replica_id],processMatch=r.execution.mode==='cloud_vm'?b&&String(b.pid)===row.metric.worker_pid&&String(b.worker_started_at)===row.metric.worker_started_at:['local-process-'+row.metric.worker_pid,'local-process-'+row.metric.launcher_pid].includes(c?.runtime_id);return !c||!processMatch||['model_id','model_revision','batch_size','max_length'].some(k=>String(row.metric[k])!==String(r.input.model[k]));}))return {...result,state:'IDENTITY_MISMATCH'};
        const combined=aggregateReplicaWindow(rows,g,{allowIdle:true});if(end-Date.parse(combined.ended_at)/1000>15)return {...result,state:'STALE'};
        result.metrics=combined.metrics;result.points=combined.points;result.collected_at=combined.ended_at;
        if(combined.sample_count===0)return {...result,state:'WAITING_REQUESTS'};
        const observation={...combined,source:'prometheus',flow_id:r.flow_id,group_id:g.group_id,model:r.input.model,scope:'Same-membership Prometheus window; merged histogram bucket deltas; CPU normalized by host cores; RSS sum; server p95 excludes client/proxy latency'};
        return {...result,state:'COLLECTING',can_feedback:true,metrics:combined.metrics,observation};
      }catch(e){return {...result,state:e.message==='waiting_requests'?'WAITING_REQUESTS':e.message==='counter_reset'?'COUNTER_RESET':'WAITING_SAMPLES'};}
    }
    const model=r.input.model,runtime=r.execution.runtime_id??r.appdeploy?.delivery?.deployment?.runtime_id;
    if(!/^flow-[\w-]+$/.test(r.flow_id)||!/^local-process-\d+$/.test(runtime??''))return {...result,state:'IDENTITY_MISMATCH'};
    const end=Math.floor(now()/1000),selector='{__name__=~"khu_app_.*",flow_id='+JSON.stringify(r.flow_id)+'}';
    let up,rows;try{[up,rows]=await Promise.all([get('/api/v1/query',{query:'up{job="khu-app"}',time:end}),get('/api/v1/query_range',{query:selector,start:end-60,end,step:5})]);}catch{return {...result,state:'UNAVAILABLE'};}
    if(up.length!==1||Number(up[0].value?.[1])!==1||end-Number(up[0].value?.[0])>15)return {...result,state:'DOWN'};
    if(!rows.length)return {...result,state:'WAITING_SAMPLES'};
    const identity=m=>m.flow_id===r.flow_id&&m.model_id===model.model_id&&m.model_revision===model.model_revision&&Number(m.batch_size)===model.batch_size&&Number(m.max_length)===model.max_length&&['local-process-'+m.worker_pid,'local-process-'+m.launcher_pid].includes(runtime);
    if(rows.some(row=>!identity(row.metric)))return {...result,state:'IDENTITY_MISMATCH'};
    const metric=name=>rows.find(row=>row.metric.__name__===name)?.values??[];
    const requests=metric('khu_app_requests_total'),cpu=metric('khu_app_cpu_seconds_total'),memory=metric('khu_app_memory_rss_bytes'),cores=metric('khu_app_host_cpu_cores'),errors=metric('khu_app_request_errors_total');
    if(![requests,cpu,memory,cores,errors].every(a=>a.length>=2&&a.every(([t,v])=>finite(t)&&finite(v)&&Number(v)>=0)))return {...result,state:'WAITING_SAMPLES'};
    if([requests,cpu,errors,...rows.filter(row=>row.metric.__name__==='khu_app_request_duration_seconds_bucket').map(row=>row.values)].some(a=>a.some(([,v],i)=>i>0&&Number(v)<Number(a[i-1][1]))))return {...result,state:'COUNTER_RESET'};
    const first=Number(requests[0][0]),last=Number(requests.at(-1)[0]),seconds=last-first;
    if(seconds<5||last>end||end-last>15)return {...result,state:'STALE'};
    const at=(a,t)=>Number(a.find(([s])=>Number(s)===t)?.[1]);
    const delta=a=>at(a,last)-at(a,first),hostCores=at(cores,last),count=delta(requests),errorCount=delta(errors),cpuSeconds=delta(cpu);
    if(![count,errorCount,cpuSeconds,hostCores].every(Number.isFinite)||hostCores<1||count<0||errorCount<0||errorCount>count||cpuSeconds<0)return {...result,state:'COUNTER_RESET'};
    const resource={cpu_average_percent:Math.min(100,cpuSeconds/seconds/hostCores*100),memory_peak_mib:Math.max(...memory.map(v=>Number(v[1])))/1048576,accelerator_average_percent:0,accelerator_memory_peak_mib:0};
    result.metrics={resource};result.collected_at=new Date(last*1000).toISOString();
    result.points=cpu.slice(1).map(([t,v],i)=>{const prev=cpu[i],dt=Number(t)-Number(prev[0]);return {at:new Date(Number(t)*1000).toISOString(),cpu_percent:Math.max(0,Math.min(100,(Number(v)-Number(prev[1]))/dt/hostCores*100)),memory_mib:at(memory,Number(t))/1048576};}).filter(v=>Number.isFinite(v.memory_mib));
    if(!Number.isInteger(count)||!Number.isInteger(errorCount)||count===0)return {...result,state:'WAITING_REQUESTS'};
    const buckets=rows.filter(row=>row.metric.__name__==='khu_app_request_duration_seconds_bucket').map(row=>({bound:row.metric.le==='+Inf'?Infinity:Number(row.metric.le),count:delta(row.values)})).sort((a,b)=>a.bound-b.bound);
    if(!buckets.length||buckets.at(-1).bound!==Infinity||buckets.at(-1).count!==count||buckets.some((b,i)=>Number.isNaN(b.bound)||b.bound<=0||!Number.isFinite(b.count)||b.count<0||(i&&(b.count<buckets[i-1].count||b.bound<=buckets[i-1].bound))))return {...result,state:'WAITING_SAMPLES'};
    let previous={bound:0,count:0},p95;
    for(const bucket of buckets){if(bucket.count>=count*.95){p95=Number.isFinite(bucket.bound)?previous.bound+(bucket.bound-previous.bound)*(count*.95-previous.count)/(bucket.count-previous.count):previous.bound;break;}previous=bucket;}
    if(!Number.isFinite(p95))return {...result,state:'WAITING_SAMPLES'};
    const observation={source:'prometheus',flow_id:r.flow_id,runtime_id:runtime,model:{...model},started_at:new Date(first*1000).toISOString(),ended_at:new Date(last*1000).toISOString(),sample_count:count,scope:'AppDeploy local app; Prometheus scrape window; server request p95 estimated from histogram buckets; excludes client network latency',metrics:{resource,inference:{latency_p95_ms:p95*1000,throughput_rps:count/seconds,error_rate_percent:errorCount/count*100},cost:{currency:'USD',estimated_cost:0}}};
    return {...result,state:'COLLECTING',can_feedback:true,metrics:observation.metrics,observation};
  }
  return {observe,async requireObservation(r){const result=await observe(r);if(!result.can_feedback)throw localError('prometheus_observation_not_ready');return result.observation;}};
}
import {aggregateReplicaWindow} from './replica-metrics.mjs';
