import {createAppDeployClient} from './appdeploy-client.mjs';
import {measureLocalWorker} from './local-probe.mjs';
import {createPrometheusClient} from './prometheus.mjs';
export function createClients({profileUrl=process.env.KHU_LLMOP_URL??'http://127.0.0.1:18110',resourceUrl=process.env.KHU_RESOURCE_OPS_URL??'http://127.0.0.1:18120',geonUrl=process.env.KHU_GEON_URL??'http://127.0.0.1:18130',policyUrl='http://127.0.0.1:18170',ollamaUrl='http://127.0.0.1:11434'}={}){
  for(const base of [profileUrl,resourceUrl,geonUrl,policyUrl,ollamaUrl]){const url=new URL(base);if(url.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||url.username||url.password||url.search||url.hash||url.pathname!=='/')throw Error('local_endpoint_required');}
  async function call(base,path,body){
    const timeout=(path==='/api/v1/application-profiles'&&body?.input_type==='natural_language')||path.endsWith('/reasoning-comparisons')?260000:30000;
    const response=await fetch(base+path,{method:body===undefined?'GET':'POST',headers:{'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});
    if(!response.ok)throw Error('provider_unavailable');return response.json();
  }
  const appPath=id=>'/api/v1/agent-control/flows/'+encodeURIComponent(id)+'/appdeploy';
  const reasoning=(id,body)=>call(geonUrl,'/api/v1/agent-control/flows/'+encodeURIComponent(id)+'/reasoning-comparisons',body);
  const prometheus=createPrometheusClient();
  return {...createAppDeployClient(),measureLocalWorker,prometheusObserve:prometheus.observe,prometheusObservation:prometheus.requireObservation,reasoning,llmModels:()=>call(ollamaUrl,'/api/tags'),researchCases:()=>call(policyUrl,'/api/v1/khu-policy/cases'),researchReplay:body=>call(policyUrl,'/api/v1/khu-policy/replay',body),profile:body=>call(profileUrl,'/api/v1/application-profiles',body),recommend:body=>call(resourceUrl,'/api/v1/resource-recommendations',body),plan:body=>call(geonUrl,'/api/v1/agent-control/external-flows',body),flow:id=>call(geonUrl,'/api/v1/agent-control/flows/'+encodeURIComponent(id)),deploymentStatus:body=>call(geonUrl,'/api/v1/agent-control/deployment-status',body),agents:()=>call(geonUrl,'/api/v1/agents'),registerAgent:body=>call(geonUrl,'/api/v1/agents',body),feedback:(body,{operationAgent}={})=>call(geonUrl,'/api/v1/agent-control/optimization-feedback'+(operationAgent?'?operation_agent='+encodeURIComponent(operationAgent):''),body),appPrepare:(id,body)=>call(geonUrl,appPath(id)+'/prepare',body),appSubmit:(id,body)=>call(geonUrl,appPath(id)+'/submit',body),appStatus:id=>call(geonUrl,appPath(id))};
}
