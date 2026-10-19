import test from 'node:test';import assert from 'node:assert/strict';
import {cloudIntent} from '../server/cloud-execution.mjs';
import {validateVmRegistration} from '../server/vm-appdeploy.mjs';
import {buildCandidates} from '../server/candidates.mjs';
import {profile,snapshots} from './fixtures.mjs';
import {createDemoObservationServer} from '../server/demo-observations.mjs';
const catalogModule=await import('../server/vm-catalog.mjs').catch(()=>({}));
test('A10G fallback is opt-in and never invents benchmark evidence',async()=>{
 assert.equal(typeof catalogModule.loadVmCatalog,'function');const normal=await catalogModule.loadVmCatalog({fallbackEnabled:false}),extra=await catalogModule.loadVmCatalog({fallbackEnabled:true});
 assert.equal(normal.entries.some(s=>s.name==='g5.xlarge'),false);const spec=extra.entries.find(s=>s.name==='g5.xlarge');assert.equal(spec.gpu_model,'NVIDIA A10G');assert.equal(spec.gpu_memory_mib,22888);
 const p=profile();p.requirements.accelerator={required:true,type:'GPU',count_min:1,memory_mib_min_per_device:16000};p.requirements.deployment.replicas_max=1;
 const args={applicationProfile:p,catalog:extra,cpuPolicy:snapshots.cpuPolicy,evidence:snapshots.evidence,workload:{},recommendation:{recommendations:[{resource:{resource_id:'fallback-gpu',labels:{spec_id:spec.spec_id}}}]}};
 const card=buildCandidates({...args,executionTarget:'cloud'}).all.find(s=>s.name==='g5.xlarge');assert.equal(card.selectable,true);assert.equal(card.measurement,null);assert.equal(card.benchmark_qualified,false);
 assert.equal(buildCandidates({...args,executionTarget:'historical'}).all.some(s=>s.name==='g5.xlarge'),false);
});
test('approved A10G intent uses the GPU disk and CPU output cannot pass as CUDA proof',()=>{
 const i=cloudIntent({flow_id:'flow-fallback',selection_version:1,spec_sha256:'a'.repeat(64),candidate_id:'g5.xlarge',region:'us-west-2',max_lifetime_seconds:1800},'run-fallback');assert.equal(i.root_disk_gib,100);
 const run={state:'PREPARING',intent:i,resource:{provider_instance_id:'i-0123456789abcdef0'},artifact_sha256:'b'.repeat(64)},credential={private_key:'fixture',user:'cb-user',host:'34.1.2.3',host_key_fingerprint:'SHA256:abcde'};
 const proof={state:'READY',execution_mode:'ssm_guest',management_transport:'ssm',provider_instance_id:run.resource.provider_instance_id,intent_sha256:i.intent_sha256,artifact_sha256:run.artifact_sha256,forward_finite:true,cpu_cores:4,memory_bytes:16*1024**3,storage_bytes:30*1024**3,gpu_count:1,cuda_available:true,model_device:'cuda:0',input_device:'cuda:0'};
 assert.equal(validateVmRegistration({run,credential,proof}).gpu,true);assert.throws(()=>validateVmRegistration({run,credential,proof:{...proof,model_device:'cpu'}}),/cuda_forward_required/);
});
test('optional resource input identifies A10G rather than labeling every GPU as L4',async t=>{
 const catalog=await catalogModule.loadVmCatalog({fallbackEnabled:true}),server=createDemoObservationServer({catalog});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const response=await(await fetch('http://127.0.0.1:'+server.address().port+'/api/v1/query?query='+encodeURIComponent('DCGM_FI_DEV_FB_FREE{job="dcgm"}'))).json();const gpu=response.data.result.find(r=>r.metric.spec_id==='aws+us-west-2+g5.xlarge');assert.equal(gpu.metric.modelName,'NVIDIA A10G');assert.equal(Number(gpu.value[1]),22888);assert.equal(response.source,'demo_input');
});
