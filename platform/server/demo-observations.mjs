import {loadVmCatalog} from './vm-catalog.mjs';
import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {localHostSnapshot} from './local-host.mjs';
const queries=['up{job="node"}','count(count(node_cpu_seconds_total{job="node"}) by (node_id,hostname,node_ip,cpu)) by (node_id,hostname,node_ip)','100 * (1 - avg by (node_id,hostname,node_ip) (rate(node_cpu_seconds_total{job="node",mode="idle"}[5m])))','node_memory_MemTotal_bytes{job="node"}','node_memory_MemAvailable_bytes{job="node"}','node_filesystem_size_bytes{job="node",mountpoint="/",fstype!~"tmpfs|overlay"}','node_filesystem_avail_bytes{job="node",mountpoint="/",fstype!~"tmpfs|overlay"}','node_uname_info{job="node"}','node_os_info{job="node"}','up{job="dcgm"}','DCGM_FI_DEV_GPU_UTIL{job="dcgm"}','DCGM_FI_DEV_FB_FREE{job="dcgm"}','DCGM_FI_DEV_FB_USED{job="dcgm"}','DCGM_FI_DEV_FB_RESERVED{job="dcgm"}'];
export function createDemoObservationServer({catalog,now=()=>Date.now()/1000,localSnapshot}={}){return createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost'),respond=(status,data)=>{res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(data));};
  if(req.method==='GET'&&url.pathname==='/health')return respond(200,{status:'ok',source:'demo_input'});
  const index=queries.indexOf(url.searchParams.get('query'));if(req.method!=='GET'||url.pathname!=='/api/v1/query'||index<0)return respond(400,{status:'error',errorType:'unsupported_query',error:'Only the local demo query set is supported'});
  const evaluationTime=url.searchParams.has('time')?Number(url.searchParams.get('time')):now();if(!Number.isFinite(evaluationTime))return respond(400,{status:'error',error:'invalid_time'});
  // Whole seconds remain conservative across the Go/JSON float timestamp conversion.
  const timestamp=Math.floor(evaluationTime);
  const entries=index>=9?catalog.entries.filter(s=>s.gpu_count):catalog.entries;
  const result=entries.map((s,i)=>{const id='demo-'+s.name.replaceAll('.','-');const metric={node_id:id,hostname:id,node_ip:'192.0.2.'+(catalog.entries.indexOf(s)+1),provider:'aws',region:'us-west-2',resource_type:'VM',spec_id:s.spec_id,execution_target:'historical',architecture:'x86_64',sysname:'Linux',machine:'x86_64',name:'Ubuntu',version_id:'24.04',job:index>=9?'dcgm':'node'};if(index>=10)Object.assign(metric,{UUID:'GPU-DEMO-'+i,gpu:'0',modelName:s.gpu_model});
    const value=[1,s.cpu_cores,0,s.memory_mib*1048576,s.memory_mib*1048576,100*1073741824,100*1073741824,1,1,1,0,s.gpu_memory_mib,0,0][index];return {metric,value:[timestamp,String(value)]};});
  if(localSnapshot&&index<9){
    try{const h=await localSnapshot();result.push({metric:{node_id:h.id,hostname:h.hostname,node_ip:'127.0.0.1',provider:'local',region:'local',resource_type:'HOST',spec_id:h.spec_id,execution_target:'local',architecture:'x86_64',sysname:'Windows',machine:'x86_64',name:'Windows',version_id:'local',job:'node'},value:[timestamp,String([1,h.cpu_cores,h.cpu_utilization,h.memory_total,h.memory_available,h.disk_total,h.disk_available,1,1][index])]});}catch{return respond(503,{status:'error',error:'local_observation_unavailable'});}
  }
  respond(200,{status:'success',data:{resultType:'vector',result},source:'demo_input'});
});}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){const catalog=await loadVmCatalog();createDemoObservationServer({catalog,localSnapshot:localHostSnapshot}).listen(18140,'127.0.0.1',()=>process.stdout.write('Historical demo + actual local OS observation input 127.0.0.1:18140\n'));}
