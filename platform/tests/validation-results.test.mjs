import nodeTest from 'node:test';
import {existsSync} from 'node:fs';
// Optional historical-evidence integration checks require the original files.
const evidenceAvailable=['cpu-result-2026-10-16T08-59-14-944Z.json','gpu-result-2026-10-16T16-22-47-089Z.json','campaign-result-2026-10-16T17-15-32-712Z.json'].every(f=>existsSync(new URL('../../output/khu-platform-validation/cloud-live-validation/'+f,import.meta.url)));
const test=(name,run)=>nodeTest(name,{skip:!evidenceAvailable},run);
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {createPlatformServer} from '../server/index.mjs';
const results=await import('../server/validation-results.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const directory=resolve('output/khu-platform-validation/cloud-live-validation');
const files=['cpu-result-2026-10-16T08-59-14-944Z.json','gpu-result-2026-10-16T16-22-47-089Z.json','campaign-result-2026-10-16T17-15-32-712Z.json'];
const load=options=>{assert.equal(typeof results.loadValidationResults,'function','saved AWS evidence must be loaded without cloud execution');return results.loadValidationResults({directory,...options});};

test('saved CPU and GPU evidence keeps historical success separate from current Flow state and distinct p95 metrics',async()=>{
 const view=await load({readFlow:async id=>({flow_id:id,execution:{status:'STOPPED',actual_replicas:0},replica_group:{actual_replicas:1},cloud:{state:'CLEANED'}})});
 const [cpu,gpu,campaign]=view.results;
 assert.equal(cpu.status,'verified');assert.equal(gpu.status,'verified');assert.equal(campaign.status,'verified');
 assert.equal(cpu.windows[0].sample_count,64);assert.equal(gpu.windows[0].sample_count,64);
 assert.equal(cpu.windows[0].http_p95_ms,1662.0063999999547);assert.equal(cpu.windows[0].model_p95_ms,3.3345089999556876);
 assert.equal(gpu.device,'cuda:0');assert.equal(gpu.gpu_name,'NVIDIA A10G');
 assert.equal(cpu.current[0].execution_status,'STOPPED');assert.equal(cpu.current[0].replicas,0);
 assert.equal(cpu.historical,true);assert.equal(view.read_only,true);
});

test('VM count history preserves the four provider observations and all four measurement windows',async()=>{
 const {results:[,,c]}=await load();assert.deepEqual(c.timeline.map(s=>s.running_vm_count),[1,2,1,0]);
 assert.equal(c.sample_count,256);assert.equal(c.windows.length,4);
 assert.deepEqual(c.windows.map(w=>w.prometheus_sample_count),[64,64,64,62]);
 assert.equal(c.timeline[1].children.filter(r=>r.provider_state==='running').length,2);
 assert.equal(c.timeline[0].checked_at,'2026-10-16T17:20:55.354Z');
 assert.equal(c.automatic_vm_scaling,false);
});

test('missing, malformed, or insufficient device evidence cannot become a successful result',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-results-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 await writeFile(join(dir,files[0]),'{bad');
 const gpu=JSON.parse(await readFile(join(directory,files[1]),'utf8'));gpu.measurement.sample.model_device='cpu';
 await writeFile(join(dir,files[1]),JSON.stringify(gpu));
 const view=await load({directory:dir});assert.deepEqual(view.results.map(r=>r.status),['unavailable','incomplete','unavailable']);
 assert.equal(view.results[1].checks.find(c=>c.key==='device').passed,false);
});

test('a claimed campaign pass with missing cleanup, wrong provider count, or an unrelated measurement is incomplete',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-results-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const raw=JSON.parse(await readFile(join(directory,files[2]),'utf8'));
 for(const change of [d=>{d.child_cleanup[1].cloud.cleanup.attachments.remaining=['vol-other'];},d=>{d.snapshots[1].running_vm_count=1;},d=>{d.measurements[2].instance_id='i-unrelated';}]){
  const altered=structuredClone(raw);change(altered);await writeFile(join(dir,files[2]),JSON.stringify(altered));
  assert.equal((await load({directory:dir})).results[2].status,'incomplete');
 }
});

test('undefined or mismatched provider IDs cannot prove a real single VM deployment',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-results-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const raw=JSON.parse(await readFile(join(directory,files[0]),'utf8'));
 delete raw.provider_instance_id;delete raw.bootstrap_proof.provider_instance_id;delete raw.provider_status.instance_id;
 await writeFile(join(dir,files[0]),JSON.stringify(raw));
 const r=(await load({directory:dir})).results[0];assert.equal(r.status,'incomplete');assert.equal(r.checks.find(c=>c.key==='deployment').passed,false);
});

test('campaign validation rejects unrelated Prometheus observations and incorrect per-VM state transitions',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-results-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const raw=JSON.parse(await readFile(join(directory,files[2]),'utf8'));
 for(const change of [d=>{for(const p of d.prometheus){p.observation.source='demo_input';p.observation.flow_id='flow-unrelated';}},
  d=>{for(const c of d.snapshots[3].children)c.provider_state='unknown';},
  d=>{d.snapshots[2].children[0].provider_state='Terminated';d.snapshots[2].children[1].provider_state='running';},
  d=>{d.measurements[0].bootstrap_proof.provider_instance_id='i-unrelated';}]){
  const altered=structuredClone(raw);change(altered);await writeFile(join(dir,files[2]),JSON.stringify(altered));
  assert.equal((await load({directory:dir})).results[2].status,'incomplete');
 }
});

test('a missing campaign error metric is unknown rather than zero errors',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'khu-results-'));t.after(()=>rm(dir,{recursive:true,force:true}));
 const raw=JSON.parse(await readFile(join(directory,files[2]),'utf8'));delete raw.measurements[0].measurement.metrics.inference.error_rate_percent;
 await writeFile(join(dir,files[2]),JSON.stringify(raw));
 const r=(await load({directory:dir})).results[2];assert.equal(r.status,'incomplete');assert.equal(r.error_rate_percent,null);
});

test('results API is read-only, tolerates unavailable current Flows, and serves only the fixed evidence selection',async t=>{
 const server=createPlatformServer({store:{read:async()=>{throw Error('flow unavailable');}}});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const url=`http://127.0.0.1:${server.address().port}/api/v1/validation-results`;
 const response=await fetch(url+'?file=../../secret');assert.equal(response.status,200);
 const body=await response.json();assert.equal(body.results.length,3);assert.equal(body.results[0].status,'verified');
 assert.equal(body.results[0].current[0].available,false);assert.equal(body.read_only,true);
 assert.equal((await fetch(url,{method:'POST'})).status,405);
});
