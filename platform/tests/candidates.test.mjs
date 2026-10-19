import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';

const mod = await import('../server/candidates.mjs').catch(e => { if(e.code==='ERR_MODULE_NOT_FOUND') return {}; throw e; });
const policy = JSON.parse(await readFile(new URL('../data/khu-cpu-policy.json',import.meta.url)));
const evidence = JSON.parse(await readFile(new URL('../data/khu-campaign-evidence.json',import.meta.url)));
const cohort = policy.cohorts[0];
function args(overrides={}) {
  return {applicationProfile:{requirements:{compute:{cpu_cores_min:2,memory_mib_min:2048,storage_gib_min:20},accelerator:{required:false},deployment:{replicas_min:1}}},
    recommendation:{recommendations:[]},workload:{...cohort,min_throughput:1,max_batch_p95_ms:1000,input_count:1000000},cpuPolicy:policy,evidence,
    nowUtc:policy.price_snapshot.checked_at_utc,...overrides};
}
async function build(input=args()) {
  assert.equal(typeof mod.buildCandidates,'function','Candidate summaries are not implemented');
  const catalog=JSON.parse(await readFile(new URL('../data/khu-vm-spec-catalog.json',import.meta.url)));
  return mod.buildCandidates({...input,catalog});
}
test('keeps five historical shapes distinct from available VMs',async()=>{
  const result=await build(); assert.equal(result.all.length,5);
  assert.equal(result.all.filter(c=>c.benchmark_qualified).length,2);
  assert.ok(result.all.every(c=>c.origin==='historical_shape' && !c.selectable && c.source.url));
});
test('GPU requirement filters CPU shapes without fabricating GPU performance rank',async()=>{
  const input=args(); input.applicationProfile.requirements.accelerator={required:true,type:'GPU',count_min:1,memory_mib_min_per_device:12000};
  const result=await build(input);
  assert.ok(result.all.filter(c=>c.shape.gpu_count===0).every(c=>!c.feasible));
  assert.ok(result.all.filter(c=>c.shape.gpu_count===1).every(c=>!c.benchmark_qualified));
});
test('different model revision cannot reuse matching cohort measurements',async()=>{
  const input=args(); input.workload.model_revision='different';
  assert.ok((await build(input)).all.every(c=>c.measurement===null && !c.benchmark_qualified));
});
test('expired prices keep hardware information but withhold cost',async()=>{
  const result=await build(args({nowUtc:'2099-01-01T00:00:00Z'}));
  assert.ok(result.all.every(c=>c.price.hourly_usd===null));
  assert.ok(result.all.some(c=>c.feasible && c.measurement));
});
test('unknown storage cannot claim historical shape fully confirmed',async()=>{
  assert.ok((await build()).all.filter(c=>!c.shape.storage_gib).every(c=>c.fit_state==='storage_unconfirmed' || !c.feasible));
});
test('unknown VRAM cannot qualify a GPU candidate',async()=>{const spec=JSON.parse(await readFile(new URL('../data/khu-vm-spec-catalog.json',import.meta.url)));delete spec.entries.find(e=>e.name==='g6.2xlarge').gpu_memory_mib;const input=args();input.applicationProfile.requirements.accelerator={required:true,type:'GPU',count_min:1,memory_mib_min_per_device:16000};const result=mod.buildCandidates({...input,catalog:spec});assert.equal(result.all.find(c=>c.name==='g6.2xlarge').feasible,false);});

async function fullRecommendation(){
  const catalog=JSON.parse(await readFile(new URL('../data/khu-vm-spec-catalog.json',import.meta.url)));
  return {recommendations:catalog.entries.map(spec=>({resource:{resource_id:'demo-'+spec.name.replaceAll('.','-'),labels:{spec_id:spec.spec_id}}}))};
}
test('cloud CPU execution does not offer GPU runtime candidates as selectable even when hardware fits',async()=>{
  const result=await build(args({executionTarget:'cloud',recommendation:await fullRecommendation()}));
  assert.ok(result.primary.every(c=>!c.shape.gpu_count));
  const gpu=result.all.find(c=>c.name==='g6.2xlarge');
  assert.equal(gpu.feasible,true);assert.equal(gpu.selectable,false);
  assert.equal(gpu.execution_eligible,false);assert.match(gpu.execution_reason,/CPU.*GPU|GPU.*CPU/);
});
test('all five selectable VM shapes reach the main list without hiding GPUs behind a three-card limit',async()=>{
  const result=await build(args({recommendation:await fullRecommendation()}));
  assert.equal(result.primary.length,5);
  assert.equal(result.primary.filter(c=>c.shape.gpu_count===0).length,3);
  assert.equal(result.primary.filter(c=>c.shape.gpu_count===1).length,2);
  assert.ok(result.primary.every(c=>c.selectable));
});
test('a GPU-only request keeps CPU shapes as excluded detail rather than selectable recommendations',async()=>{
  const input=args({recommendation:await fullRecommendation()});
  input.applicationProfile.requirements.accelerator={required:true,type:'GPU',count_min:1,memory_mib_min_per_device:16000};
  const result=await build(input);
  assert.deepEqual(result.primary.map(c=>c.name),['g6.2xlarge','g6.8xlarge']);
  assert.equal(result.all.filter(c=>!c.selectable).length,3);
});
