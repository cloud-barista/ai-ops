import {loadVmCatalog} from './vm-catalog.mjs';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,extname} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {buildCandidates} from './candidates.mjs';
import {createWorkflowStore} from './workflow-store.mjs';
import {createClients} from './upstream.mjs';
import {createWorkflowRoutes} from './workflow-routes.mjs';
import {workerArtifact} from './appdeploy-client.mjs';
import {assertLocalModel,localModels} from './local-ai.mjs';
import {updateHistoryVisibility} from '../history.mjs';
import {profileLlmCandidates} from '../profile-llm.mjs';
import {createLlmComparison} from './llm-comparison.mjs';
import {loadValidationResults} from './validation-results.mjs';
import {replicaTargets} from './prometheus-targets.mjs';
import {refreshResources} from './resource-refresh.mjs';
import {boundCloudMetrics} from './cloud-metrics-proxy.mjs';

export const digest=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function fault(status,code){return Object.assign(Error(code),{status,code});}
const publicRoot=fileURLToPath(new URL('../',import.meta.url));
const publicFiles=new Set(['index.html','research-theme.css','styles.css','navigation.css','navigation.mjs','app.js','validation-ui.mjs','analysis-input.mjs','profile-llm.mjs','llm-comparison-ui.mjs','prometheus-ui.mjs','agents-ui.mjs','replicas-ui.mjs','cloud-ui.mjs','cloud-campaign-ui.mjs','cloud-state.mjs','cloud-types.mjs','policy.mjs','evidence.mjs','view-model.mjs','infrastructure-context.mjs','panels.mjs','reasoning.mjs','pipeline.mjs','deployment.mjs','history.mjs','data/khu-cpu-policy.json','data/khu-campaign-evidence.json','data/khu-vm-spec-catalog.json']);
function validate(value){
  const {application:a,model:m,analysis_input:i,workload:w}=value??{};
  if(value?.execution_target&&!['historical','local','cloud'].includes(value.execution_target))throw fault(400,'invalid_execution_target');
  if(!a?.app_id||!a.app_version||!m?.model_id||!m.model_revision||!['structured','natural_language'].includes(i?.input_type)||!w||!Number.isFinite(w.min_throughput)||!Number.isFinite(w.max_batch_p95_ms))throw fault(400,'invalid_input');
  if(!Number.isInteger(m.batch_size)||m.batch_size<1||!Number.isInteger(m.max_length)||m.max_length<1||m.batch_size!==w.batch_size||m.max_length!==w.max_length||m.model_id!==w.model_id||m.model_revision!==w.model_revision)throw fault(400,'invalid_workload');
  if(['local','cloud'].includes(value.execution_target)){try{assertLocalModel(m);}catch{throw fault(400,'unsupported_local_model');}}
  if(i.input_type==='structured'&&(i.app_spec?.app_id!==a.app_id||i.app_spec?.app_version!==a.app_version))throw fault(400,'identity_mismatch');
  if(i.input_type==='natural_language'&&(typeof i.request!=='string'||!i.request.trim()||Array.from(i.request).length>8000||i.app_spec||i.application?.app_id!==a.app_id||i.application?.app_version!==a.app_version))throw fault(400,'invalid_analysis_input');
  if(i.input_type==='natural_language'&&i.candidate_id&&!profileLlmCandidates.some(c=>c.candidate_id===i.candidate_id))throw fault(400,'invalid_llm_candidate');
}
async function body(request){let size=0;const parts=[];for await(const part of request){size+=part.length;if(size>131072)throw fault(413,'body_too_large');parts.push(part);}try{return JSON.parse(Buffer.concat(parts).toString());}catch{throw fault(400,'invalid_json');}}
const send=(res,status,value)=>{res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(value));};

