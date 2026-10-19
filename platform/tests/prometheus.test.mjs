import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {localModels} from '../server/local-ai.mjs';
const prom=await import('../server/prometheus.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const model=localModels[0],now=Math.floor(Date.now()/1000);
const receipt={flow_id:'flow-prom-test',input:{execution_target:'local',model},execution:{mode:'local_process',status:'RUNNING',execution_id:'dep-prom',target_profile_id:'khu-local-pc',runtime_id:'local-process-123'}};
const metric=(name,values,extra={})=>({metric:{__name__:name,flow_id:receipt.flow_id,model_id:model.model_id,model_revision:model.model_revision,batch_size:'1',max_length:'64',worker_pid:'123',launcher_pid:'122',...extra},values:values.map((v,i)=>[now-10+i*5,String(v)])});
function series(){return [metric('khu_app_cpu_seconds_total',[1,1.3,1.6]),metric('khu_app_memory_rss_bytes',[260*1048576,261*1048576,262*1048576]),metric('khu_app_host_cpu_cores',[12,12,12]),metric('khu_app_requests_total',[0,32,64]),metric('khu_app_request_errors_total',[0,0,0]),metric('khu_app_request_duration_seconds_bucket',[0,30,62],{le:'.01'}),metric('khu_app_request_duration_seconds_bucket',[0,32,64],{le:'.05'}),metric('khu_app_request_duration_seconds_bucket',[0,32,64],{le:'+Inf'})];}
async function fixture(t,rows=series(),up=1){
  const http=createServer((req,res)=>{const u=new URL(req.url,'http://localhost');res.setHeader('content-type','application/json');if(u.pathname==='/-/ready'){res.end('ready');return;}const result=u.pathname.endsWith('query_range')?rows:[{metric:{job:'khu-app'},value:[now,String(up)]}];res.end(JSON.stringify({status:'success',data:{resultType:u.pathname.endsWith('query_range')?'matrix':'vector',result}}));});
  await new Promise(r=>http.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>http.close(r)));return 'http://127.0.0.1:'+http.address().port;
}
test('real Prometheus samples are bound to the local model and converted into an observation window',async t=>{
  assert.equal(typeof prom.createPrometheusClient,'function');const c=prom.createPrometheusClient({base:await fixture(t),now:()=>now*1000});const result=await c.observe(receipt);
  assert.equal(result.state,'COLLECTING');assert.equal(result.observation.source,'prometheus');assert.equal(result.observation.sample_count,64);assert.equal(result.observation.metrics.inference.throughput_rps,6.4);assert.equal(result.observation.metrics.inference.error_rate_percent,0);assert.equal(result.observation.metrics.resource.memory_peak_mib,262);assert.ok(Math.abs(result.observation.metrics.resource.cpu_average_percent-.5)<1e-9);assert.ok(result.observation.metrics.inference.latency_p95_ms<=10);assert.equal(result.observation.flow_id,receipt.flow_id);assert.equal(result.can_feedback,true);
});
test('stopped apps and historical VM receipts cannot reuse the last Prometheus samples',async t=>{
  assert.equal(typeof prom.createPrometheusClient,'function');const c=prom.createPrometheusClient({base:await fixture(t),now:()=>now*1000});for(const r of [{...receipt,execution:{...receipt.execution,status:'STOPPED'}},{...receipt,input:{...receipt.input,execution_target:'historical'}}]){const result=await c.observe(r);assert.equal(result.state,'IDLE');assert.equal(result.observation,null);assert.equal(result.can_feedback,false);}
});
test('stale, down, wrong-model and wrong-process samples never become GEON inputs',async t=>{
  assert.equal(typeof prom.createPrometheusClient,'function');for(const rows of [series().map(r=>({...r,values:r.values.map(([t,v])=>[t-120,v])})),series().map(r=>({...r,metric:{...r.metric,model_revision:'other'}})),series().map(r=>({...r,metric:{...r.metric,worker_pid:'999',launcher_pid:'998'}}))]){const c=prom.createPrometheusClient({base:await fixture(t,rows),now:()=>now*1000});const result=await c.observe(receipt);assert.equal(result.can_feedback,false);assert.equal(result.observation,null);}const c=prom.createPrometheusClient({base:await fixture(t,series(),0),now:()=>now*1000});assert.equal((await c.observe(receipt)).can_feedback,false);
});
test('no requests, counter resets and insufficient scrapes do not manufacture request performance',async t=>{
  assert.equal(typeof prom.createPrometheusClient,'function');for(const rows of [series().map(r=>r.metric.__name__==='khu_app_requests_total'?{...r,values:r.values.map(([t])=>[t,'0'])}:r),series().map(r=>r.metric.__name__==='khu_app_requests_total'?{...r,values:r.values.map(([t],i)=>[t,String(64-i*32)])}:r),series().map(r=>({...r,values:r.values.slice(-1)}))]){const c=prom.createPrometheusClient({base:await fixture(t,rows),now:()=>now*1000});const result=await c.observe(receipt);assert.equal(result.can_feedback,false);assert.equal(result.observation,null);}
});
test('Prometheus base URL must remain on loopback',()=>{assert.equal(typeof prom.createPrometheusClient,'function');assert.throws(()=>prom.createPrometheusClient({base:'https://outside.example/'}),/local_endpoint_required/);});
test('a counter reset inside the window is rejected even if the final count recovers',async t=>{
  const rows=series().map(r=>r.metric.__name__==='khu_app_requests_total'?{...r,values:r.values.map(([t],i)=>[t,String([10,0,64][i])])}:r);
  const result=await prom.createPrometheusClient({base:await fixture(t,rows),now:()=>now*1000}).observe(receipt);
  assert.equal(result.state,'COUNTER_RESET');assert.equal(result.can_feedback,false);
});
test('replica monitoring retains CPU and memory during no-traffic windows but cannot submit feedback',async t=>{
 const group={group_id:'group-prom',operation_state:'IDLE',membership_epoch:1,membership_changed_at:new Date((now-30)*1000).toISOString(),actual_replicas:1,replicas:[{replica_id:'replica-prom',runtime_id:'local-process-123',status:'RUNNING'}]};
 const r={...receipt,replica_group:group};
 const rows=series().map(row=>({...row,metric:{...row.metric,group_id:group.group_id,replica_id:'replica-prom'},values:/requests_total|request_errors_total|_bucket$/.test(row.metric.__name__)?row.values.map(([t])=>[t,'64']):row.values}));
 const result=await prom.createPrometheusClient({base:await fixture(t,rows),now:()=>now*1000}).observe(r);
 assert.equal(result.state,'WAITING_REQUESTS');assert.equal(result.metrics.resource.memory_peak_mib,262);assert.ok(result.metrics.resource.cpu_average_percent>0);assert.equal(result.metrics.inference,undefined);assert.equal(result.observation,null);assert.equal(result.can_feedback,false);assert.equal(result.points.length,2);assert.ok(result.collected_at);
});
