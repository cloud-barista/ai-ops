import {readFile,statfs} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {createCloudJournal} from './cloud-journal.mjs';import {createCloudClient} from './cloud-client.mjs';import {createCloudExecution,activeCloudRun} from './cloud-execution.mjs';
import {processIdentity} from './process-identity.mjs';
import {createCloudCampaigns} from './cloud-campaign.mjs';
import {auditCampaignConnections} from './cloud-campaign-connections.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
export const cloudJournalDirectory=process.env.KHU_CLOUD_JOURNAL_DIR??resolve(root,'output/khu-platform-validation/cloud-journal');
export const cloudCampaignDirectory=process.env.KHU_CLOUD_CAMPAIGN_DIR??resolve(root,'output/khu-platform-validation/cloud-campaign-journal');
export async function cloudConfig(){try{return JSON.parse(await readFile(process.env.KHU_CLOUD_CONFIG??resolve(root,'platform/config/cloud-connection.json'),'utf8'));}catch(e){if(e.code!=='ENOENT')throw Error('invalid_cloud_configuration');return {enabled:false};}}
export async function watcherHealth(directory=cloudJournalDirectory,{inspect=processIdentity}={}){try{
 const h=JSON.parse(await readFile(resolve(directory,'watcher.json'),'utf8')),owner=JSON.parse(await readFile(resolve(directory,'watcher.lock/owner.json'),'utf8'));
 const age=Date.now()-Date.parse(h.heartbeat_at);if(h.schema_version!==1||h.ready!==true||!Number.isFinite(age)||age<0||age>25000||!Number.isInteger(h.pid)||h.pid<1||!h.nonce||!h.process_started_at||!h.executable)return {healthy:false};
 for(const k of ['pid','nonce','process_started_at','executable'])if(h[k]!==owner[k])return {healthy:false};
 const actual=await inspect(h.pid);if(actual.process_started_at!==h.process_started_at||actual.executable!==h.executable)return {healthy:false};return {healthy:true,pid:h.pid,started_at:h.started_at};
 }catch{return {healthy:false};}}
export async function cloudServices({clients,store}={}){
 const config=await cloudConfig(),journal=createCloudJournal(cloudJournalDirectory),client=createCloudClient(config),original=client.preflight;
 client.preflight=async i=>{const result=await original(i),disk=await statfs(root);const free=Number(disk.bavail)*Number(disk.bsize)>=5*1073741824;return {...result,ready:result.ready&&free,checks:{...result.checks,windows_storage:free}};};
 let campaigns;
 const execution=createCloudExecution({journal,client,enabled:config.enabled===true,watcher:()=>watcherHealth(),admission:(r,runs)=>campaigns.admit(r,runs),stopApp:async run=>{
  const groups=await clients?.groupList?.();for(const g of groups??[])if(g.flow_id===run.intent.flow_id&&g.target_profile_id===run.app?.target_profile_id&&g.operation_state!=='STOPPED'){await clients.groupStop(g.group_id);}
 }});
 campaigns=createCloudCampaigns({journal:createCloudJournal(cloudCampaignDirectory),cloudJournal:journal,execution,client,connectionAudit:instanceIds=>auditCampaignConnections({config,instanceIds})});
 return {config,journal,client,execution,campaigns,async blocked(){return (await journal.list()).some(activeCloudRun)||(await campaigns.list()).some(p=>['ACTIVE','CLEANUP_PENDING'].includes(p.state));}};
}
