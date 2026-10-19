import {cloudConfig,cloudJournalDirectory} from '../server/cloud-services.mjs';import {createCloudJournal} from '../server/cloud-journal.mjs';import {startSsmRelay,createSsmTunnelManager,startRelayControl,requestRelayClose} from '../server/ssm-relay.mjs';import {activeCloudRun} from '../server/cloud-execution.mjs';import {createHash} from 'node:crypto';import {join} from 'node:path';import {tmpdir} from 'node:os';
const journal=createCloudJournal(cloudJournalDirectory),listRuns=()=>journal.list(),canClose=async()=>!(await listRuns()).some(activeCloudRun),suffix=createHash('sha256').update(cloudJournalDirectory).digest('hex').slice(0,16),address=process.platform==='win32'?'\\\\.\\pipe\\khu-ssm-'+suffix:join(tmpdir(),'khu-ssm-'+suffix+'.sock');
if(process.argv.includes('--stop-idle')){if(!await canClose())throw Error('relay_has_active_cloud_run');try{await requestRelayClose(address);console.log(JSON.stringify({ssm_relay_stopped:true}));}catch(e){if(['ENOENT','ECONNREFUSED'].includes(e.code))process.exitCode=3;else throw e;}}
else{
const config=await cloudConfig();if(!config.enabled||config.management_transport!=='ssm')throw Error('approved_ssm_mode_required');
const manager=createSsmTunnelManager(config,{listRuns});
const server=await startSsmRelay({listRuns,connect:r=>manager.connect(r)});console.log(JSON.stringify({mode:'OWNED_SSM_SSH_RELAY',host:'127.0.0.1',port:18195}));
const control=await startRelayControl({address,canClose,close:async()=>{await server.closeOwned();await manager.close();},onStopped:()=>{control.close();process.exit(0);}});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await server.closeOwned();await manager.close();process.exit(0);});
}
