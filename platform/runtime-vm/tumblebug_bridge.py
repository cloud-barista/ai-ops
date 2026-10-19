"""Fixed, credential-sanitized TB bridge streamed through authenticated SSH.

Runs in memory with -B on ubuntu216. It changes only a new, label-owned PoC
cloud VM on explicit create/terminate/guest actions; it never unseals Bao,
changes lab files, writes caches, or reconfigures lab services.
"""
import base64,datetime,hashlib,ipaddress,json,os,re,shutil,subprocess,sys,time,urllib.request,urllib.parse,urllib.error
from pathlib import Path
TYPES=('m6a.large','m6a.2xlarge','m6a.8xlarge','g6.2xlarge','g6.8xlarge','g5.xlarge')
SSH_OBSERVER_B64=''  # __KHU_SSH_OBSERVER_SOURCE__
BOOTSTRAP_FAILURES={'READ_GUARD':'guest_bootstrap_guard_failed','CHECK_CONFIG':'guest_bootstrap_already_started','WRITE_SOURCE':'guest_bootstrap_source_write_failed','WRITE_CONFIG':'guest_bootstrap_config_write_failed','OPEN_LOG':'guest_bootstrap_log_write_failed','START_PROCESS':'guest_bootstrap_process_start_failed'}
def ssh_evidence(stderr,reason):
 # Verbose OpenSSH output contains the command and credential paths. Retain
 # only fixed phase/reason enums; never persist or return the original text.
 if isinstance(stderr,bytes):stderr=stderr.decode('utf-8',errors='replace')
 lines=(stderr or '').splitlines();phase='UNKNOWN'
 for marker,value in [('debug1: Connecting to ','CONNECTING'),('debug1: Connection established.','TCP_CONNECTED'),('debug1: Remote protocol version ','SERVER_BANNER'),('debug1: SSH2_MSG_KEXINIT received','KEX_STARTED'),('debug1: SSH2_MSG_NEWKEYS received','KEY_EXCHANGE'),('Authenticated to ','AUTHENTICATED'),('debug1: Sending command: ','COMMAND_SENT')]:
  if any(line.startswith(marker) for line in lines):phase=value
 return {'phase':phase,'reason':reason}
class GuestSshFailure(ValueError):
 def __init__(self,code,stderr,reason):
  super().__init__(code);self.ssh_diagnostic=ssh_evidence(stderr,reason)
class GuestCommandPrepared(Exception):
 def __init__(self,command):self.command=command
def safe_id(value):
 if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9._+-]{1,120}',value):raise ValueError('invalid_resource_identity')
 return value
def validate(i):
 if not re.fullmatch(r'khu-poc-[a-z0-9-]{1,60}',i.get('infra_id','')) or not re.fullmatch(r'[a-f0-9]{64}',i.get('intent_sha256','')):raise ValueError('invalid_ownership')
 if i.get('candidate_id') not in TYPES or i.get('region')!='us-west-2':raise ValueError('invalid_cloud_shape')
 return i
def guard_command(i):
 deadline=datetime.datetime.fromisoformat(i['deadline_at'].replace('Z','+00:00')).timestamp()
 program='''import base64,json,math,subprocess,time
from pathlib import Path
deadline=DEADLINE
remaining=deadline-time.time()
assert 0 < remaining <= 2700
subprocess.run(['sudo','-n','shutdown','-h','+'+str(max(1,math.ceil(remaining/60)))],check=True,timeout=15)
root=Path('/var/tmp/INFRA');root.mkdir(mode=0o700,exist_ok=True)
(root/'guard.json').write_text(json.dumps({'intent_sha256':'SHA','deadline':deadline,'armed':True}))
# TB finishes its first SSH post-command before the caller can attach an IAM
# profile. Refresh only a preinstalled, active SSM service after that window;
# keep the shutdown guard intact and schedule at most once for this owned VM.
marker=root/'ssm-refresh-scheduled.json'
if remaining>120 and not marker.exists():
 for service in ('snap.amazon-ssm-agent.amazon-ssm-agent.service','amazon-ssm-agent.service'):
  if subprocess.run(['systemctl','is-active','--quiet',service],capture_output=True,timeout=5).returncode!=0:continue
  refresh=subprocess.run(['sudo','-n','systemd-run','--unit=khu-ssm-refresh-'+root.name.removeprefix('khu-poc-'),'--on-active=90s','--collect','systemctl','try-restart',service],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
  if refresh.returncode==0:marker.write_text(json.dumps({'scheduled':True,'delay_seconds':90}))
  break
observer_source=base64.b64decode('OBSERVER').decode()
if observer_source:
 unit='khu-sshdiag-'+root.name.removeprefix('khu-poc-')
 subprocess.run(['sudo','-n','systemd-run','--unit='+unit,'--collect','--property=RuntimeMaxSec=1200','--property=StandardOutput=null','--property=StandardError=null','python3','-B','-c',observer_source,str(root)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
 for attempt in range(15):
  try:
   ready=json.loads((root/'ssh-observer-ready.json').read_text())
   if ready.get('ready') is True:print('KHU_GUEST_OBSERVER_READY');break
  except (OSError,ValueError):pass
  time.sleep(.2)
 status=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=3).stdout.strip()
 if status in ('active','inactive','failed','activating'):print('KHU_GUEST_OBSERVER_STATE='+status)
print('KHU_GUEST_GUARD_ARMED')
'''.replace('DEADLINE',repr(deadline)).replace('INFRA',i['infra_id']).replace('SHA',i['intent_sha256']).replace("'OBSERVER'",repr(SSH_OBSERVER_B64))
 return "python3 -B -c 'import base64;exec(base64.b64decode(\""+base64.b64encode(program.encode()).decode()+"\"))'"
