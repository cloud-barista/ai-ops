import {mkdir, readFile, writeFile, rename, readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export function createWorkflowStore(directory) {
  const queues=new Map();
  const path=id=>{if(!/^[a-zA-Z0-9_-]{1,160}$/.test(id))throw Error('invalid_flow_id');return join(directory,id+'.json');};
  const read=async id=>JSON.parse(await readFile(path(id),'utf8'));
  async function write(value){
    const target=path(value.flow_id);await mkdir(directory,{recursive:true});
    const temporary=target+'.'+randomUUID()+'.tmp';
    await writeFile(temporary,JSON.stringify(value,null,2),'utf8');
    // Windows may briefly deny replacement while a reader holds the old file.
    for(let attempt=0;;attempt++){
      try{await rename(temporary,target);break;}
      catch(error){if(!['EPERM','EACCES','EBUSY'].includes(error.code)||attempt===5)throw error;await delay(20*(attempt+1));}
    }
    return value;
  }
  function update(id,transform){
    path(id);const previous=queues.get(id)??Promise.resolve();
    const next=previous.catch(()=>{}).then(async()=>write(await transform(await read(id))));
    queues.set(id,next);next.finally(()=>{if(queues.get(id)===next)queues.delete(id);}).catch(()=>{});return next;
  }
  async function list(){await mkdir(directory,{recursive:true});return Promise.all((await readdir(directory)).filter(n=>/^[\w-]+\.json$/.test(n)).map(n=>read(n.slice(0,-5))));}
  return {read,write,update,list};
}
