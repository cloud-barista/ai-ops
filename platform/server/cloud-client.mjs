import {isCloudGpuType} from '../cloud-types.mjs';
import {spawn} from 'node:child_process';import {readFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {gunzipSync} from 'node:zlib';
import {createSsmGuestAdapter} from './ssm-guest.mjs';
import {auditRejectedCreation} from './cloud-rejection.mjs';
import {auditNetworkBinding} from './cloud-network.mjs';
export const awsPriceSource='https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20West%20(Oregon)/Linux/index.json';
export function parseAwsPrice(data,type){const values=Object.values(data.regions?.['US West (Oregon)']??{}).filter(v=>v['Instance Type']===type&&v['Operating System']==='Linux'&&v['Pre Installed S/W']!== 'SQL Std');if(values.length!==1||!/^\d+\.\d+$/.test(values[0].price))throw Error('price_unavailable');return values[0].price;}
export async function currentAwsQuote(type){const response=await fetch(awsPriceSource,{signal:AbortSignal.timeout(20000)});if(!response.ok)throw Error('price_unavailable');let b=Buffer.from(await response.arrayBuffer());if(b[0]===31&&b[1]===139)b=gunzipSync(b);const data=JSON.parse(b);return {hourly_usd:parseAwsPrice(data,type),source:awsPriceSource,published_at:data.manifest.hawkFilePublicationDate,checked_at:new Date().toISOString()};}
export function bridgeProcessTimeout(action,staticNetwork=false){
 // The outer SSH deadline must include every bounded HTTP call made by Python.
 // A guest command can spend 70s connecting/retrying SSH inside Tumblebug.
 const budgets={review:85000,network_review:80000,preflight:65000,create:180000,status:40000,terminate:65000,failed_create:40000,clear_rejected:40000,guest_guard:150000,guest_bootstrap:150000,guest_bootstrap_status:150000,guest_ssh_probe:240000,guest_runtime_probe:150000,guest_command_prepare:85000,credential:85000};
 if(!(action in budgets))throw Error('unsupported_bridge_action');return action==='create'&&staticNetwork?260000:budgets[action];
}
const diagnostics=new Set(['guest_remote_assertion_failed','guest_ssh_authentication_failed','guest_host_key_mismatch','guest_key_invalid','guest_ssh_connection_failed','guest_command_failed','invalid_bootstrap_payload','bootstrap_start_failed','guest_bootstrap_guard_failed','guest_bootstrap_already_started','guest_bootstrap_source_write_failed','guest_bootstrap_config_write_failed','guest_bootstrap_log_write_failed','guest_bootstrap_process_start_failed','verified_host_key_required','owned_guest_identity_required','bridge_http_failed','bridge_timeout','bridge_response_invalid']);
const sshPhases=new Set(['UNKNOWN','CONNECTING','TCP_CONNECTED','SERVER_BANNER','KEX_STARTED','KEY_EXCHANGE','AUTHENTICATED','COMMAND_SENT']);
const sshReasons=new Set(['UNKNOWN','RESET','REFUSED','TIMEOUT','CLOSED','AUTHENTICATION','HOST_KEY','KEY_FORMAT','REMOTE_EXIT']);
export function providerUnavailable(diagnostic,ssh){
 const safe=diagnostics.has(diagnostic)?{diagnostic_code:diagnostic}:{};
 if(safe.diagnostic_code&&sshPhases.has(ssh?.phase)&&sshReasons.has(ssh?.reason))safe.ssh_diagnostic={phase:ssh.phase,reason:ssh.reason};
 return Object.assign(Error('provider_unavailable'),safe);
}
export function boundedProcess(executable,args,input,{timeout=30000,maxBytes=4194304}={}){return new Promise((resolve,reject)=>{
 let out='',size=0,finished=false;const p=spawn(executable,args,{windowsHide:true,stdio:['pipe','pipe','pipe']});
 const timer=setTimeout(()=>{p.kill();done(providerUnavailable('bridge_timeout'));},timeout);
 const done=(error,value)=>{if(finished)return;finished=true;clearTimeout(timer);error?reject(providerUnavailable(error.diagnostic_code,error.ssh_diagnostic)):resolve(value);};
 p.stdout.on('data',b=>{size+=b.length;if(size>maxBytes){p.kill();done(providerUnavailable('bridge_response_invalid'));}else out+=b.toString('utf8');});
 p.stderr.on('data',()=>{});p.on('error',done);p.on('close',code=>{try{const value=JSON.parse(out);if(code!==0||value.error)return done(providerUnavailable(value.error?.diagnostic_code,value.error?.ssh_diagnostic));done(null,value);}catch{done(providerUnavailable('bridge_response_invalid'));}});p.stdin.on('error',()=>{});p.stdin.end(input);
 });}
export async function bridgeSource(){
 const source=await readFile(fileURLToPath(new URL('../runtime-vm/tumblebug_bridge.py',import.meta.url)),'utf8');
 const observer=await readFile(fileURLToPath(new URL('../runtime-vm/ssh_observer.py',import.meta.url)),'utf8');
 return source.replace("SSH_OBSERVER_B64=''  # __KHU_SSH_OBSERVER_SOURCE__",`SSH_OBSERVER_B64='${Buffer.from(observer).toString('base64')}'`);
}
export async function bridgeEnvelope(action,input){
 return {command:`python3 -B -c 'import io,json,sys; e=json.load(sys.stdin); sys.stdin=io.StringIO(json.dumps(e["request"])); exec(compile(e["source"],"khu-bridge","exec"))'`,input:JSON.stringify({source:await bridgeSource(),request:{action,input}})};
}
export function createCloudClient(config={},transport){
 if((config.host&&config.host!=='192.0.2.216')||(config.port&&config.port!==7877)||(config.user&&config.user!=='lab-user'))throw Error('fixed_cloud_bridge_required');
 const invoke=async(action,input)=>{try{if(transport)return await transport(action,input);if(!config.ssh_key)throw Error('cloud_connection_required');const envelope=await bridgeEnvelope(action,input);return await boundedProcess('ssh',['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','-i',config.ssh_key,'-p','7877','lab-user@192.0.2.216',envelope.command],envelope.input,{timeout:bridgeProcessTimeout(action,!!input?.availability_zone)});}catch(e){throw providerUnavailable(e.diagnostic_code,e.ssh_diagnostic);}};
 const guestInvoke=config.management_transport==='ssm'?createSsmGuestAdapter(config,{invoke}):invoke;
 const aws=async(args)=>{if(!config.aws_profile||!config.aws_cli)throw Error('provider_audit_unconfigured');return boundedProcess(config.aws_cli,['--profile',config.aws_profile,'--region','us-west-2','--output','json','--no-cli-pager','ec2',...args],'',{timeout:20000});};
 const provider=(service,action,args)=>boundedProcess(config.aws_cli,['--profile',config.aws_profile,'--region','us-west-2','--output','json','--no-cli-pager',service,action,...args],'',{timeout:20000});
 const networkBinding=async intent=>auditNetworkBinding({intent,binding:await invoke('network_review',{...intent,network_reuse:config.network_reuse}),provider});
 async function instance(r){if(!/^i-[a-f0-9]{8,17}$/.test(r.provider_instance_id))throw Error('invalid_instance_identity');const d=await aws(['describe-instances','--instance-ids',r.provider_instance_id]),i=d.Reservations?.flatMap(x=>x.Instances??[]).find(x=>x.InstanceId===r.provider_instance_id);if(!i)throw Error('provider_unavailable');return i;}
 async function capture(r){const i=await instance(r);if(i.State.Name==='terminated')throw Error('attachment_identity_missing');const a=await aws(['describe-addresses','--filters','Name=instance-id,Values='+r.provider_instance_id]);return {...r,availability_zone:i.Placement?.AvailabilityZone,attachment_ids:{volumes:(i.BlockDeviceMappings??[]).map(x=>x.Ebs?.VolumeId).filter(Boolean),elastic_ips:(a.Addresses??[]).map(x=>x.AllocationId).filter(Boolean),captured_at:new Date().toISOString()}};}
 return {
  async review(intent){const quote=await currentAwsQuote(intent.candidate_id),image_id=config.image_ids?.[isCloudGpuType(intent.candidate_id)?'gpu':'cpu'];if(!image_id||!config.ssh_key)return {...quote,image_id:null,ready_for_approval:false,connection_state:'CONFIGURATION_REQUIRED'};const binding=intent.availability_zone?await networkBinding(intent):undefined;const reviewed=await invoke('review',{...intent,image_id});return {...quote,...reviewed,image_id,...binding?{network_binding:binding}:{},ready_for_approval:!!config.aws_cli&&!!config.aws_profile};},
  async preflight(intent){const bridge=await invoke('preflight',intent);let audit=false,network=true;try{const d=await aws(['describe-regions','--region-names','us-west-2']);await aws(['describe-instances','--max-results','5']);await aws(['describe-volumes','--max-results','5']);await aws(['describe-addresses']);audit=d.Regions?.some(r=>r.RegionName==='us-west-2')===true;}catch{}if(intent.availability_zone){try{network=JSON.stringify(await networkBinding(intent))===JSON.stringify(intent.network_binding);}catch{network=false;}}return {...bridge,ready:bridge.ready===true&&audit&&network,checks:{...bridge.checks,provider_attachment_audit:audit,existing_network_binding:network}};},
  async create(intent){const r=await invoke('create',intent);try{return await capture(r);}catch{return {...r,attachment_audit_state:'UNAVAILABLE'};}},
  async status(r){const s=await invoke('status',r);if(s.resource){if(r.attachment_ids)s.resource={...s.resource,attachment_ids:r.attachment_ids};else try{s.resource=await capture(s.resource);}catch{s.resource={...s.resource,attachment_audit_state:'UNAVAILABLE'};}}return s;},
  terminate:r=>invoke('terminate',r),
  rejectedCreation:run=>auditRejectedCreation({run,bridge:invoke,provider:(service,action,args)=>boundedProcess(config.aws_cli,['--profile',config.aws_profile,'--region','us-west-2','--output','json','--no-cli-pager',service,action,...args],'',{timeout:20000})}),
  clearRejected:(intent,proof)=>invoke('clear_rejected',{...intent,provider_name:proof.provider_name}),
  async providerStatus(r){const i=await instance(r);return {instance_id:i.InstanceId,state:i.State.Name==='terminated'?'Terminated':i.State.Name,source:'aws-describe-instances',checked_at:new Date().toISOString()};},
  async billableAttachments(r){if(!r.attachment_ids?.captured_at)return {verified:false,remaining:[]};const remaining=[];for(const id of r.attachment_ids.volumes){if(!/^vol-[a-f0-9]+$/.test(id))throw Error('invalid_attachment');const d=await aws(['describe-volumes','--filters','Name=volume-id,Values='+id]);if(d.Volumes?.some(v=>v.VolumeId===id))remaining.push({type:'volume',id});}for(const id of r.attachment_ids.elastic_ips){if(!/^eipalloc-[a-f0-9]+$/.test(id))throw Error('invalid_attachment');const d=await aws(['describe-addresses','--filters','Name=allocation-id,Values='+id]);if(d.Addresses?.some(a=>a.AllocationId===id))remaining.push({type:'elastic_ip',id});}return {verified:true,remaining,checked_at:new Date().toISOString(),source:'aws-read-only-audit'};},
  guestGuard:r=>guestInvoke('guest_guard',r),guestSshProbe:r=>invoke('guest_ssh_probe',r),guestCapacity:r=>invoke('guest_capacity',r),credential:r=>invoke('credential',r),guestBootstrap:async r=>guestInvoke('guest_bootstrap',{...r,bootstrap_source:await readFile(fileURLToPath(new URL('../runtime-vm/bootstrap.py',import.meta.url)),'utf8')}),guestBootstrapStatus:r=>guestInvoke('guest_bootstrap_status',r),
  guestRuntimeProbe:async r=>guestInvoke('guest_runtime_probe',{...r,runtime_probe_source:await readFile(fileURLToPath(new URL('../runtime-vm/runtime_probe.py',import.meta.url)),'utf8')}),
 };
}
