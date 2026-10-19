import {writeFile,mkdir,open,rmdir,readFile,rename,unlink} from 'node:fs/promises';import {randomUUID} from 'node:crypto';import {resolve} from 'node:path';
import {cloudServices,cloudJournalDirectory} from './cloud-services.mjs';import {activeCloudRun} from './cloud-execution.mjs';import {createAppDeployClient} from './appdeploy-client.mjs';import {processIdentity} from './process-identity.mjs';
import {holdWindowsMutex} from './windows-mutex.mjs';
const services=await cloudServices({clients:createAppDeployClient()}),directory=cloudJournalDirectory,started_at=new Date().toISOString(),nonce=randomUUID(),identity=await processIdentity(process.pid);
await mkdir(directory,{recursive:true});const releaseWatcher=await holdWindowsMutex(resolve(directory,'watcher-supervisor')),lock=resolve(directory,'watcher.lock');
try{await mkdir(lock);}catch(e){
 if(e.code!=='EEXIST')throw e;const old=JSON.parse(await readFile(resolve(lock,'owner.json'),'utf8')),actual=await processIdentity(old.pid);
 if(actual.state!=='absent'&&(actual.process_started_at===old.process_started_at||!old.process_started_at))throw Error('cleanup_watcher_already_owned');
 await rename(lock,lock+'.retired-'+nonce);await mkdir(lock);
}
const ownership={pid:process.pid,started_at,nonce,...identity};const owner=await open(resolve(lock,'owner.json'),'wx');await owner.writeFile(JSON.stringify(ownership));await owner.close();
let writing=Promise.resolve();
function heartbeat(ready=true){writing=writing.catch(()=>{}).then(async()=>{
 const tmp=resolve(directory,'watcher.json.'+nonce+'.tmp');await writeFile(tmp,JSON.stringify({schema_version:1,...ownership,ready,heartbeat_at:new Date().toISOString()}));
 for(let n=0;;n++){try{await rename(tmp,resolve(directory,'watcher.json'));break;}catch(e){if(n>=5||!['EPERM','EACCES','EBUSY'].includes(e.code))throw e;await new Promise(r=>setTimeout(r,50*(n+1)));}}
 });return writing;}
// Heartbeats continue during provider calls; pending resources retain supervision.
await heartbeat();const ticker=setInterval(()=>heartbeat().catch(()=>{}),5000);process.on('SIGINT',()=>{});process.on('SIGTERM',()=>{});
try{for(;;){await services.campaigns.reconcile().catch(()=>{});const runs=(await services.journal.list()).filter(activeCloudRun);for(const r of runs){try{
 if(r.state==='CREATE_UNKNOWN'){await services.execution.status(r.run_id);await services.execution.cleanup(r.run_id);}else if(r.state==='CLEANUP_PENDING'||Date.now()>=Date.parse(r.deadline_at)){await services.execution.cleanup(r.run_id);}
 }catch{/* Retain intent and retry; errors are never termination proof. */}}
 let requested=false;try{requested=JSON.parse(await readFile(resolve(directory,'watcher-stop.json'),'utf8')).nonce===nonce;}catch{}
 if(requested&&!await services.blocked())break;await new Promise(done=>setTimeout(done,10000));
 }}finally{clearInterval(ticker);await heartbeat(false);await unlink(resolve(lock,'owner.json'));await rmdir(lock);await releaseWatcher();}
