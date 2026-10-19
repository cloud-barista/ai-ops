import {writeFile,readFile,rename,mkdir} from 'node:fs/promises';import {resolve,dirname} from 'node:path';import {fileURLToPath} from 'node:url';import {randomUUID} from 'node:crypto';
const defaultPath=fileURLToPath(new URL('../monitoring/replica-targets.json',import.meta.url));
export function replicaTargets(groups){
 return groups.filter(g=>(g.target_profile_id==='khu-local-pc'||/^khu-vm-[a-f0-9-]+$/.test(g.target_profile_id))&&g.operation_state==='IDLE').flatMap(g=>g.replicas.filter(c=>c.status==='RUNNING').map(c=>{if(!Number.isInteger(c.port)||c.port<18160||c.port>18169)throw Error('invalid_replica_port');const cloud=g.target_profile_id!=='khu-local-pc';return {targets:['127.0.0.1:'+(cloud?18100:c.port)],labels:{khu_group_id:g.group_id,khu_replica_id:c.replica_id,...(cloud?{__metrics_path__:'/api/v1/metrics/replicas/'+g.group_id+'/'+c.replica_id}:{})}};}));
}
export async function syncReplicaTargets(groups,path=defaultPath){
 const targets=replicaTargets(groups);
 const content=JSON.stringify(targets);
 try{if(await readFile(path,'utf8')===content)return targets;}catch(e){if(e.code!=='ENOENT')throw e;}
 await mkdir(dirname(resolve(path)),{recursive:true});const tmp=path+'.'+randomUUID()+'.tmp';await writeFile(tmp,content);
 // Windows file discovery can briefly hold the destination during a refresh.
 // Retain the old complete document until an atomic replacement succeeds.
 for(let attempt=0;;attempt++){try{await rename(tmp,path);break;}catch(e){if(!['EPERM','EACCES','EBUSY'].includes(e.code)||attempt>=9)throw e;await new Promise(done=>setTimeout(done,100));}}
 return targets;
}
