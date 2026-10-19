import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createAppDeployClient,workerArtifact} from '../server/appdeploy-client.mjs';
import {profile,input} from './fixtures.mjs';
import {createPlatformServer} from '../server/index.mjs';
import {createWorkflowStore} from '../server/workflow-store.mjs';
import {clients,catalog,snapshots} from './fixtures.mjs';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

test('local execution version is bound before LLM_Op and preserved through GEON and AppDeploy',async t=>{
  let analyzed;const p=profile();
  const upstream=clients({profile:async value=>{analyzed=value;return {application_profile:{...p,app_version:value.app_spec.app_version},analysis:{mode:'structured'}};}});
  const dir=await mkdtemp(join(tmpdir(),'khu-ai-'));
  const server=createPlatformServer({clients:upstream,store:createWorkflowStore(dir),catalog,snapshots});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const value={...input(),execution_target:'local'};
  const response=await fetch(`http://127.0.0.1:${server.address().port}/api/v1/flows`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(value)});
  assert.equal(response.status,201);const receipt=await response.json();
  assert.match(analyzed.app_spec.app_version,/^1\.0\.0-poc\./);
  assert.equal(receipt.input.application.app_version,receipt.application_profile.app_version);
  assert.equal(receipt.requested_application.app_version,'1.0.0');
});

test('AppDeploy launches the selected pinned model with the flow identity',async t=>{
  let spec;const server=createServer(async(req,res)=>{let raw='';for await(const c of req)raw+=c;res.setHeader('content-type','application/json');if(req.method==='GET')return res.end('{"apps":[]}');spec=JSON.parse(raw).app_spec;res.end('{"app_version_id":"ai-test"}');});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const p=profile();p.artifact=workerArtifact;p.app_version='1.0.0-poc.contract';
  await createAppDeployClient({base:`http://127.0.0.1:${server.address().port}`}).registerLocalApp(p,{model:input().model,flow_id:'flow-ai-contract'});
  assert.match(spec.entrypoint.command,/python(?:\.exe)?$/i);
  const args=spec.entrypoint.args;
  assert.equal(args[args.indexOf('--model-id')+1],input().model.model_id);
  assert.equal(args[args.indexOf('--revision')+1],input().model.model_revision);
  assert.equal(args[args.indexOf('--flow-id')+1],'flow-ai-contract');
  assert.match(spec.metadata.version,/^1\.0\.0-poc\./);
});

test('unsupported model cannot be registered as the fixed BERT Tiny worker',async t=>{
  let calls=0;const server=createServer((req,res)=>{calls++;res.end('{}');});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const p=profile();p.artifact=workerArtifact;
  const client=createAppDeployClient({base:`http://127.0.0.1:${server.address().port}`});
  await assert.rejects(()=>client.registerLocalApp(p,{model:{...input().model,model_id:'unknown/model'},flow_id:'flow-ai-contract'}),/unsupported_local_model/);
  assert.equal(calls,0);
});

test('inference probe refuses a process running another model before sending requests',async t=>{
  const {measureLocalWorker}=await import('../server/local-probe.mjs');let requests=0;
  const server=createServer((req,res)=>{requests++;res.setHeader('content-type','application/json');res.end(JSON.stringify({status:'ready',pid:123,app:'khu-bert-tiny',model_inference:true,model_id:'other/model',model_revision:input().model.model_revision,flow_id:'flow-ai-contract',batch_size:1,max_length:64}));});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  await assert.rejects(()=>measureLocalWorker('local-process-123',{model:input().model,flow_id:'flow-ai-contract',base:`http://127.0.0.1:${server.address().port}`}),/worker_identity_mismatch/);
  assert.equal(requests,1);
});

test('Windows venv worker binds its actual PID to the AppDeploy launcher PID',async t=>{
  const {measureLocalWorker}=await import('../server/local-probe.mjs');
  const {aiPython}=await import('../server/local-ai.mjs');
  const identity={pid:456,launcher_pid:123,launcher_executable:aiPython,app:'khu-bert-tiny',model_inference:true,flow_id:'flow-ai-contract',...input().model};
  let count=0;
  const server=createServer((req,res)=>{res.setHeader('content-type','application/json');
    if(req.url==='/healthz')return res.end(JSON.stringify({...identity,status:'ready'}));
    if(req.url==='/metrics')return res.end(JSON.stringify({...identity,completed:count,cpu_seconds:count/1000,memory_rss_mib:100}));
    count++;res.end(JSON.stringify({...identity,input_count:1,inference_ms:1,logits:[[.1,.2]],predictions:[1],probabilities:[[.4,.6]]}));
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const observed=await measureLocalWorker('local-process-123',{model:input().model,flow_id:'flow-ai-contract',base:`http://127.0.0.1:${server.address().port}`});
  assert.equal(observed.worker_pid,456);assert.equal(observed.sample_count,64);
});
