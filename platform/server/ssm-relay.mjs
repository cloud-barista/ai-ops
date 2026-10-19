import net from 'node:net';import {spawn,execFile} from 'node:child_process';import {dirname} from 'node:path';import {setTimeout as pause} from 'node:timers/promises';import {promisify} from 'node:util';import {processIdentity} from './process-identity.mjs';
import {awsJsonCall} from './ssm-client.mjs';
export function resolveSsmRoute(runs,host,port,now=Date.now()){
 const matches=runs.filter(r=>['PREPARING','RUNNING','CLEANUP_PENDING'].includes(r.state)&&Date.parse(r.deadline_at)>now&&r.resource?.host===host&&port===22&&/^i-[a-f0-9]{8,17}$/.test(r.resource.provider_instance_id??'')&&r.intent?.infra_id===r.resource.infra_id&&r.intent?.intent_sha256===r.resource.intent_sha256&&r.ssm?.online===true&&/^iip-assoc-[a-zA-Z0-9]+$/.test(r.ssm.association_id??''));
 if(matches.length!==1)throw Error('owned_ssm_route_required');return matches[0];
}
export async function startSsmRelay({port=18195,listRuns,connect}){
 const sockets=new Set(),server=net.createServer(socket=>{
  sockets.add(socket);socket.on('close',()=>sockets.delete(socket));socket.on('error',()=>{});socket.setTimeout(30000,()=>socket.destroy());
  let buffer=Buffer.alloc(0),pending;
  const feed=b=>{buffer=Buffer.concat([buffer,b]);if(buffer.length>1024)return socket.destroy();if(pending&&buffer.length>=pending.size){const p=pending;pending=null;const value=buffer.subarray(0,p.size);buffer=buffer.subarray(p.size);p.resolve(value);}};
  socket.on('data',feed);const read=size=>new Promise((resolve,reject)=>{if(buffer.length>=size){const b=buffer.subarray(0,size);buffer=buffer.subarray(size);return resolve(b);}pending={size,resolve};socket.once('close',()=>reject(Error('closed')));});
  (async()=>{
   const header=await read(2);if(header[0]!==5||header[1]<1)throw Error('protocol');const methods=await read(header[1]);if(!methods.includes(0)){socket.end(Buffer.from([5,255]));return;}socket.write(Buffer.from([5,0]));
   const request=await read(4);if(request[0]!==5||request[1]!==1||request[2]!==0||request[3]!==1)throw Error('protocol');const target=await read(6),host=[...target.subarray(0,4)].join('.'),targetPort=target.readUInt16BE(4);
   const run=resolveSsmRoute(await listRuns(),host,targetPort),upstream=await connect(run);sockets.add(upstream);upstream.on('close',()=>sockets.delete(upstream));upstream.on('error',()=>socket.destroy());socket.on('close',()=>upstream.destroy());upstream.on('close',()=>socket.destroy());
   if(upstream.connecting)await new Promise((resolve,reject)=>{upstream.once('connect',resolve);upstream.once('error',reject);});
   // Recheck after starting a session; cleanup may have raced its creation.
   const current=resolveSsmRoute(await listRuns(),host,targetPort);if(current.run_id!==run.run_id){upstream.destroy();throw Error('ownership');}
   socket.pause();socket.off('data',feed);socket.setTimeout(0);socket.write(Buffer.from([5,0,0,1,127,0,0,1,0,0]));if(buffer.length)upstream.write(buffer);socket.pipe(upstream);upstream.pipe(socket);socket.resume();
  })().catch(()=>{if(!socket.destroyed)socket.end(Buffer.from([5,1,0,1,0,0,0,0,0,0]));});
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
 server.closeOwned=async()=>{for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));};return server;
}
export async function startRelayControl({address,canClose,close,onStopped=()=>{}}){
 let closing=false;const control=net.createServer(socket=>{let text='';socket.setTimeout(45000,()=>socket.destroy());socket.on('error',()=>{});socket.on('data',b=>{text+=b;if(text.length>32)return socket.destroy();if(text!=='CLOSE\n')return;
  (async()=>{if(closing||!await canClose())return socket.end(JSON.stringify({error:'relay_has_active_cloud_run'}));closing=true;try{await close();socket.end(JSON.stringify({stopped:true}),onStopped);}catch{closing=false;socket.end(JSON.stringify({error:'relay_shutdown_unconfirmed'}));}})().catch(()=>socket.destroy());
 });});await new Promise((resolve,reject)=>{control.once('error',reject);control.listen(address,resolve);});return control;
}
export function requestRelayClose(address){return new Promise((resolve,reject)=>{const s=net.connect(address);let text='';s.setTimeout(45000,()=>{s.destroy();reject(Error('relay_shutdown_unconfirmed'));});s.once('connect',()=>s.write('CLOSE\n'));s.once('error',reject);s.on('data',b=>{text+=b;if(text.length>1024)s.destroy();});s.once('end',()=>{try{const r=JSON.parse(text);r.stopped===true?resolve():reject(Error(r.error==='relay_has_active_cloud_run'?r.error:'relay_shutdown_unconfirmed'));}catch{reject(Error('relay_shutdown_unconfirmed'));}});});}
export function serialTunnelOperations({start,stop,ready=async()=>{},usable=()=>true}){
 let current,queue=Promise.resolve();const serialize=fn=>{const task=queue.then(fn);queue=task.catch(()=>{});return task;};
 const closeCurrent=async()=>{if(!current)return;await stop(current);current=undefined;};
 return {peek:()=>current,ensure:run=>serialize(async()=>{if(current?.run_id===run.run_id&&current.initialized===true&&usable(current))return current;await closeCurrent();current=await start(run);await ready(current);current.initialized=true;return current;}),close:()=>serialize(closeCurrent)};
}
export function multiTunnelOperations({start,stop,ready=async()=>{},usable=()=>true,ports=[18197,18198]}){
 const owned=new Map();let queue=Promise.resolve();
 const serial=fn=>{const task=queue.then(fn);queue=task.catch(()=>{});return task;};
 const closeOne=async id=>{const item=owned.get(id);if(!item)return;await stop(item);owned.delete(id);};
 return {items:()=>[...owned.values()],ensure:run=>serial(async()=>{
  const existing=owned.get(run.run_id);if(existing?.initialized&&usable(existing))return existing;
  if(existing)await closeOne(run.run_id);
  const port=ports.find(p=>![...owned.values()].some(i=>i.localPort===p));if(port===undefined)throw Error('ssm_tunnel_limit');
  const item=await start(run,port);item.localPort=port;owned.set(run.run_id,item);await ready(item);item.initialized=true;return item;
 }),closeRun:id=>serial(()=>closeOne(id)),close:()=>serial(async()=>{let failure;for(const id of [...owned.keys()])try{await closeOne(id);}catch(e){failure??=e;}if(failure)throw failure;})};
}
const execute=promisify(execFile);
async function ownedPlugin(item,config){
 if(process.platform!=='win32'||!Number.isInteger(item.process.pid))return undefined;
 const expected=Buffer.from(config.ssm_plugin.toLowerCase()).toString('base64');
 const command=`$e=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${expected}'));$items=@(Get-CimInstance Win32_Process -Filter 'ParentProcessId = ${item.process.pid}' | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant() -eq $e -and $_.CommandLine.Contains('${item.provider_instance_id}')} | ForEach-Object {$p=Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue;if($p){@{pid=$p.Id;process_started_at=$p.StartTime.ToUniversalTime().Ticks.ToString();executable=$p.Path.ToLowerInvariant()}}});ConvertTo-Json -Compress -InputObject $items`;
 const {stdout}=await execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',command],{windowsHide:true,timeout:5000,maxBuffer:4096});const items=JSON.parse(stdout);if(items.length>1)throw Error('ssm_plugin_identity_ambiguous');return items[0];
}
async function stopOwnedPlugin(identity){
 if(!identity)return;const actual=await processIdentity(identity.pid);if(actual.state==='absent'||actual.process_started_at!==identity.process_started_at||actual.executable!==identity.executable)return;
 const expected=Buffer.from(identity.executable).toString('base64');
 const command=`$e=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${expected}'));$p=Get-Process -Id ${identity.pid} -ErrorAction SilentlyContinue;if($p -and $p.StartTime.ToUniversalTime().Ticks.ToString() -eq '${identity.process_started_at}' -and $p.Path.ToLowerInvariant() -eq $e){Stop-Process -InputObject $p -ErrorAction Stop;$p.WaitForExit(5000)|Out-Null}`;
 await execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',command],{windowsHide:true,timeout:7000,maxBuffer:4096});const after=await processIdentity(identity.pid);if(after.process_started_at===identity.process_started_at&&after.executable===identity.executable)throw Error('ssm_plugin_shutdown_unconfirmed');
}
export function createSsmTunnelManager(config,{listRuns,localPort=18197}={}){
 async function stop(item){
  item.plugin??=await ownedPlugin(item,config);let sessionError;
  try{if(item.sessionId)await awsJsonCall(config,'ssm','terminate-session',['--session-id',item.sessionId]);}catch(e){sessionError=e;}
  // The plugin is a grandchild of this relay on Windows and can outlive aws.exe.
  // Capture its exact start time/path before readiness, then stop that identity.
  await stopOwnedPlugin(item.plugin);
  if(item.process.exitCode===null&&Number.isInteger(item.process.pid)){
   if(process.platform==='win32'){try{await execute('taskkill',['/PID',String(item.process.pid),'/T','/F'],{windowsHide:true,timeout:7000,maxBuffer:4096});}catch{if(item.process.exitCode===null)throw Error('ssm_cli_shutdown_unconfirmed');}}else item.process.kill();
   for(let n=0;n<25&&item.process.exitCode===null;n++)await pause(100);
   if(item.process.exitCode===null)throw Error('ssm_cli_shutdown_unconfirmed');
  }
  if(sessionError)throw Error('ssm_session_shutdown_unconfirmed');
 }
 function start(run,localPort){
  if(!config.ssm_plugin)throw Error('ssm_plugin_required');
  const child=spawn(config.aws_cli,['--profile',config.aws_profile,'--region','us-west-2','--no-cli-pager','ssm','start-session','--target',run.resource.provider_instance_id,'--document-name','AWS-StartPortForwardingSession','--parameters',JSON.stringify({portNumber:['22'],localPortNumber:[String(localPort)]})],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PATH:dirname(config.ssm_plugin)+(process.platform==='win32'?';':':')+(process.env.PATH??''),AWS_MAX_ATTEMPTS:'1',AWS_RETRY_MODE:'standard'}});
  const item={run_id:run.run_id,host:run.resource.host,provider_instance_id:run.resource.provider_instance_id,deadline_at:run.deadline_at,process:child,ready:false};let out='';child.on('error',()=>{item.failed=true;});child.stderr.on('data',()=>{});child.stdout.on('data',b=>{out=(out+b).slice(-4096);item.sessionId??=out.match(/Starting session with SessionId:\s*([\w.@-]{1,200})/)?.[1];if(/Waiting for connections|Port \d+ opened/.test(out))item.ready=true;});return item;
 }
 const lifecycle=multiTunnelOperations({ports:[localPort,localPort+1],start,stop,usable:item=>item.ready&&item.process.exitCode===null,ready:async item=>{const deadline=Date.now()+25000;while(!item.ready||process.platform==='win32'&&!item.plugin){item.plugin??=await ownedPlugin(item,config);if(item.failed||item.process.exitCode!==null||Date.now()>deadline)throw Error('ssm_tunnel_unavailable');if(!item.ready||process.platform==='win32'&&!item.plugin)await pause(200);}}});
 const timer=setInterval(async()=>{for(const item of lifecycle.items()){try{const r=resolveSsmRoute(await listRuns(),item.host,22);if(r.run_id===item.run_id)continue;}catch{}try{await lifecycle.closeRun(item.run_id);}catch{/* Retain the owned identity and retry; shutdown cannot be acknowledged. */}}},5000);timer.unref();
 return {async connect(run){const item=await lifecycle.ensure(run);return net.connect(item.localPort,'localhost');},async close(){await lifecycle.close();clearInterval(timer);}};
}
