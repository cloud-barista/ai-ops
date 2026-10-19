import test from 'node:test';import assert from 'node:assert/strict';import {createServer} from 'node:http';
import {createAppDeployClient,workerArtifact} from '../server/appdeploy-client.mjs';import {profile} from './fixtures.mjs';
test('AppSpec registration uses AppDeploy TCP port contract and the fixed worker entrypoint',async t=>{
  let registered;const server=createServer(async(req,res)=>{let raw='';for await(const chunk of req)raw+=chunk;res.setHeader('content-type','application/json');if(req.method==='GET')return res.end(JSON.stringify({apps:[]}));registered=JSON.parse(raw).app_spec;res.end(JSON.stringify({app_version_id:'appver-contract'}));});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const client=createAppDeployClient({base:`http://127.0.0.1:${server.address().port}`}),p=profile();p.artifact=workerArtifact;await client.registerLocalApp(p);
  assert.equal(registered.network.ports[0].protocol,'TCP');assert.equal(registered.network.ports[0].app_port,18160);assert.equal(registered.entrypoint.command,process.execPath);assert.equal(registered.artifact.type,'script');assert.equal(registered.healthcheck.type,'http');
});
test('owned AWS VM registration uses AppDeploy Cloud target type for CPU and GPU',async t=>{
  const targets=[];
  const server=createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    res.setHeader('content-type','application/json');
    const body=JSON.parse(raw);
    if(req.url==='/api/v1/credentials'){res.statusCode=201;return res.end(JSON.stringify({credential_ref:'cred://runtime/test-key'}));}
    if(req.url==='/api/v1/target-profiles'){
      targets.push(body);
      if(body.vm_type!=='Cloud'){res.statusCode=400;return res.end(JSON.stringify({error_code:'target_profile_invalid'}));}
      res.statusCode=201;return res.end(JSON.stringify({target_profile_id:body.target_profile_id}));
    }
    if(req.url==='/api/v1/apps'){res.statusCode=201;return res.end(JSON.stringify({app_version_id:'appver-test'}));}
    res.statusCode=404;res.end('{}');
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const client=createAppDeployClient({base:`http://127.0.0.1:${server.address().port}`});
  for(const gpu of [false,true]){
    const run={run_id:`run-${gpu?'gpu':'cpu'}-registration`,intent:{candidate_id:gpu?'g6.2xlarge':'m6a.2xlarge',region:'us-west-2'}};
    const receipt={flow_id:'flow-test',input:{model:{model_id:'test',model_revision:'rev',batch_size:1,max_length:16}},application_profile:{app_id:`test-${gpu?'gpu':'cpu'}`,app_version:'1',requirements:{compute:{cpu_cores_min:2,memory_mib_min:2048,storage_gib_min:20}}}};
    const credential={user:'ubuntu',host:'198.51.100.10',ssh_port:22,host_key_fingerprint:'SHA256:test',private_key:'fixture-only'};
    const proof={root:'/opt/khu',cpu_cores:8,memory_bytes:16*1024**3,storage_bytes:30*1024**3,gpu_count:gpu?1:0,gpu_name:gpu?'NVIDIA TEST':''};
    const registered=await client.registerVmApp({receipt,run,credential,proof,artifact:{artifact:{uri:'file:///opt/khu/worker.py'}},gpu});
    assert.equal(registered.target.target_profile_id,`khu-vm-${gpu?'gpu':'cpu'}-registration`);
  }
  assert.deepEqual(targets.map(t=>t.vm_type),['Cloud','Cloud']);
  assert.deepEqual(targets.map(t=>t.runtime.runtime_type),['cpu','gpu']);
  // AppDeploy placement compares this field to a vendor, not a device model.
  assert.equal(targets[1].capacity.gpu_type,'nvidia');
  assert.equal(targets[1].labels.gpu_model,'NVIDIA TEST');
});
test('AppDeploy rejection reports only a fixed operation and HTTP status',async t=>{
  const server=createServer(async(req,res)=>{for await(const _ of req){};res.statusCode=400;res.setHeader('content-type','application/json');res.end(JSON.stringify({message:'private_key=DO_NOT_PRINT'}));});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
  const client=createAppDeployClient({base:`http://127.0.0.1:${server.address().port}`});
  await assert.rejects(client.registerVmApp({run:{run_id:'run-test'},credential:{private_key:'DO_NOT_PRINT'}}),e=>{
    assert.equal(e.message,'provider_unavailable');
    assert.deepEqual(e.appdeploy_diagnostic,{operation:'credentials',http_status:400});
    assert.equal(JSON.stringify(e).includes('DO_NOT_PRINT'),false);
    return true;
  });
});
test('new experiments retain the approved logical app identity with separate registry versions and VM entrypoints',async t=>{
 const registered=new Map();
 const server=createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);res.setHeader('content-type','application/json');
  if(req.url==='/api/v1/credentials')return res.end(JSON.stringify({credential_ref:'cred://runtime/'+body.credential_id}));
  if(req.url==='/api/v1/target-profiles')return res.end(JSON.stringify({target_profile_id:body.target_profile_id}));
  const spec=body.app_spec,key=spec.metadata.name+'@'+spec.metadata.version;
  if(registered.has(key)){res.statusCode=400;return res.end(JSON.stringify({error:{code:'APP_SPEC_INVALID'}}));}
  registered.set(key,spec);res.end(JSON.stringify({app_version_id:'appver-'+registered.size}));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const client=createAppDeployClient({base:`http://127.0.0.1:${server.address().port}`});
 const receipt={flow_id:'flow-same',input:{model:{model_id:'test',model_revision:'rev',batch_size:1,max_length:64}},application_profile:{app_id:'workshop-application',app_version:'1',requirements:{compute:{cpu_cores_min:2,memory_mib_min:2048,storage_gib_min:20}}}};
 const credential={user:'ubuntu',host:'198.51.100.10',ssh_port:22,host_key_fingerprint:'SHA256:test',private_key:'fixture-only'};
 const results=[];
 for(const id of ['first','second'])results.push(await client.registerVmApp({receipt:{...receipt,application_profile:{...receipt.application_profile,app_version:'1-poc.'+id}},run:{run_id:'run-'+id,intent:{candidate_id:'m6a.2xlarge',region:'us-west-2'}},credential,proof:{root:'/var/tmp/'+id,cpu_cores:8,memory_bytes:16*1024**3,storage_bytes:30*1024**3},artifact:{artifact:{uri:'file:///var/tmp/'+id+'/worker.py'}},gpu:false}));
 assert.notEqual(results[0].app.app_version_id,results[1].app.app_version_id);
 const specs=[...registered.values()];assert.match(specs[0].entrypoint.command,/first\/venv/);assert.match(specs[1].entrypoint.command,/second\/venv/);
 assert.ok(specs.every(s=>s.entrypoint.args.includes('flow-same')&&s.metadata.name===receipt.application_profile.app_id));assert.equal(receipt.application_profile.app_id,'workshop-application');
});
