import test from 'node:test';
import assert from 'node:assert/strict';
import {createLlmComparison} from '../server/llm-comparison.mjs';

test('a progress read started during execution cannot become INTERRUPTED when the run finishes during the read',async()=>{
 const records=new Map();let holdRead=false,releaseRead,readStarted,releaseModel;
 const readGate=new Promise(resolve=>{releaseRead=resolve;}),entered=new Promise(resolve=>{readStarted=resolve;}),modelGate=new Promise(resolve=>{releaseModel=resolve;});
 const store={async read(id){if(!records.has(id))throw Object.assign(Error('missing'),{code:'ENOENT'});const copy=structuredClone(records.get(id));if(holdRead&&copy.state==='RUNNING'){holdRead=false;readStarted();await readGate;}return copy;},async write(r){records.set(r.flow_id,structuredClone(r));},async list(){return [...records.values()].map(r=>structuredClone(r));}};
 const clients={llmModels:async()=>({models:[{name:'qwen3.5:4b'},{name:'llama3.2:3b'}]}),profile:async()=>{await modelGate;return {};}};
 const service=createLlmComparison({clients,store}),started=await service.start({request_id:'snapshot-race-001'});
 holdRead=true;const pending=service.get(started.run_id);await entered;releaseModel();
 for(let i=0;i<100&&records.get(started.run_id).state!=='COMPLETED';i++)await new Promise(resolve=>setTimeout(resolve,1));
 assert.equal(records.get(started.run_id).state,'COMPLETED');assert.equal(service.isRunning(),false);releaseRead();
 assert.equal((await pending).state,'RUNNING');assert.equal((await service.get(started.run_id)).state,'COMPLETED');
});
