import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {createPlatformServer} from '../server/index.mjs';
import {createClients} from '../server/upstream.mjs';

async function listen(t,server){
  server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}

test('one Swagger page exposes all three services with namespaced schemas and valid local references',async t=>{
  const base=await listen(t,createPlatformServer());
  for(const path of ['/swagger','/swagger/','/swagger/index.html']){
    const response=await fetch(base+path);assert.equal(response.status,200);
    assert.match(response.headers.get('content-type'),/text\/html/);
    const html=await response.text();assert.match(html,/SwaggerUIBundle/);assert.match(html,/url: '\.\/openapi.json'/);
    const specUrl=new URL('./openapi.json',base+path);
    assert.equal((await fetch(specUrl)).status,200);
  }
  const response=await fetch(base+'/openapi.json');assert.equal(response.status,200);
  const spec=await response.json();
  assert.equal(spec.openapi,'3.0.3');assert.deepEqual(spec.servers,[{url:'/swagger/services'}]);
  assert.deepEqual(spec.tags.map(tag=>tag.name),['LLM_Op','Resource Ops','Deployment Agent']);
  assert.ok(spec.paths['/llmop/api/v1/application-profiles'].post.requestBody);
  assert.ok(spec.paths['/resource-ops/api/v1/resource-recommendations'].post.requestBody);
  assert.ok(spec.paths['/deployment-agent/api/v1/deployment-plans'].post.requestBody);
  assert.equal(spec.paths['/deployment-agent/api/v1/planner/deployments'],undefined);
  assert.equal(spec.components.securitySchemes['deployment-agent.AutonomyAdminToken'].scheme,'bearer');
  assert.deepEqual(spec.paths['/deployment-agent/api/v1/agents/{name}'].delete.security,[{'deployment-agent.AutonomyAdminToken':[]}]);
  const operations=Object.values(spec.paths).flatMap(methods=>Object.values(methods));
  assert.equal(new Set(operations.map(operation=>operation.operationId)).size,operations.length);
  for(const service of ['llmop','resource-ops','deployment-agent'])assert.ok(Object.keys(spec.components.schemas).some(name=>name.startsWith(service+'.')));
  function check(value){
    if(!value||typeof value!=='object')return;
    if(value.$ref){
      assert.ok(value.$ref.startsWith('#/components/'));
      let target=spec;for(const part of value.$ref.slice(2).split('/'))target=target?.[part.replace(/~1/g,'/').replace(/~0/g,'~')];
      assert.ok(target,`unresolved reference: ${value.$ref}`);
    }
    for(const child of Object.values(value))check(child);
  }
  check(spec);
});

test('Try it out reaches the selected service and preserves methods, query, authentication, errors and replay headers',async t=>{
  const seen=[];
  async function upstream(service){return listen(t,createServer(async(request,response)=>{
    let body='';for await(const part of request)body+=part;
    seen.push({service,method:request.method,path:request.url,body,authorization:request.headers.authorization,cookie:request.headers.cookie});
    response.writeHead(service==='deployment-agent'?409:200,{'content-type':'application/json','idempotent-replayed':'true'});
    response.end(JSON.stringify({service}));
  }));}
  const profileUrl=await upstream('llmop'),resourceUrl=await upstream('resource-ops'),geonUrl=await upstream('deployment-agent');
  const base=await listen(t,createPlatformServer({clients:createClients({profileUrl,resourceUrl,geonUrl})}));
  let response=await fetch(base+'/swagger/services/llmop/healthz');assert.equal(response.status,200);assert.deepEqual(await response.json(),{service:'llmop'});
  const body=JSON.stringify({request_id:'통합 문서'});
  response=await fetch(base+'/swagger/services/resource-ops/api/v1/resource-recommendations',{method:'POST',headers:{'content-type':'application/json'},body});
  assert.equal(response.status,200);assert.equal(seen.at(-1).body,body);
  response=await fetch(base+'/swagger/services/deployment-agent/api/v1/agents/demo?source=runtime',{method:'DELETE',headers:{authorization:'Bearer test-placeholder',cookie:'private=do-not-forward'}});
  assert.equal(response.status,409);assert.equal(response.headers.get('idempotent-replayed'),'true');
  assert.deepEqual(seen.at(-1),{service:'deployment-agent',method:'DELETE',path:'/api/v1/agents/demo?source=runtime',body:'',authorization:'Bearer test-placeholder',cookie:undefined});
  assert.equal((await fetch(base+'/swagger/services/llmop/not-documented')).status,404);
  assert.equal((await fetch(base+'/swagger/services/other/healthz')).status,404);
  assert.equal((await fetch(base+'/swagger/services/llmop/healthz',{method:'POST',body:'{}'})).status,405);
  assert.equal(seen.length,3);
});

test('Swagger gateway bounds request bodies and reports unavailable services without internal errors',async t=>{
  const base=await listen(t,createPlatformServer({clients:{swaggerRequest:async()=>{throw Error('private diagnostic');}}}));
  const response=await fetch(base+'/swagger/services/llmop/healthz');
  assert.equal(response.status,502);assert.deepEqual(await response.json(),{error:{code:'swagger_upstream_unavailable'}});
  const oversized=await fetch(base+'/swagger/services/resource-ops/api/v1/resource-recommendations',{method:'POST',body:'x'.repeat(4*1024*1024+1)});
  assert.equal(oversized.status,413);assert.deepEqual(await oversized.json(),{error:{code:'body_too_large'}});
  assert.throws(()=>createClients({profileUrl:'http://outside.example'}),/local_endpoint_required/);
});