def create_payload(i):
 validate(i);safe_id(i['image_id'])
 disk=i.get('root_disk_gib',100 if i['candidate_id'].startswith(('g5.','g6.')) else 30)
 if disk!=(100 if i['candidate_id'].startswith(('g5.','g6.')) else 30):raise ValueError('reviewed_disk_required')
 return {'name':i['infra_id'],'description':'Owned KHU algorithm execution PoC','installMonAgent':'no','policyOnPartialFailure':'rollback','label':{'purpose':'khu-execution-poc','khu-intent':i['intent_sha256']},'nodeGroups':[{'name':'app','nodeGroupSize':1,'specId':'aws+us-west-2+'+i['candidate_id'],'imageId':i['image_id'],'rootDiskSize':disk,'rootDiskType':'gp3','label':{'khu-intent':i['intent_sha256']}}],'postCommandAsync':False,'postCommands':[{'command':[guard_command(i)],'nodeGroupId':'app','timeoutMinutes':1,'continueOnError':False}]}

def static_payload(i,binding):
 payload=create_payload(i);node=payload['nodeGroups'][0]
 node.update(connectionName=binding['connection_name'],vNetId=binding['vnet_id'],subnetId=binding['subnet_id'],securityGroupIds=[binding['security_group_id']],sshKeyId=binding['ssh_key_id'])
 return payload
def owned_resource(i,info):
 validate(i)
 if info.get('id')!=i['infra_id'] or info.get('label',{}).get('khu-intent')!=i['intent_sha256'] or len(info.get('node',[]))!=1:raise ValueError('ownership_mismatch')
 n=info['node'][0]
 if not re.fullmatch(r'i-[a-f0-9]{8,17}',n.get('cspResourceId','')):raise ValueError('provider_identity_missing')
 if not n.get('cspResourceName') or not n.get('connectionName'):raise ValueError('provider_identity_missing')
 return {'infra_id':i['infra_id'],'intent_sha256':i['intent_sha256'],'node_id':safe_id(n['id']),'provider_instance_id':n['cspResourceId'],'spider_name':safe_id(n['cspResourceName']),'connection_name':safe_id(n['connectionName']),'host':n.get('publicIP',''),'ssh_port':22,'ssh_key_id':n.get('sshKeyId',''),'ssh_user':n.get('nodeUserName','')}
def env_file(path):
 result={}
 for line in Path(path).read_text().splitlines():
  line=line.strip().removeprefix('export ');k,sep,v=line.partition('=')
  if sep and re.fullmatch(r'[A-Z0-9_]+',k):result[k]=v.strip().strip('"\'')
 return result

def storage_record(path,required):
 try:
  return {'available':True,'free_bytes':shutil.disk_usage(path).free,'required_free_bytes':required}
 except OSError:
  return {'available':False,'required_free_bytes':required}
