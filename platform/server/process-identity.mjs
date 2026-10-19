import {execFile} from 'node:child_process';import {promisify} from 'node:util';
const execute=promisify(execFile);
export async function processIdentity(pid){
 if(!Number.isInteger(pid)||pid<1)throw Error('invalid_process_identity');
 if(process.platform!=='win32')throw Error('windows_process_identity_required');
 const {stdout}=await execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',`$p=Get-Process -Id ${pid} -ErrorAction SilentlyContinue;if($null -eq $p){' {"state":"absent"} '}else{@{process_started_at=$p.StartTime.ToUniversalTime().Ticks.ToString();executable=$p.Path.ToLowerInvariant()} | ConvertTo-Json -Compress}`],{timeout:5000,windowsHide:true,maxBuffer:4096});return JSON.parse(stdout);
}
