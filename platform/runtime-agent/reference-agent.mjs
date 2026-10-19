import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {pathToFileURL} from 'node:url';

function propose(request){
  if(!request?.run_id||!request.agent)throw Error('agent_input_invalid');
  if(request.action==='generate_deployment_decision'&&request.capability==='ai_application_automation'){
    const p=request.input?.application_profile,r=request.input?.resource_recommendation;
    const candidate=r?.candidates?.find(c=>c.candidate_id===r.selected_candidate_id&&c.feasible);
    if(!p?.requirements||!candidate)throw Error('agent_input_invalid');
    return {decision:'DEPLOY',selected_candidate_id:candidate.candidate_id,reason:'The selected candidate is feasible; GEON validates minimum requirements.',confidence:candidate.scores?.total??1,reasoning_mode:'rule_based'};
  }
  if(request.action==='generate_scaling_decision'&&request.capability==='ai_application_operation_optimization'){
    const i=request.input?.operation_input,metrics=i?.optimization_feedback?.metrics;
    if(i?.deployment_status?.state!=='RUNNING'||!metrics?.resource||!metrics.inference||!Number.isInteger(i.current_replicas)||!Number.isInteger(i.minimum_replicas)||!Number.isInteger(i.maximum_replicas)||i.current_replicas<1||i.current_replicas<i.minimum_replicas||i.current_replicas>i.maximum_replicas)throw Error('agent_input_invalid');
    const violations=[...(i.optimization_feedback.slo_violations??[])],slo=i.slo??{};
    if(slo.latency_p95_ms_max>0&&metrics.inference.latency_p95_ms>slo.latency_p95_ms_max)violations.push('latency_p95_ms');
    if(slo.throughput_rps_min>0&&metrics.inference.throughput_rps<slo.throughput_rps_min)violations.push('throughput_rps');
    let action='KEEP',desired=i.current_replicas,reason='SLO and utilization remain within the configured operating range.';
    if(violations.length&&desired<i.maximum_replicas){action='SCALE_OUT';desired++;reason='SLO evidence requires one bounded replica increase.';}
    else if(!violations.length&&desired>i.minimum_replicas&&metrics.resource.cpu_average_percent<30&&metrics.resource.accelerator_average_percent<30){action='SCALE_IN';desired--;reason='Healthy SLO and low utilization allow one bounded replica decrease.';}
    const evidence=action==='SCALE_IN'?['cpu_average_percent='+metrics.resource.cpu_average_percent.toFixed(2),'accelerator_average_percent='+metrics.resource.accelerator_average_percent.toFixed(2)]:[...new Set(violations)];
    return {action,current_replicas:i.current_replicas,desired_replicas:desired,reason,evidence,created_at:new Date().toISOString()};
  }
  throw Error('agent_input_invalid');
}
export function createReferenceAgentServer(){
  return createServer(async(req,res)=>{
    res.setHeader('content-type','application/json');res.setHeader('cache-control','no-store');
    if(req.method==='GET'&&req.url==='/healthz')return res.end(JSON.stringify({status:'ok',service:'khu-reference-agent'}));
    if(req.method!=='POST'||req.url!=='/execute'){res.statusCode=404;return res.end(JSON.stringify({error:{code:'not_found'}}));}
    try{
      let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>131072)throw Error('agent_input_invalid');chunks.push(chunk);}
      const request=JSON.parse(Buffer.concat(chunks)),parameters=propose(request);
      res.end(JSON.stringify({run_id:request.run_id,agent:request.agent,status:'completed',proposal:{action:request.action,parameters},evidence:{call_id:randomUUID(),executor:'reference_http_rule'},domain_validation:request.action==='generate_scaling_decision'?'scaling_decision':'deployment_decision'}));
    }catch{res.statusCode=422;res.end(JSON.stringify({error:{code:'agent_input_invalid'}}));}
  });
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)createReferenceAgentServer().listen(18185,'127.0.0.1',()=>process.stdout.write('KHU reference Agent listening on 127.0.0.1:18185\n'));
