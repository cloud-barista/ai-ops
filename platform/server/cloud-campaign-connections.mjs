import {execFile} from 'node:child_process';import {promisify} from 'node:util';import {awsJsonCall} from './ssm-client.mjs';
const execute=promisify(execFile);
export function localCampaignConnectionScript(config,ids){
 const encoded=Buffer.from(JSON.stringify({ids,plugin:config.ssm_plugin,cli:config.aws_cli})).toString('base64');
 const command=`$d=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}'))|ConvertFrom-Json;$items=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -in @('session-manager-plugin.exe','aws.exe') -and $_.CommandLine -and $_.ExecutablePath});$owned=@($items | Where-Object {$p=$_;@($d.ids|Where-Object {$p.CommandLine.Contains($_)}).Count -gt 0});$plugins=@($owned|Where-Object {[IO.Path]::GetFullPath($_.ExecutablePath) -ieq [IO.Path]::GetFullPath($d.plugin)});$cli=@($owned|Where-Object {[IO.Path]::GetFullPath($_.ExecutablePath) -ieq [IO.Path]::GetFullPath($d.cli) -and $_.CommandLine.Contains('start-session')});$ports=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|Where-Object {$_.LocalPort -in @(18197,18198)});@{plugin_processes=$plugins.Count;cli_processes=$cli.Count;forward_listeners=$ports.Count}|ConvertTo-Json -Compress`;
 return command;
}
async function inspectLocal(config,ids){
 if(process.platform!=='win32'||!config.ssm_plugin||!config.aws_cli)throw Error('local_audit_unavailable');
 const {stdout}=await execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',localCampaignConnectionScript(config,ids)],{windowsHide:true,timeout:15000,maxBuffer:4096});return JSON.parse(stdout);
}
export async function auditCampaignConnections({config,instanceIds,aws=awsJsonCall,local=inspectLocal}){
 const result={at:new Date().toISOString(),instance_ids:instanceIds,verified:false,source:'owned-aws-ssm-and-local-process-audit'};
 try{
  if(!Array.isArray(instanceIds)||instanceIds.length>2||new Set(instanceIds).size!==instanceIds.length||instanceIds.some(id=>!/^i-[a-f0-9]{8,17}$/.test(id)))throw Error('invalid_owned_instances');
  const [sessions,processes]=await Promise.all([aws(config,'ssm','describe-sessions',['--state','Active']),local(config,instanceIds)]);
  if(!Array.isArray(sessions.Sessions)||!['plugin_processes','cli_processes','forward_listeners'].every(k=>Number.isInteger(processes[k])&&processes[k]>=0))throw Error('audit_unavailable');
  const active=sessions.Sessions.filter(s=>instanceIds.includes(s.Target));return {...result,active_owned_sessions:active.length,...processes,verified:active.length===0&&Object.values(processes).every(n=>n===0)};
 }catch{return {...result,error_code:'connection_audit_unavailable'};}
}
