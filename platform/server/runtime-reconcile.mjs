import {createAppDeployClient} from './appdeploy-client.mjs';import {createWorkflowStore} from './workflow-store.mjs';import {cloudServices,cloudJournalDirectory} from './cloud-services.mjs';import {activeCloudRun} from './cloud-execution.mjs';import {syncReplicaTargets} from './prometheus-targets.mjs';import {readFile,writeFile} from 'node:fs/promises';import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));
function providerTerminatedGroup(g,r,run){return !!run&&r.cloud?.run_id===run.run_id&&run.run_id===r.cloud_run_id&&run.intent?.flow_id===g.flow_id&&g.flow_id===r.flow_id&&run.app?.group_id===g.group_id&&run.app?.target_profile_id===g.target_profile_id&&run.app?.app_version_id===g.app_version_id&&run.state==='CLEANED'&&run.cleanup?.confirmed===true&&run.cleanup?.attachments?.verified===true&&Array.isArray(run.cleanup.attachments.remaining)&&run.cleanup.attachments.remaining.length===0&&run.last_provider_status?.state==='Terminated'&&run.last_provider_status.instance_id===run.resource?.provider_instance_id;}
export async function stopOwnedRuntime({store,clients,services,discovery=syncReplicaTargets,journalDirectory=cloudJournalDirectory}){
 if(services.campaigns)for(const parent of (await services.campaigns.list()).filter(p=>['ACTIVE','CLEANUP_PENDING'].includes(p.state)))await services.campaigns.action(parent.run_id,'stop');
 const receipts=await store.list(),byFlow=new Map(receipts.map(r=>[r.flow_id,r])),groups=await clients.groupList(),runs=await services.journal.list(),byRun=new Map(runs.map(r=>[r.run_id,r]));let unknown=[];
 for(const g of groups){const r=byFlow.get(g.flow_id);if(!r||r.replica_group?.group_id!==g.group_id||r.appdeploy?.target_profile_id!==g.target_profile_id&&r.selected_candidate_id!==g.target_profile_id)continue;
  if(g.operation_state==='STOPPED'&&g.actual_replicas===0)continue;
  if(providerTerminatedGroup(g,r,byRun.get(r.cloud?.run_id)))continue;
  try{const stopped=await clients.groupStop(g.group_id);if(stopped.operation_state!=='STOPPED'||stopped.actual_replicas!==0){unknown.push(g.group_id);continue;}
   await store.update(r.flow_id,x=>({...x,replica_group:stopped,execution:{...x.execution,status:'STOPPED',actual_replicas:0},appdeploy:{...x.appdeploy,status:'STOPPED'}}));
  }catch{unknown.push(g.group_id);}
 }
 // Legacy single-process receipts remain supported; no unowned deployments are stopped.
 for(const r of receipts.filter(r=>r.input?.execution_target==='local'&&!r.replica_group&&r.execution?.execution_id&&r.appdeploy?.app_version_id&&r.execution.status!=='STOPPED')){
  try{const d=(await clients.localDeployments()).find(d=>d.deployment_id===r.execution.execution_id);if(d?.target_profile_id!=='khu-local-pc'||d.app_version_id!==r.appdeploy.app_version_id)throw Error('ownership_mismatch');const s=await clients.appStop(r.execution.execution_id);if(s.status!=='STOPPED')throw Error('unknown');await store.update(r.flow_id,x=>({...x,execution:{...x.execution,status:'STOPPED'}}));}catch{unknown.push(r.execution.execution_id);}
 }
 for(const r of runs.filter(activeCloudRun))await services.execution.cleanup(r.run_id);
 if(services.campaigns)await services.campaigns.reconcile();
 const pending=[...(await services.journal.list()).filter(activeCloudRun).map(r=>r.run_id),...(services.campaigns?(await services.campaigns.list()).filter(p=>['ACTIVE','CLEANUP_PENDING'].includes(p.state)).map(p=>p.run_id):[])];
 await discovery(await clients.groupList());
 if(!pending.length){try{const owner=JSON.parse(await readFile(resolve(journalDirectory,'watcher.lock/owner.json'),'utf8'));await writeFile(resolve(journalDirectory,'watcher-stop.json'),JSON.stringify({nonce:owner.nonce}));}catch{}}
 return {unknown_groups:unknown,pending_cloud_runs:pending,retain_cleanup_watcher:pending.length>0};
}
export async function reconcileOwnedRuntime({store,clients,discovery=syncReplicaTargets}){
 const receipts=await store.list(),groups=await clients.groupList(),checked=[];for(const r of receipts){if(!r.replica_group?.group_id||r.cloud?.state==='CLEANED'&&r.cloud.cleanup?.confirmed===true)continue;const cached=groups.find(g=>g.group_id===r.replica_group.group_id&&g.flow_id===r.flow_id&&g.target_profile_id===r.replica_group.target_profile_id);if(!cached)continue;
  const g=await clients.groupStatus(cached.group_id);if(g.flow_id!==r.flow_id||g.target_profile_id!==cached.target_profile_id)throw Error('reconciliation_identity_mismatch');checked.push(g);
  await store.update(r.flow_id,x=>({...x,replica_group:g,execution:{...x.execution,status:g.operation_state==='STOPPED'&&g.actual_replicas===0?'STOPPED':g.operation_state==='IDLE'&&g.actual_replicas>0?'RUNNING':'UNKNOWN',actual_replicas:g.actual_replicas,applied_revision:g.applied_revision},runtime_reconciled_at:new Date().toISOString()}));
 }
 await discovery(checked);return {reconciled:true,active_or_unknown_groups:checked.filter(g=>g.operation_state!=='STOPPED').map(g=>g.group_id)};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const clients=createAppDeployClient(),store=createWorkflowStore(resolve(root,'output/khu-poc/flows'));
 const result=process.argv[2]==='stop'?await stopOwnedRuntime({store,clients,services:await cloudServices({clients,store})}):await reconcileOwnedRuntime({store,clients});process.stdout.write(JSON.stringify(result));
 if(result.unknown_groups?.length)process.exitCode=2;
}
