const fail=code=>{throw Error(code);};
export function aggregateReplicaWindow(rows,g,{allowIdle=false}={}){
 const children=g.replicas.filter(c=>c.status==='RUNNING'),changed=Date.parse(g.membership_changed_at)/1000;
 if(!children.length||!Number.isFinite(changed))fail('waiting_samples');
 const names=['khu_app_requests_total','khu_app_request_errors_total','khu_app_cpu_seconds_total','khu_app_memory_rss_bytes','khu_app_host_cpu_cores'];
 const gpu=rows.some(row=>row.metric.device==='cuda');if(gpu)names.push('khu_app_gpu_utilization_percent','khu_app_gpu_memory_used_mib');
 const series=children.flatMap(c=>{const own=rows.filter(row=>row.metric.replica_id===c.replica_id),required=names.map(n=>{const found=own.filter(row=>row.metric.__name__===n);if(found.length!==1)fail('waiting_samples');return found[0];});const buckets=own.filter(row=>row.metric.__name__==='khu_app_request_duration_seconds_bucket');if(!buckets.length)fail('waiting_samples');return [...required,...buckets];});
 let common=series[0].values.map(([t])=>Number(t)).filter(t=>t>changed);
 for(const row of series){if(row.values.some(([t,v])=>!Number.isFinite(Number(t))||!Number.isFinite(Number(v))||Number(v)<0))fail('invalid_metrics');common=common.filter(t=>row.values.some(([u])=>Number(u)===t));if(/_total$|_bucket$/.test(row.metric.__name__)&&row.values.filter(([t])=>Number(t)>changed).some(([,v],i,a)=>i>0&&Number(v)<Number(a[i-1][1])))fail('counter_reset');}
 common.sort((a,b)=>a-b);if(common.length<2)fail('waiting_samples');
 const first=common[0],last=common.at(-1),seconds=last-first;if(seconds<5)fail('waiting_samples');
 const at=(row,t)=>Number(row.values.find(([u])=>Number(u)===t)[1]),delta=row=>at(row,last)-at(row,first),by=n=>series.filter(row=>row.metric.__name__===n),sumDelta=n=>by(n).reduce((s,row)=>s+delta(row),0);
 const count=sumDelta(names[0]),errors=sumDelta(names[1]),cpu=sumDelta(names[2]),cores=by(names[4]).map(row=>at(row,last));
 if(cores.some(n=>n<1)||cores.some(n=>n!==cores[0])||!Number.isInteger(count)||!Number.isInteger(errors)||errors>count)fail('invalid_metrics');if(count===0&&!allowIdle)fail('waiting_requests');
 const buckets=new Map();let expectedBounds;
 for(const child of children){const own=series.filter(row=>row.metric.replica_id===child.replica_id&&row.metric.__name__==='khu_app_request_duration_seconds_bucket');const bounds=own.map(row=>row.metric.le).sort();if(expectedBounds&&JSON.stringify(bounds)!==expectedBounds)fail('bucket_alignment');expectedBounds=JSON.stringify(bounds);for(const row of own)buckets.set(row.metric.le,(buckets.get(row.metric.le)??0)+delta(row));}
 const sorted=[...buckets].map(([le,count])=>({bound:le==='+Inf'?Infinity:Number(le),count})).sort((a,b)=>a.bound-b.bound);
 if(sorted.at(-1).bound!==Infinity||sorted.at(-1).count!==count||sorted.some((v,i)=>v.count<0||!Number.isFinite(v.count)||Number.isNaN(v.bound)||(i&&v.count<sorted[i-1].count)))fail('bucket_alignment');
 const resource={cpu_average_percent:Math.min(100,cpu/seconds/cores[0]*100),memory_peak_mib:Math.max(...common.map(t=>by(names[3]).reduce((s,row)=>s+at(row,t),0)))/1048576,accelerator_average_percent:gpu?common.reduce((s,t)=>s+by(names[5]).reduce((a,row)=>a+at(row,t),0)/children.length,0)/common.length:0,accelerator_memory_peak_mib:gpu?Math.max(...common.map(t=>by(names[6]).reduce((s,row)=>s+at(row,t),0))):0};
 const points=common.slice(1).map((t,i)=>{const previous=common[i],dt=t-previous;return {at:new Date(t*1000).toISOString(),cpu_percent:Math.max(0,Math.min(100,by(names[2]).reduce((sum,row)=>sum+at(row,t)-at(row,previous),0)/dt/cores[0]*100)),memory_mib:by(names[3]).reduce((sum,row)=>sum+at(row,t),0)/1048576};});
 const window={membership_epoch:g.membership_epoch,started_at:new Date(first*1000).toISOString(),ended_at:new Date(last*1000).toISOString(),sample_count:count,points,metrics:{resource}};
 if(count===0)return window;
 let previous={bound:0,count:0},p95;
 for(const b of sorted){if(b.count>=count*.95){p95=Number.isFinite(b.bound)?previous.bound+(b.bound-previous.bound)*(count*.95-previous.count)/(b.count-previous.count):previous.bound;break;}previous=b;}
 return {...window,metrics:{resource,inference:{latency_p95_ms:p95*1000,throughput_rps:count/seconds,error_rate_percent:errors/count*100},cost:{currency:'USD',estimated_cost:0}}};
}