export function createPlatformServer({clients,store,catalog,snapshots,routeExtension=createWorkflowRoutes(),llmComparison}={}){
  llmComparison??=createLlmComparison({clients,store:createWorkflowStore(process.env.KHU_LLM_COMPARISON_DIR??resolve(publicRoot,'../output/khu-poc/llm-comparisons'))});
  let analysisInFlight=0;
  return createServer(async(request,response)=>{
    try{
      const url=new URL(request.url,'http://localhost');const path=url.pathname;
      if(path==='/api/v1/monitoring/targets'){
        if(request.method!=='GET')throw fault(405,'method_not_allowed');
        return send(response,200,replicaTargets(await clients.groupList()));
      }
      const scrape=path.match(/^\/api\/v1\/metrics\/replicas\/(group-[\w-]+)\/(replica-[\w-]+)$/);
      if(scrape&&request.method==='GET'){
        const g=(await clients.groupList()).find(x=>x.group_id===scrape[1]),c=g?.replicas.find(x=>x.replica_id===scrape[2]&&x.status==='RUNNING');if(!g||!/^khu-vm-[a-f0-9-]+$/.test(g.target_profile_id)||g.operation_state!=='IDLE'||!c||!Number.isInteger(c.port)||c.port<18160||c.port>18169)throw fault(409,'owned_running_replica_required');
        const owned=await store.read(g.flow_id),binding=owned.worker_bindings?.[c.replica_id];if(owned.cloud?.state!=='RUNNING'||owned.appdeploy?.target_profile_id!==g.target_profile_id||owned.replica_group?.group_id!==g.group_id||!binding)throw fault(409,'owned_running_replica_required');
        const out=await clients.replicaInvoke(c.deployment_id,{method:'GET',path:'/metrics/prometheus',port:c.port,timeout_seconds:2});if(out.status_code!==200||!boundCloudMetrics(out.raw_body,owned,g,c))throw fault(502,'metrics_unavailable');response.writeHead(200,{'content-type':'text/plain; version=0.0.4; charset=utf-8','cache-control':'no-store'});response.end(out.raw_body);return;
      }
        if(request.method==='GET'&&['/healthz','/api/v1/health'].includes(path))return send(response,200,{status:'ok',service:'khu-research-poc',execution_modes:['mock','local_process','cloud_vm'],observation_origins:['demo_input','observed'],cloud_execution:'separate_review_approval_preflight_required'});
      if(request.method==='GET'&&path==='/api/v1/execution-options')return send(response,200,{local:{target_id:'khu-local-pc',artifact:workerArtifact,app:'BERT Tiny CPU inference',model_inference:true,models:localModels}});
      if(request.method==='GET'&&path==='/api/v1/scenarios')return send(response,200,JSON.parse(await readFile(resolve(publicRoot,'data/demo-scenarios.json'),'utf8')));
      if(request.method==='GET'&&path==='/api/v1/flows')return send(response,200,{flows:await store.list()});
      if(path==='/api/v1/validation-results'){
        if(request.method!=='GET')throw fault(405,'method_not_allowed');
        return send(response,200,await loadValidationResults({readFlow:id=>store.read(id)}));
      }
      if(path==='/api/v1/llm-candidates'||path.startsWith('/api/v1/llm-comparisons')){
        if(request.method==='POST'&&analysisInFlight)throw fault(409,'llm_comparison_busy');
        const result=await llmComparison.route({request,path,value:request.method==='POST'?await body(request):undefined});
        if(result)return send(response,result.status??200,result.data);
        throw fault(404,'not_found');
      }
      if(request.method==='GET'&&path==='/api/v1/research-policy/cases'){
        try{return send(response,200,await clients.researchCases());}catch{throw fault(502,'research_policy_unavailable');}
      }
      if(request.method==='GET'&&/^\/api\/v1\/flows\/[\w-]+$/.test(path))return send(response,200,await store.read(path.split('/').at(-1)));
      const resourceRefresh=path.match(/^\/api\/v1\/flows\/([\w-]+)\/resource-refresh$/);
      if(resourceRefresh){
        if(request.method!=='POST')throw fault(405,'method_not_allowed');
        return send(response,200,await refreshResources({store,clients,catalog,snapshots,id:resourceRefresh[1],value:await body(request)}));
      }
      const prometheusPath=path.match(/^\/api\/v1\/flows\/([\w-]+)\/prometheus$/);
      if(prometheusPath&&request.method==='GET')return send(response,200,await clients.prometheusObserve(await store.read(prometheusPath[1])));
      const historyPath=path.match(/^\/api\/v1\/flows\/([\w-]+)\/history$/);
      if(historyPath&&request.method==='POST'){
        const value=await body(request);
        return send(response,200,await store.update(historyPath[1],r=>updateHistoryVisibility(r,value)));
      }
      if(request.method==='POST'&&path==='/api/v1/flows'){
        const input=await body(request);validate(input);const flow_id='flow-'+randomUUID();
        if(input.analysis_input.input_type==='natural_language'&&llmComparison.isRunning())throw fault(409,'llm_comparison_busy');
        const created_at=new Date().toISOString();
        const local=['local','cloud'].includes(input.execution_target);
        const requested_application={...input.application};
        if(local){
          const version=input.application.app_version+'-poc.'+digest({flow_id,model:input.model}).slice(0,12);
          input.application={...input.application,app_version:version};
          if(input.analysis_input.input_type==='structured')input.analysis_input.app_spec={...input.analysis_input.app_spec,app_version:version};
          else input.analysis_input.application={...input.analysis_input.application,app_version:version};
        }
        let receipt={flow_id,trace_id:randomUUID(),state:'ANALYSIS_PENDING',created_at,input,requested_application,selection_version:0,execution_mode:input.execution_target==='cloud'?'cloud_vm':local?'local_process':'mock',observation_origin:input.execution_target==='local'?'observed':'demo_input',history:[{event:'analysis_requested',at:created_at}]};await store.write(receipt);
        try{
          let result;analysisInFlight++;
          try{result=await clients.profile(input.analysis_input);}finally{analysisInFlight--;}
          const profile=result.application_profile;
          if(profile?.app_id!==input.application.app_id||profile.app_version!==input.application.app_version)throw fault(422,'identity_mismatch');
          if(input.analysis_input.input_type==='natural_language'&&result.analysis?.mode!=='llm'){
            receipt={...receipt,analysis:result.analysis};throw fault(422,'llm_analysis_unavailable');
          }
          if(input.analysis_input.input_type==='natural_language'&&input.analysis_input.candidate_id){
            const candidate=profileLlmCandidates.find(c=>c.candidate_id===input.analysis_input.candidate_id);
            receipt={...receipt,analysis:result.analysis};
            if(result.analysis?.candidate_id!==candidate.candidate_id||result.analysis.actual_model!==candidate.actual_model)throw fault(422,'llm_identity_mismatch');
          }
          receipt={...receipt,state:'RECOMMENDATION_PENDING',application_profile:profile,analysis:result.analysis,history:[...receipt.history,{event:'profile_received',at:new Date().toISOString()}]};await store.write(receipt);
          const rec=await clients.recommend({schema_version:'1.0',request_id:flow_id,application_profile:profile,preferences:{max_results:5},selectors:{providers:[input.execution_target==='local'?'local':'aws']}});
          if(rec.profile_id!==profile.profile_id||rec.request_id!==flow_id)throw fault(422,'identity_mismatch');
          receipt={...receipt,state:'ANALYZED',application_profile:profile,analysis:result.analysis,resource_recommendation:rec,hashes:{profile:digest(profile),recommendation:digest(rec)},history:[...receipt.history,{event:'resource_received',at:new Date().toISOString()}],candidates:buildCandidates({applicationProfile:profile,recommendation:rec,workload:input.workload,catalog,cpuPolicy:snapshots.cpuPolicy,evidence:snapshots.evidence,executionTarget:input.execution_target})};
          await store.write(receipt);return send(response,201,receipt);
        }catch(error){await store.write({...receipt,state:'STOPPED',history:[...receipt.history,{event:'analysis_stopped',at:new Date().toISOString()}],error:{code:error.code??'provider_unavailable'}});return send(response,error.status??502,{flow_id,error:{code:error.code??'provider_unavailable'}});}
      }
      if(routeExtension&&path.startsWith('/api/')){const value=request.method==='POST'?await body(request):undefined;const result=await routeExtension({request,path,value,store,clients});if(result)return send(response,result.status??200,result.data);}
      if(request.method==='GET'&&!path.startsWith('/api/')){
        const name=path==='/'?'index.html':path.slice(1);if(!publicFiles.has(name))throw fault(404,'not_found');
        const data=await readFile(resolve(publicRoot,name));response.writeHead(200,{'cache-control':'no-store','content-type':({'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.json':'application/json'})[extname(name)]+'; charset=utf-8'});response.end(data);return;
      }
      throw fault(404,'not_found');
    }catch(error){send(response,error.code==='ENOENT'?404:error.status??500,{error:{code:error.code==='ENOENT'?'not_found':error.status?error.code:'internal_error'}});}
  });
}

export async function startPlatform(options={}){
  const load=async name=>JSON.parse(await readFile(resolve(publicRoot,'data',name),'utf8'));
  const server=createPlatformServer({clients:createClients(),store:createWorkflowStore(process.env.KHU_FLOW_DIR??resolve(publicRoot,'../output/khu-poc/flows')),catalog:await loadVmCatalog(),snapshots:{cpuPolicy:await load('khu-cpu-policy.json'),evidence:await load('khu-campaign-evidence.json')},routeExtension:createWorkflowRoutes(),...options});
  server.listen(Number(process.env.PORT??18100),'127.0.0.1',()=>process.stdout.write('KHU Research PoC http://127.0.0.1:'+server.address().port+'\n'));return server;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))await startPlatform();
