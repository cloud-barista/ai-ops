import {mkdir, readFile, writeFile, rename, readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export function createWorkflowStore(directory) {
  const queues=new Map();
  const path=id=>{if(!/^[a-zA-Z0-9_-]{1,160}$/.test(id))throw Error('invalid_flow_id');return join(directory,id+'.json');};
  const readRecord=async id=>JSON.parse(await readFile(path(id),'utf8'));
  async function writeRecord(value){
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
  // Serialize access to each file so Windows readers cannot block replacement.
  function enqueue(id,operation){
    path(id);const previous=queues.get(id)??Promise.resolve();
    const next=previous.catch(()=>{}).then(operation);
    queues.set(id,next);next.finally(()=>{if(queues.get(id)===next)queues.delete(id);}).catch(()=>{});return next;
  }
  const read=async id=>enqueue(id,()=>readRecord(id));
  const write=async value=>enqueue(value.flow_id,()=>writeRecord(value));
  const update=(id,transform)=>enqueue(id,async()=>writeRecord(await transform(await readRecord(id))));
  async function list(){await mkdir(directory,{recursive:true});return Promise.all((await readdir(directory)).filter(n=>/^[\w-]+\.json$/.test(n)).map(n=>read(n.slice(0,-5))));}
  return {read,write,update,list};
}