def memory_file(contents):
 # Linux anonymous RAM file: no key, known_hosts file, or cache is written in
 # the shared lab filesystem. OpenSSH may close inherited FDs, so it reads the
 # live parent process's descriptor path while Python retains ownership.
 fd=os.memfd_create('khu-owned-guest',os.MFD_CLOEXEC)
 try:
  os.fchmod(fd,0o600)
  if os.write(fd,contents)!=len(contents):raise ValueError('credential_memory_failed')
  os.lseek(fd,0,os.SEEK_SET);return fd
 except Exception:os.close(fd);raise
def direct_guest_command(c,command):
 host=c.get('host','');user=c.get('user','');key=c.get('private_key','')
 if not ipaddress.IPv4Address(host).is_global or host=='192.0.2.216' or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user) or not key:raise ValueError('owned_guest_identity_required')
 parts=c.get('host_key','').split()
 if len(parts)!=2 or parts[0] not in ('ssh-rsa','ssh-ed25519','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521'):raise ValueError('verified_host_key_required')
 fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(base64.b64decode(parts[1],validate=True)).digest()).decode().rstrip('=')
 if fingerprint!=c.get('host_key_fingerprint'):raise ValueError('verified_host_key_required')
 fds=[]
 try:
  fds.append(memory_file(key.encode()));fds.append(memory_file((host+' '+c['host_key']+'\n').encode()))
  algorithm='rsa-sha2-512,rsa-sha2-256,ssh-rsa' if parts[0]=='ssh-rsa' else parts[0]
  descriptor='/proc/'+str(os.getpid())+'/fd/'
  args=['ssh','-v','-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+descriptor+str(fds[1]),'-o','GlobalKnownHostsFile=/dev/null','-o','HostKeyAlgorithms='+algorithm,'-o','ConnectTimeout=15','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3','-i',descriptor+str(fds[0]),'-p','22',user+'@'+host,command]
  # A reset before key exchange cannot have delivered the remote command.
  # Reconnect only in that proven pre-auth phase, on the same pinned host,
  # inside the existing total timeout. Never replay authenticated commands.
  deadline=time.monotonic()+70
  for attempt in range(4):
   remaining=deadline-time.monotonic()
   if remaining<=0:raise GuestSshFailure('bridge_timeout','','TIMEOUT')
   try:result=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=remaining)
   except subprocess.TimeoutExpired as error:raise GuestSshFailure('bridge_timeout',error.stderr,'TIMEOUT') from None
   if result.returncode==0:break
   evidence=ssh_evidence(result.stderr,'RESET')
   retry=not result.stdout and 'connection reset' in result.stderr.lower() and evidence['phase'] in ('CONNECTING','TCP_CONNECTED')
   if not retry or attempt==3:break
   pause=(2,5,10)[attempt]
   if deadline-time.monotonic()<=pause+1:break
   time.sleep(pause)
  if result.returncode!=0:
   # Only fixed stage markers survive; remote exception text can contain secrets.
   for line in result.stderr.splitlines():
    if line.startswith('KHU_BOOTSTRAP_FAILED:'):
     reason=BOOTSTRAP_FAILURES.get(line.removeprefix('KHU_BOOTSTRAP_FAILED:'))
     if reason:raise GuestSshFailure(reason,result.stderr,'REMOTE_EXIT')
   text=result.stderr.lower()
   for marker,code,reason in [('assertionerror','guest_remote_assertion_failed','REMOTE_EXIT'),('permission denied','guest_ssh_authentication_failed','AUTHENTICATION'),('host key verification failed','guest_host_key_mismatch','HOST_KEY'),('invalid format','guest_key_invalid','KEY_FORMAT'),('connection reset','guest_ssh_connection_failed','RESET'),('connection refused','guest_ssh_connection_failed','REFUSED'),('connection timed out','guest_ssh_connection_failed','TIMEOUT'),('connection closed','guest_ssh_connection_failed','CLOSED')]:
    if marker in text:raise GuestSshFailure(code,result.stderr,reason)
   raise GuestSshFailure('guest_command_failed',result.stderr,'UNKNOWN')
  if not result.stdout or len(result.stdout)>4194304:raise ValueError('guest_command_failed')
  return result.stdout
 finally:
  for fd in fds:os.close(fd)
