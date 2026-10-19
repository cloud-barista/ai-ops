import {writeFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createCloudClient,providerUnavailable} from '../server/cloud-client.mjs';
import {cloudConfig,cloudJournalDirectory} from '../server/cloud-services.mjs';
import {createCloudJournal} from '../server/cloud-journal.mjs';

const runId=process.argv[2];
if(!/^run-[0-9a-f-]{36}$/.test(runId??''))throw Error('owned_run_required');
const run=await createCloudJournal(cloudJournalDirectory).read(runId);
if(!['CREATED','PREPARING','RUNNING'].includes(run.state)||!run.resource?.provider_instance_id)throw Error('running_owned_vm_required');
const directory=fileURLToPath(new URL('../../output/khu-platform-validation/cloud-live-validation/',import.meta.url));
const result={run_id:run.run_id,provider_instance_id:run.resource.provider_instance_id,checked_at:new Date().toISOString()};
try{result.guest=await createCloudClient(await cloudConfig()).guestRuntimeProbe({...run.intent,...run.resource});}
catch(e){const safe=providerUnavailable(e.diagnostic_code,e.ssh_diagnostic);result.failure={diagnostic_code:safe.diagnostic_code,ssh_diagnostic:safe.ssh_diagnostic};}
await mkdir(directory,{recursive:true});
await writeFile(resolve(directory,'runtime-diagnostic-'+runId+'.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));
