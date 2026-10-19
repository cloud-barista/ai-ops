import test from 'node:test';import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';
const mod=await import('../server/cloud-campaign-connections.mjs').catch(e=>{if(e.code==='ERR_MODULE_NOT_FOUND')return {};throw e;});
const ids=['i-'+'1'.repeat(17),'i-'+'2'.repeat(17)];
async function audit(sessions,local){assert.equal(typeof mod.auditCampaignConnections,'function','owned connection absence must be independently verified');return mod.auditCampaignConnections({config:{},instanceIds:ids,aws:async()=>sessions,local:async()=>local});}
test('active SSM sessions for either owned VM prevent a clean campaign',async()=>{const r=await audit({Sessions:[{Target:ids[1],SessionId:'owned-active'},{Target:'i-foreign',SessionId:'foreign'}]},{plugin_processes:0,cli_processes:0,forward_listeners:0});assert.equal(r.verified,false);assert.equal(r.active_owned_sessions,1);});
test('terminated AWS sessions do not hide a retained local plugin or tunnel',async()=>{for(const field of ['plugin_processes','cli_processes','forward_listeners']){const r=await audit({Sessions:[]},{plugin_processes:0,cli_processes:0,forward_listeners:0,[field]:1});assert.equal(r.verified,false);}});
test('malformed AWS or local observations remain unknown rather than proving zero',async()=>{assert.equal((await audit({},{})).verified,false);assert.equal((await audit({Sessions:[]},{})).verified,false);});
test('only verified absence of all owned sessions and forwarding processes is clean',async()=>{const r=await audit({Sessions:[{Target:'i-foreign'}]},{plugin_processes:0,cli_processes:0,forward_listeners:0});assert.equal(r.verified,true);assert.deepEqual(r.instance_ids,ids);assert.equal(r.active_owned_sessions,0);});
test('Windows ownership comparison recognizes slash-equivalent executable paths',async()=>{
 if(process.platform!=='win32')return;
 assert.equal(typeof mod.localCampaignConnectionScript,'function','local process audit command must be runnable in a controlled Windows environment');
 const script=mod.localCampaignConnectionScript({ssm_plugin:'C:/khu/plugin.exe',aws_cli:'C:/khu/aws.exe'},ids);
 const fixture="function Get-CimInstance { @([pscustomobject]@{Name='aws.exe';ExecutablePath='C:\\khu\\aws.exe';CommandLine='aws ssm start-session --target i-11111111111111111'},[pscustomobject]@{Name='session-manager-plugin.exe';ExecutablePath='C:\\khu\\plugin.exe';CommandLine='plugin i-22222222222222222'}) }; function Get-NetTCPConnection { @() }; ";
 const {stdout}=await promisify(execFile)('powershell.exe',['-NoProfile','-NonInteractive','-Command',fixture+script],{windowsHide:true});const r=JSON.parse(stdout);assert.equal(r.cli_processes,1);assert.equal(r.plugin_processes,1);
});
