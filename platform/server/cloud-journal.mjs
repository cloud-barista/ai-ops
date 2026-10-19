import {mkdir,open,rename,readFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';import {randomUUID} from 'node:crypto';import {setTimeout as delay} from 'node:timers/promises';
import {withWindowsMutex} from './windows-mutex.mjs';

// A separate, never-hidden ownership store shared with the independent watcher.
export function createCloudJournal(directory){
 const file=id=>{if(!/^(run-)[a-zA-Z0-9-]{1,100}$/.test(id))throw Error('invalid_run_id');return join(directory,id+'.json');};
 const read=async id=>JSON.parse(await readFile(file(id),'utf8'));
 async function save(r){const target=file(r.run_id),tmp=target+'.'+randomUUID()+'.tmp';const h=await open(tmp,'wx');try{await h.writeFile(JSON.stringify(r,null,2));await h.sync();}finally{await h.close();}for(let n=0;;n++){try{await rename(tmp,target);return r;}catch(e){if(!['EPERM','EBUSY','EACCES'].includes(e.code)||n===10)throw e;await delay(30*(n+1));}}}
 async function locked(id,fn){await mkdir(directory,{recursive:true});return withWindowsMutex(file(id),fn);}
 return {read,exclusive:fn=>locked('run-global-gate',fn),write:r=>locked(r.run_id,async()=>{try{await read(r.run_id);throw Error('journal_already_exists');}catch(e){if(e.code!=='ENOENT')throw e;}return save(r);}),
  update:(id,transform)=>locked(id,async()=>{const old=await read(id),r=await transform(structuredClone(old));if(r.run_id!==id||JSON.stringify(r.intent)!==JSON.stringify(old.intent))throw Error('immutable_cloud_intent');
   if(old.resource){const identity=x=>({...x,attachment_ids:undefined,attachment_audit_state:undefined});if(JSON.stringify(identity(r.resource))!==JSON.stringify(identity(old.resource)))throw Error('immutable_cloud_resource');if(old.resource.attachment_ids&&JSON.stringify(r.resource.attachment_ids)!==JSON.stringify(old.resource.attachment_ids))throw Error('immutable_cloud_attachments');}
   return save({...r,journal_revision:(old.journal_revision??0)+1});}),
  list:async()=>{await mkdir(directory,{recursive:true});return Promise.all((await readdir(directory)).filter(x=>/^run-[\w-]+\.json$/.test(x)).map(x=>read(x.slice(0,-5))));}};
}
