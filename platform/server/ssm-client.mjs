import {spawn} from 'node:child_process';import {mkdtemp,writeFile,unlink,rmdir} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {setTimeout as delay} from 'node:timers/promises';
const check=(value,code)=>{if(!value)throw Error(code);};
const awsCodes=new Set(['AccessDenied','AccessDeniedException','UnauthorizedOperation','InvalidInstanceId','InvalidInstanceID.NotFound','InvocationDoesNotExist','ThrottlingException','ExpiredTokenException','RequestExpired','UnrecognizedClientException']);
export const safeAwsErrorCode=value=>awsCodes.has(value)?value:undefined;
export function awsJsonCall(config,service,action,args){return new Promise((resolve,reject)=>{
 check(config.aws_cli&&config.aws_profile,'ssm_provider_configuration_required');
 const p=spawn(config.aws_cli,['--profile',config.aws_profile,'--region','us-west-2','--output','json','--no-cli-pager',service,action,...args],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,AWS_MAX_ATTEMPTS:'1',AWS_RETRY_MODE:'standard'}});
 let out='',err='',done=false;
 const finish=(error,value)=>{if(done)return;done=true;clearTimeout(timer);error?reject(error):resolve(value);};
 const timer=setTimeout(()=>{p.kill();finish(Error('ssm_provider_timeout'));},30000);
 p.stdout.on('data',b=>{out+=b;if(out.length>4194304){p.kill();finish(Error('ssm_provider_response_too_large'));}});
 p.stderr.on('data',b=>{if(err.length<8192)err+=b;});p.on('error',()=>finish(Error('ssm_provider_unavailable')));
 p.on('close',code=>{if(code!==0){const value=err.match(/An error occurred \(([A-Za-z0-9.]+)\)/)?.[1];return finish(Object.assign(Error('ssm_provider_unavailable'),awsCodes.has(value)?{aws_code:value}:{}));}try{finish(null,JSON.parse(out));}catch{finish(Error('ssm_provider_response_invalid'));}});
});}
export function createSsmClient(config={},transport){
 const invoke=transport??((service,action,args)=>awsJsonCall(config,service,action,args));
 const identity=r=>{check(/^i-[a-f0-9]{8,17}$/.test(r?.provider_instance_id??''),'owned_instance_required');return r.provider_instance_id;};
 return {
  async executeOwned(resource,{command,user},{pause=delay,clock=Date.now,deadlineAt}={}){
   const id=identity(resource);check(typeof command==='string'&&command.length>0&&Buffer.byteLength(command)<60000&&/^[a-z_][a-z0-9_-]{0,31}$/.test(user??''),'invalid_ssm_guest_command');
   const ownedDeadline=Date.parse(deadlineAt);check(Number.isFinite(ownedDeadline)&&ownedDeadline-clock()>=180000,'ssm_command_deadline_insufficient');
   const root=await mkdtemp(join(tmpdir(),'khu-ssm-')),path=join(root,'parameters.json');let sent;
   try{
    // Use a file to avoid Windows' command-line size limit. No credential is
    // accepted in this command contract; the source is built by the bridge.
    const quoted="'"+command.replaceAll("'","'\"'\"'")+"'";
    await writeFile(path,JSON.stringify({commands:['sudo -n -u '+user+' -- sh -c '+quoted],executionTimeout:['90']}),{mode:0o600});
    check(ownedDeadline-clock()>=180000,'ssm_command_deadline_insufficient');
    const data=await invoke('ssm','send-command',['--document-name','AWS-RunShellScript','--instance-ids',id,'--parameters','file://'+path,'--timeout-seconds','30']);
    sent=data.Command?.CommandId;check(/^[a-f0-9-]{36}$/.test(sent??''),'ssm_command_identity_missing');
   }finally{await unlink(path).catch(()=>{});await rmdir(root).catch(()=>{});}
   const deadline=Math.min(clock()+110000,ownedDeadline-30000);
   for(;;){let result;try{result=await invoke('ssm','get-command-invocation',['--instance-id',id,'--command-id',sent]);}catch(e){if(e.aws_code!=='InvocationDoesNotExist')throw e;}
    if(result){check(result.InstanceId===id&&result.CommandId===sent,'command_identity_mismatch');if(result.Status==='Success'){check(result.ResponseCode===0,'ssm_command_failed');return {output:result.StandardOutputContent,command_id:sent};}check(['Pending','InProgress','Delayed'].includes(result.Status),'ssm_command_failed');}
    check(clock()<deadline,'ssm_command_timeout');await pause(1500);
   }
  },
  async attach(resource){
   const id=identity(resource),data=await invoke('ec2','describe-instances',['--instance-ids',id]);
   const items=data.Reservations?.flatMap(x=>x.Instances??[])??[],instance=items.find(x=>x.InstanceId===id);
   check(items.length===1&&instance?.State?.Name==='running','owned_running_instance_required');
   check(!instance.IamInstanceProfile,'profile_already_associated');
   const attached=await invoke('ec2','associate-iam-instance-profile',['--instance-id',id,'--iam-instance-profile','Name=AmazonSSMRoleForInstancesQuickSetup']);
   const a=attached.IamInstanceProfileAssociation;check(a?.InstanceId===id&&/^iip-assoc-[A-Za-z0-9]+$/.test(a.AssociationId??''),'ssm_association_identity_mismatch');
   return {association_id:a.AssociationId,profile_name:'AmazonSSMRoleForInstancesQuickSetup'};
  },
  async online(resource){const id=identity(resource),data=await invoke('ssm','describe-instance-information',['--filters','Key=InstanceIds,Values='+id]);return data.InstanceInformationList?.some(x=>x.InstanceId===id&&x.PingStatus==='Online')===true;},
  async sendProbe(resource){
   const id=identity(resource),command=`python3 -B -c 'import json,platform;print(json.dumps(dict(probe="KHU_SSM_PROBE_V1",os=platform.system(),machine=platform.machine())))'`;
   const data=await invoke('ssm','send-command',['--document-name','AWS-RunShellScript','--instance-ids',id,'--parameters',JSON.stringify({commands:[command]}),'--timeout-seconds','30']);
   check(/^[a-f0-9-]{36}$/.test(data.Command?.CommandId??''),'ssm_command_identity_missing');return data.Command.CommandId;
  },
  async commandStatus(resource,commandId){
   const id=identity(resource);check(/^[a-f0-9-]{36}$/.test(commandId),'ssm_command_identity_missing');
   let data;try{data=await invoke('ssm','get-command-invocation',['--instance-id',id,'--command-id',commandId]);}catch(e){if(e.aws_code==='InvocationDoesNotExist')return {state:'Pending'};throw e;}
   check(data.InstanceId===id&&data.CommandId===commandId,'command_identity_mismatch');
   if(['Pending','InProgress','Delayed'].includes(data.Status))return {state:data.Status};
   check(data.Status==='Success'&&data.ResponseCode===0,'ssm_command_failed');
   let proof;try{proof=JSON.parse(data.StandardOutputContent.trim());}catch{throw Error('ssm_probe_output_invalid');}
   check(proof.probe==='KHU_SSM_PROBE_V1'&&proof.os==='Linux'&&proof.machine==='x86_64','ssm_probe_output_invalid');
   return {state:'Success',proof:{os:proof.os,machine:proof.machine}};
  }
 };
}
