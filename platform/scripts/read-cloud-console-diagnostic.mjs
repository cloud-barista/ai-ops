// Read-only, sanitized AWS console diagnostic for one owned cloud run.
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {boundedProcess} from '../server/cloud-client.mjs';
import {cloudConfig,cloudJournalDirectory} from '../server/cloud-services.mjs';
import {createCloudJournal} from '../server/cloud-journal.mjs';

const stages=new Set(['GUARD_WATCH','START','APT_UPDATE','APT_VENV','CREATE_VENV','TORCH_INSTALL','LIBRARY_INSTALL','MODEL_FORWARD','READY','POST_READY','FAILED']);
const services=new Set(['active','inactive','failed','activating','UNKNOWN']);
const signals=['accepted publickey','connection closed','connection reset','maxstartups','drop connection','refusing connection','penalty','kex_exchange_identification','session opened','session closed','disconnected from user','received disconnect from','fatal:','error:'];
const eventKinds=new Set(['PREAUTH_TIMEOUT','SSHD_PRIVSEP','SSHD_FATAL_OTHER','SERVER_RATE_LIMIT','KEX_RESET','KEX_CLOSED','KEX_OTHER','PREAUTH_RESET','CONNECTION_RESET','PREAUTH_CLOSED','CONNECTION_CLOSED','SSHD_ERROR_OTHER']);
export function parseBootstrapConsole(raw){
 const observations=[];
 for(const line of String(raw??'').split(/\r?\n/)){
  const marker='KHU_BOOTSTRAP_DIAG=',at=line.indexOf(marker);
  if(at<0)continue;
  try{
   const value=JSON.parse(line.slice(at+marker.length).trim());
   if(!stages.has(value.stage)||!Number.isInteger(value.at)||value.at<0||!services.has(value.ssh_service)||typeof value.journal_available!=='boolean')continue;
   const counts={};
   for(const name of signals){const n=value.signals?.[name];if(Number.isInteger(n)&&n>=0&&n<=500)counts[name]=n;}
   const events=Array.isArray(value.events)?value.events.slice(-12).filter(e=>e&&Number.isInteger(e.at)&&e.at>0&&e.at<4102444800&&eventKinds.has(e.kind)).map(e=>({at:e.at,kind:e.kind})):[];
   const packets=Array.isArray(value.packets)?value.packets.slice(-32).filter(p=>p&&Number.isSafeInteger(p.at_ms)&&p.at_ms>0&&p.at_ms<4102444800000&&/^[a-f0-9]{12}$/.test(p.connection)&&['IN','OUT'].includes(p.direction)&&['SYN','SYN_ACK','RST','FIN','DATA'].includes(p.kind)&&Number.isInteger(p.ttl)&&p.ttl>=1&&p.ttl<=255&&Number.isInteger(p.payload_bytes)&&p.payload_bytes>=0&&p.payload_bytes<=65535).map(p=>({at_ms:p.at_ms,connection:p.connection,direction:p.direction,kind:p.kind,ttl:p.ttl,payload_bytes:p.payload_bytes})):[];
   observations.push({stage:value.stage,at:value.at,ssh_service:value.ssh_service,journal_available:value.journal_available,signals:counts,...events.length?{events}:{},...typeof value.packet_capture==='boolean'?{packet_capture:value.packet_capture,packets}:{}});
  }catch{/* The AWS console includes unrelated boot text; discard malformed markers. */}
 }
 return observations;
}
export function mergeConsoleObservations(previous,incoming){
 const safe=parseBootstrapConsole([...previous,...incoming].map(x=>'KHU_BOOTSTRAP_DIAG='+JSON.stringify(x)).join('\n'));
 return [...new Map(safe.map(x=>[JSON.stringify(x),x])).values()].sort((a,b)=>a.at-b.at).slice(-600);
}

export async function readOwnedConsole(runId){
 if(!/^run-[a-f0-9-]{36}$/.test(runId??''))throw Error('owned_run_id_required');
 const journal=createCloudJournal(cloudJournalDirectory),run=await journal.read(runId),instanceId=run.resource?.provider_instance_id;
 if(run.intent?.infra_id!==`khu-poc-${runId.slice(4)}`||!/^i-[a-f0-9]{8,17}$/.test(instanceId??''))throw Error('owned_instance_required');
 const config=await cloudConfig();
 if(!config.aws_cli||!config.aws_profile)throw Error('provider_audit_unconfigured');
 const consoleResult=await boundedProcess(config.aws_cli,['--profile',config.aws_profile,'--region','us-west-2','--output','json','--no-cli-pager','ec2','get-console-output','--instance-id',instanceId,'--latest'],'',{timeout:30000,maxBytes:4194304});
 const report={schema_version:1,purpose:'READ_ONLY_BOOTSTRAP_CONSOLE_DIAGNOSIS',run_id:runId,provider_instance_id:instanceId,checked_at:new Date().toISOString(),observations:parseBootstrapConsole(consoleResult.Output)};
 const root=fileURLToPath(new URL('../../',import.meta.url)),directory=resolve(root,'output/khu-platform-validation/cloud-live-validation');
 await mkdir(directory,{recursive:true});const target=resolve(directory,`console-diagnostic-${runId}.json`);
 report.snapshot_observations=report.observations.length;
 try{const previous=JSON.parse(await readFile(target,'utf8'));if(previous.run_id===runId&&previous.provider_instance_id===instanceId&&Array.isArray(previous.observations))report.observations=mergeConsoleObservations(previous.observations,report.observations);}catch{}
 await writeFile(target,JSON.stringify(report,null,2));
 return {...report,saved_to:target};
}
async function main(){console.log(JSON.stringify(await readOwnedConsole(process.argv[2])));}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(e=>{console.error(JSON.stringify({error_code:/^[a-z][a-z0-9_]+$/.test(e.message)?e.message:'console_diagnostic_failed'}));process.exitCode=2;});