class Bridge:
 def __init__(self):
  self.tbdir=Path.home()/'go/src/github.com/cloud-barista/cb-tumblebug'
  self.env=env_file(self.tbdir/'.env');self.collection=env_file(Path.home()/'geon-khudata/tumblebug-collection.env')
  base=self.collection.get('TUMBLEBUG_API_BASE_URL','http://127.0.0.1:1323/tumblebug').rstrip('/')
  u=urllib.parse.urlsplit(base)
  if u.hostname not in ('localhost','127.0.0.1') or u.scheme!='http' or u.port!=1323:raise ValueError('fixed_local_tb_required')
  self.base=base;self.ns=safe_id(self.collection.get('TUMBLEBUG_NAMESPACE','khu-poc'))
 def tb(self,path,method='GET',data=None,timeout=20):
  user=self.collection.get('TUMBLEBUG_USERNAME',self.env.get('TB_API_USERNAME','default'));password=self.env['TB_API_PASSWORD']
  headers={'Authorization':'Basic '+base64.b64encode((user+':'+password).encode()).decode(),'Content-Type':'application/json'}
  holder=self.collection.get('TUMBLEBUG_CREDENTIAL_HOLDER')
  if holder:headers['x-credential-holder']=holder
  req=urllib.request.Request(self.base+'/ns/'+self.ns+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
  with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
 def spider(self,path,method='GET',data=None):
  auth=self.env['SP_API_USERNAME']+':'+self.env['SP_API_PASSWORD']
  config='user = '+json.dumps(auth)+'\nurl = '+json.dumps('http://localhost:1024/spider'+path)+'\n'
  if method!='GET':config+='request = '+json.dumps(method)+'\nheader = "Content-Type: application/json"\ndata = '+json.dumps(json.dumps(data))+'\n'
  r=subprocess.run(['docker','exec','-i','cb-spider','curl','--silent','--fail','--max-time','15','--config','-'],input=config,text=True,capture_output=True,timeout=20,check=True)
  return json.loads(r.stdout)
 def info(self,i):return self.tb('/infra/'+i['infra_id'])
 def require_owned(self,i):
  resource=owned_resource(i,self.info(i))
  if i.get('provider_instance_id') and i['provider_instance_id']!=resource['provider_instance_id']:raise ValueError('ownership_mismatch')
  return resource
 def cmd(self,i,command):
  # TB owns provisioning/termination and supplies the already-pinned identity.
  # Use native OpenSSH for the guest so TB's repeated-handshake path is not used.
  return direct_guest_command(self.run('credential',i),command)
 def network_binding(self,i):
  refs=i.get('network_reuse',{});zone=i.get('availability_zone')
  if zone not in ('us-west-2b','us-west-2c','us-west-2d') or refs.get('availability_zone')!=zone or not re.fullmatch(r'run-[a-f0-9-]{36}',refs.get('source_run_id','')):raise ValueError('network_binding_unverified')
  expected='khu-poc-'+refs['source_run_id'][4:]
  if refs.get('security_group_id')!=expected+'-app' or refs.get('ssh_key_id')!=expected+'-aws-us-west-2':raise ValueError('network_binding_unverified')
  names={name:safe_id(refs[name]) for name in ('vnet_id','subnet_id','security_group_id','ssh_key_id')}
  vnet=self.tb('/resources/vNet/'+names['vnet_id']);group=self.tb('/resources/securityGroup/'+names['security_group_id']);key=self.tb('/resources/sshKey/'+names['ssh_key_id'])
  subnets=[s for s in vnet.get('subnetInfoList',[]) if s.get('id')==names['subnet_id'] and s.get('zone')==zone]
  if len(subnets)!=1 or vnet.get('connectionName')!='aws-us-west-2' or group.get('connectionName')!='aws-us-west-2' or key.get('connectionName')!='aws-us-west-2' or group.get('vNetId')!=names['vnet_id'] or vnet.get('status')!='Available':raise ValueError('network_binding_unverified')
  if group.get('associatedObjectList') not in (None,[]) or key.get('associatedObjectList') not in (None,[]):raise ValueError('network_binding_in_use')
  return {**names,'source_run_id':refs['source_run_id'],'availability_zone':zone,'connection_name':'aws-us-west-2','csp_vpc_id':safe_id(vnet['cspResourceId']),'csp_subnet_id':safe_id(subnets[0]['cspResourceId']),'csp_security_group_id':safe_id(group['cspResourceId']),'csp_key_name':safe_id(key['cspResourceName'])}
 def run(self,action,i):
  validate(i)
  if action=='network_review':return self.network_binding(i)
  if action in ('failed_create','clear_rejected'):
   info=self.info(i);nodes=info.get('node',[])
   if info.get('id')!=i['infra_id'] or info.get('label',{}).get('khu-intent')!=i['intent_sha256'] or len(nodes)!=1:raise ValueError('ownership_mismatch')
   node=nodes[0];uid=node.get('uid','')
   if node.get('status')!='Failed' or node.get('cspResourceId') or not re.fullmatch(r'tb[a-z0-9]{18,30}',uid) or 'InsufficientInstanceCapacity' not in json.dumps(info):raise ValueError('creation_rejection_unverified')
   if action=='clear_rejected':
    if i.get('provider_name')!=uid:raise ValueError('ownership_mismatch')
    nlbs=self.tb('/infra/'+i['infra_id']+'/nlb')
    if not isinstance(nlbs.get('nlb'),list) or nlbs['nlb']:raise ValueError('rejected_metadata_has_nlb')
    # This installed TB version returns 500 for a missing single infra. Use
    # its complete successful ID-list response instead of treating 500 as absence.
    infra_ids=self.tb('/infra?option=id').get('output')
    if not isinstance(infra_ids,list) or not all(isinstance(value,str) for value in infra_ids) or i['infra_id'] not in infra_ids or i['infra_id']+'-nlb' in infra_ids:raise ValueError('rejected_metadata_has_nlb')
    # Caller first proves all exact-uid RunInstances requests were rejected
    # and no matching EC2 instance/volume/address exists. Force removes only
    # this owned failed infra's TB metadata; it never terminates a CSP VM.
    self.tb('/infra/'+i['infra_id']+'?option=force','DELETE',timeout=20)
    return {'metadata_deleted':True}
   return {'infra_id':i['infra_id'],'intent_sha256':i['intent_sha256'],'provider_name':uid,'reason':'INSUFFICIENT_INSTANCE_CAPACITY'}
  if action=='guest_command_prepare':
   guest_action=i.get('guest_action')
   if guest_action not in ('guest_guard','guest_bootstrap','guest_bootstrap_status','guest_runtime_probe'):raise ValueError('unsupported_guest_action')
   resource=self.require_owned(i);user=resource.get('ssh_user','')
   if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',user):raise ValueError('owned_guest_identity_required')
   original=self.cmd
   def capture(request,command):raise GuestCommandPrepared(command)
   self.cmd=capture
   try:self.run(guest_action,i)
   except GuestCommandPrepared as prepared:return {'command':prepared.command,'user':user,'provider_instance_id':resource['provider_instance_id']}
   finally:self.cmd=original
   raise ValueError('unsupported_guest_action')
  if action=='review':
   payload=create_payload({**i,'deadline_at':(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=i['max_lifetime_seconds'])).isoformat()})
   # Dynamic review verifies shape/image/cost prerequisites; a selected AZ's
   # exact existing network is separately verified and bound to the review.
   result=self.tb('/infraDynamicReview','POST',payload,timeout=60)
   return {'review_reachable':True,'review_sha256':hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()}
  if action=='preflight':
   bao=subprocess.run(['docker','exec','openbao','bao','status','-format=json'],capture_output=True,text=True,timeout=15)
   sealed=json.loads(bao.stdout).get('sealed');infos=self.tb('/infra').get('infra',[])
   free=shutil.disk_usage('/var/tmp').free;required=5*1024**3
   # The streamed bridge itself writes no lab files, but the fixed TB/Spider/
   # Bao services persist state and logs on these existing host mounts. Root
   # space alone is not evidence that provisioning and cleanup can persist.
   volume=self.tbdir/'container-volume'
   services={k:storage_record(p,required) for k,p in {'tumblebug_log':volume/'cb-tumblebug-container/log','spider_log':volume/'cb-spider-container/log','openbao_data':volume/'openbao-data'}.items()}
   services['docker_data']={'available':False,'required_free_bytes':required}
   try:
    docker=subprocess.run(['docker','info','--format','{{.DockerRootDir}}'],capture_output=True,text=True,timeout=10)
    path=docker.stdout.strip()
    if docker.returncode==0 and path.startswith('/') and '\n' not in path:services['docker_data']=storage_record(path,required)
   except (OSError,subprocess.TimeoutExpired):pass
   service_free=all(s['available'] and s.get('free_bytes',0)>=required for s in services.values())
   checks={'openbao_unsealed':sealed is False,'lab_root_free':free>=required,'lab_service_storage_free':service_free,'tb_reachable':True,'unique_infra_id':not any(x.get('id')==i['infra_id'] for x in infos),'native_ssh_available':shutil.which('ssh') is not None,'anonymous_key_memory':hasattr(os,'memfd_create')}
   return {'ready':all(checks.values()),'checks':checks,'storage':{'lab_root':{'path':'/var/tmp','free_bytes':free,'required_free_bytes':required},'lab_services':services}}
  if action=='create':
   binding=i.get('network_binding')
   if i.get('availability_zone'):
    if not isinstance(binding,dict) or binding!=self.network_binding({**i,'network_reuse':binding}):raise ValueError('network_binding_changed')
    info=self.tb('/infra','POST',static_payload(i,binding),timeout=165)
   else:info=self.tb('/infraDynamic','POST',create_payload(i),timeout=165)
   return owned_resource(i,info)
  if action=='status':
   info=self.info(i);return {'state':info.get('status','UNKNOWN'),'resource':owned_resource(i,info),'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
  if action=='terminate':
   try:self.require_owned(i)
   except urllib.error.HTTPError as e:
    if e.code not in (404,410) or not re.fullmatch(r'i-[a-f0-9]{8,17}',i.get('provider_instance_id','')):raise
    name=safe_id(i['spider_name']);connection=safe_id(i['connection_name'])
    vm=self.spider('/vm/'+name+'?ConnectionName='+connection)
    if vm.get('IId',{}).get('NameId')!=name or vm['IId'].get('SystemId')!=i['provider_instance_id']:raise ValueError('ownership_mismatch')
    self.spider('/vm/'+name,'DELETE',{'ConnectionName':connection})
    return {'termination_requested':True,'source':'exact_cached_spider_identity'}
   self.tb('/infra/'+i['infra_id']+'?option=terminate','DELETE',timeout=20);return {'termination_requested':True}
  if action=='guest_guard':
   text=self.cmd(i,guard_command(i))
   if 'KHU_GUEST_GUARD_ARMED' not in text:raise ValueError('guest_guard_failed')
   state=next((s for s in ('active','inactive','failed','activating') if 'KHU_GUEST_OBSERVER_STATE='+s in text),'UNKNOWN')
   return {'armed':True,'observer_ready':'KHU_GUEST_OBSERVER_READY' in text,'observer_state':state}
  if action=='credential':
   r=self.require_owned(i);hostkey=self.tb('/infra/'+i['infra_id']+'/node/'+r['node_id']+'/sshHostKey');key=self.tb('/resources/sshKey/'+safe_id(r['ssh_key_id']))
   if not hostkey.get('fingerprint','').startswith('SHA256:') or not key.get('privateKey'):raise ValueError('ssh_identity_unavailable')
   return {'host':r['host'],'ssh_port':22,'user':r.get('ssh_user') or key.get('verifiedUsername') or key.get('username'),'private_key':key['privateKey'],'host_key_fingerprint':hostkey['fingerprint'],'host_key':hostkey.get('keyType','')+' '+hostkey.get('hostKey','')}
  if action=='guest_ssh_probe':
   # Fixed read-only commands on the same owned guest, using two separate SSH
   # connections. No model, package, log file, or arbitrary command is accepted.
   credential=self.run('credential',i);short="python3 -B -c 'print(\"KHU_SSH_PROBE_OK\")'"
   long=short+' #'+('x'*(35548-len(short.encode())-2));probes=[]
   for name,command in [('SHORT',short),('BOOTSTRAP_SIZE',long)]:
    started=time.monotonic();probe={'name':name,'command_bytes':len(command.encode())}
    try:
     if direct_guest_command(credential,command).strip()!='KHU_SSH_PROBE_OK':raise ValueError('guest_command_failed')
     probe['status']='PASSED'
    except Exception as error:
     allowed={'guest_remote_assertion_failed','guest_ssh_authentication_failed','guest_host_key_mismatch','guest_key_invalid','guest_ssh_connection_failed','guest_command_failed','bridge_timeout'}
     probe.update(status='FAILED',diagnostic_code=str(error) if isinstance(error,ValueError) and str(error) in allowed else 'bridge_response_invalid')
     if isinstance(error,GuestSshFailure):probe['ssh_diagnostic']=error.ssh_diagnostic
    probe['elapsed_ms']=round((time.monotonic()-started)*1000);probes.append(probe)
    if probe['status']=='FAILED':break
   return {'state':'COMPLETE','probes':probes}
  if action=='guest_runtime_probe':
   source=i.get('runtime_probe_source','')
   if not isinstance(source,str) or not 100<len(source)<10000:raise ValueError('invalid_runtime_probe')
   self.require_owned(i)
   program=source+'\nprint(json.dumps(probe('+','.join(repr(i[k]) for k in ('infra_id','provider_instance_id','intent_sha256'))+')))\n'
   command="python3 -B -c 'import base64;exec(base64.b64decode(\""+base64.b64encode(program.encode()).decode()+"\"))'"
   return json.loads(self.cmd(i,command))
  if action=='guest_bootstrap':
   source=i.get('bootstrap_source','');artifact=i.get('artifact')
   if not isinstance(source,str) or len(source)>30000 or not isinstance(artifact,dict) or len(json.dumps(artifact))>100000:raise ValueError('invalid_bootstrap_payload')
   cfg={k:i[k] for k in ('infra_id','provider_instance_id','intent_sha256','artifact','artifact_sha256','device')}
   program='''import base64,json,subprocess,sys
from pathlib import Path
root=Path('/var/tmp/INFRA')
stage='READ_GUARD';out=None
try:
 assert json.loads((root/'guard.json').read_text())['armed'] is True
 stage='CHECK_CONFIG'
 assert not (root/'bootstrap-config.json').exists()
 stage='WRITE_SOURCE'
 (root/'bootstrap.py').write_bytes(base64.b64decode('__SOURCE__'))
 stage='WRITE_CONFIG'
 (root/'bootstrap-config.json').write_bytes(base64.b64decode('__CONFIG__'))
 stage='OPEN_LOG'
 out=(root/'bootstrap-launch.log').open('ab')
 stage='START_PROCESS'
 p=subprocess.Popen(['python3','-B',str(root/'bootstrap.py'),'--config',str(root/'bootstrap-config.json')],stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
except Exception:
 print('KHU_BOOTSTRAP_FAILED:'+stage,file=sys.stderr)
 raise SystemExit(1)
finally:
 if out is not None:out.close()
print('KHU_BOOTSTRAP_STARTED')
'''.replace('INFRA',i['infra_id']).replace('__SOURCE__',base64.b64encode(source.encode()).decode()).replace('__CONFIG__',base64.b64encode(json.dumps(cfg).encode()).decode())
   command="python3 -B -c 'import base64;exec(base64.b64decode(\""+base64.b64encode(program.encode()).decode()+"\"))'"
   if 'KHU_BOOTSTRAP_STARTED' not in self.cmd(i,command):raise ValueError('bootstrap_start_failed')
   return {'state':'STARTED'}
  if action=='guest_bootstrap_status':
   command="python3 -B -c 'import json;from pathlib import Path;p=Path(\"/var/tmp/"+i['infra_id']+"/bootstrap-result.json\");print(p.read_text() if p.exists() else json.dumps({\"state\":\"PREPARING\"}))'"
   text=self.cmd(i,command)
   # Only the known bootstrap JSON is returned; provider stdout is never surfaced.
   result=json.loads(text.strip())
   if result.get('state')=='READY' and (result.get('provider_instance_id')!=i['provider_instance_id'] or result.get('intent_sha256')!=i['intent_sha256']):raise ValueError('bootstrap_identity_mismatch')
   return result
  raise ValueError('unsupported_bridge_action')
def main():
 try:
  request=json.load(sys.stdin);result=Bridge().run(request['action'],request['input']);print(json.dumps(result))
 except Exception as error:
  allowed={'guest_remote_assertion_failed','guest_ssh_authentication_failed','guest_host_key_mismatch','guest_key_invalid','guest_ssh_connection_failed','guest_command_failed','invalid_bootstrap_payload','bootstrap_start_failed','verified_host_key_required','owned_guest_identity_required','bridge_timeout'}
  allowed.update(BOOTSTRAP_FAILURES.values())
  diagnostic=str(error) if isinstance(error,ValueError) and str(error) in allowed else 'bridge_http_failed' if isinstance(error,urllib.error.HTTPError) else 'bridge_timeout' if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)) else 'bridge_response_invalid'
  failure={'code':'provider_unavailable','diagnostic_code':diagnostic}
  if isinstance(error,GuestSshFailure):failure['ssh_diagnostic']=error.ssh_diagnostic
  print(json.dumps({'error':failure}));raise SystemExit(1)
if __name__=='__main__':main()
