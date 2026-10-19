"""Bounded bootstrap in a newly owned AWS guest, never in ubuntu216."""
import hashlib,json,os,re,subprocess,sys,threading,time,urllib.request
from pathlib import Path
MODEL='google/bert_uncased_L-2_H-128_A-2';REVISION='30b0a37ccaaa32f332884b96992754e246e48c5f'
DIAG_STAGES={'START','APT_UPDATE','APT_VENV','CREATE_VENV','TORCH_INSTALL','LIBRARY_INSTALL','MODEL_FORWARD','READY','POST_READY','FAILED'}
DIAG_SIGNALS=('accepted publickey','connection closed','connection reset','maxstartups','drop connection','refusing connection','penalty','kex_exchange_identification','session opened','session closed','disconnected from user','received disconnect from','fatal:','error:')

def ssh_event_kind(message):
 text=message.lower()
 if 'fatal:' in text:
  if 'timeout before authentication' in text:return 'PREAUTH_TIMEOUT'
  if 'privilege separation' in text:return 'SSHD_PRIVSEP'
  return 'SSHD_FATAL_OTHER'
 if 'maxstartups' in text or 'drop connection' in text or 'refusing connection' in text or 'penalty' in text:return 'SERVER_RATE_LIMIT'
 if 'kex_exchange_identification' in text:
  if 'connection reset' in text:return 'KEX_RESET'
  if 'connection closed' in text:return 'KEX_CLOSED'
  return 'KEX_OTHER'
 if 'connection reset' in text:return 'PREAUTH_RESET' if '[preauth]' in text else 'CONNECTION_RESET'
 if 'connection closed' in text:return 'PREAUTH_CLOSED' if '[preauth]' in text else 'CONNECTION_CLOSED'
 if 'error:' in text:return 'SSHD_ERROR_OTHER'
 return None

def ssh_events(journal):
 events=[]
 for line in journal.splitlines():
  try:
   record=json.loads(line)
   kind=ssh_event_kind(record.get('MESSAGE',''))
   stamp=int(record.get('__REALTIME_TIMESTAMP','0'))//1000000
   if kind and 0<stamp<4102444800:events.append({'at':stamp,'kind':kind})
  except (ValueError,TypeError,AttributeError):continue
 return events[-12:]

def capture_serial_diagnostic(stage,runner=subprocess.run):
 if stage not in DIAG_STAGES:raise ValueError('invalid_diagnostic_stage')
 try:
  ssh=runner(['systemctl','is-active','ssh.service'],capture_output=True,text=True,timeout=3)
  service=ssh.stdout.strip() if ssh.stdout.strip() in ('active','inactive','failed','activating') else 'UNKNOWN'
  logs=runner(['sudo','-n','journalctl','-u','ssh.service','-u','ssh.socket','--since','-2min','--no-pager','-n','200','--output=json'],capture_output=True,text=True,timeout=5)
  source=logs.stdout.lower() if logs.returncode==0 else ''
  payload={'stage':stage,'at':int(time.time()),'ssh_service':service,'journal_available':logs.returncode==0,'signals':{k:source.count(k) for k in DIAG_SIGNALS},'events':ssh_events(logs.stdout) if logs.returncode==0 else []}
  line='KHU_BOOTSTRAP_DIAG='+json.dumps(payload,separators=(',',':'))+'\n'
  sent=runner(['sudo','-n','tee','/dev/ttyS0'],input=line,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True,timeout=3)
  return sent.returncode==0
 except (OSError,subprocess.TimeoutExpired,ValueError):return False
def validate_bootstrap(c,actual_instance):
 if not re.fullmatch(r'khu-poc-[a-z0-9-]{1,60}',c.get('infra_id','')) or not re.fullmatch(r'i-[a-f0-9]{8,17}',c.get('provider_instance_id','')) or actual_instance!=c['provider_instance_id']:raise ValueError('owned_guest_required')
def gpu_proof(p):return p.get('cuda_available') is True and str(p.get('model_device','')).startswith('cuda:') and str(p.get('input_device','')).startswith('cuda:') and p.get('forward_finite') is True
def instance_id():
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 req=urllib.request.Request('http://169.254.169.254/latest/api/token',method='PUT',headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
 token=opener.open(req,timeout=3).read().decode()
 req=urllib.request.Request('http://169.254.169.254/latest/meta-data/instance-id',headers={'X-aws-ec2-metadata-token':token})
 return opener.open(req,timeout=3).read().decode()
def bootstrap(c):
 validate_bootstrap(c,instance_id());root=Path('/var/tmp')/c['infra_id'];root.mkdir(mode=0o700,exist_ok=True)
 guard=json.loads((root/'guard.json').read_text());assert guard['armed'] is True and guard['intent_sha256']==c['intent_sha256']
 deadline=min(float(guard['deadline'])-120,time.time()+900);env=dict(os.environ,PIP_DISABLE_PIP_VERSION_CHECK='1',PYTHONDONTWRITEBYTECODE='1')
 state={'stage':'START'};stop=threading.Event()
 def observe():
  while not stop.is_set():
   capture_serial_diagnostic(state['stage']);stop.wait(15)
 threading.Thread(target=observe,daemon=True).start()
 def run(args,stage):
  state['stage']=stage;capture_serial_diagnostic(stage)
  remaining=deadline-time.time()
  if remaining<=0:raise TimeoutError('bootstrap_deadline')
  subprocess.run(args,check=True,timeout=remaining,env=env,stdout=log,stderr=log)
 with (root/'bootstrap.log').open('w') as log:
  payload=c['artifact'];packed=json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode()
  if hashlib.sha256(packed).hexdigest()!=c['artifact_sha256']:raise ValueError('artifact_hash_mismatch')
  for name,contents in payload['files'].items():
   if name not in ('bert_worker.py','requirements-cpu.txt','requirements-cuda.txt'):raise ValueError('invalid_artifact_member')
   (root/name).write_text(contents,encoding='utf-8')
  # Only the IMDS-verified new owned guest is changed.
  run(['sudo','-n','env','DEBIAN_FRONTEND=noninteractive','apt-get','update'],'APT_UPDATE')
  run(['sudo','-n','env','DEBIAN_FRONTEND=noninteractive','apt-get','install','-y','python3-venv'],'APT_VENV')
  run(['python3','-m','venv',str(root/'venv')],'CREATE_VENV');python=str(root/'venv/bin/python')
  device=c['device']
  if device not in ('cpu','cuda'):raise ValueError('invalid_device')
  run([python,'-m','pip','install','--no-cache-dir','torch==2.7.1','--index-url','https://download.pytorch.org/whl/'+('cu128' if device=='cuda' else 'cpu')],'TORCH_INSTALL')
  run([python,'-m','pip','install','--no-cache-dir','transformers==4.46.2','tokenizers==0.20.3','numpy==2.2.6','safetensors==0.8.0','psutil==7.1.0'],'LIBRARY_INSTALL')
  proof_code='''import json,os,torch,transformers,tokenizers,numpy,safetensors
from transformers import AutoTokenizer,AutoModelForSequenceClassification
torch.manual_seed(42)
device='DEVICE'; cache='CACHE'
assert device!='cuda' or torch.cuda.is_available()
t=AutoTokenizer.from_pretrained('MODEL',revision='REVISION',cache_dir=cache)
m=AutoModelForSequenceClassification.from_pretrained('MODEL',revision='REVISION',cache_dir=cache,num_labels=2).eval().to(device)
x=t(['KHU inference validation'],return_tensors='pt',padding='max_length',truncation=True,max_length=64).to(device)
with torch.inference_mode(): out=m(**x).logits
if device=='cuda':torch.cuda.synchronize()
proof={'execution_mode':'ssh_guest','cuda_available':torch.cuda.is_available(),'model_device':str(next(m.parameters()).device),'input_device':str(x['input_ids'].device),'forward_finite':bool(torch.isfinite(out).all()),'versions':{n:globals()[n].__version__ for n in ('torch','transformers','tokenizers','numpy','safetensors')},'cpu_cores':os.cpu_count(),'gpu_count':torch.cuda.device_count() if device=='cuda' else 0,'gpu_memory_bytes':torch.cuda.get_device_properties(0).total_memory if device=='cuda' else 0,'gpu_name':torch.cuda.get_device_name(0) if device=='cuda' else None}
open('PROOF','w').write(json.dumps(proof))
'''.replace('DEVICE',device).replace('CACHE',str(root/'model-cache')).replace('MODEL',MODEL).replace('REVISION',REVISION).replace('PROOF',str(root/'bootstrap-proof.json'))
  run([python,'-B','-c',proof_code],'MODEL_FORWARD');proof=json.loads((root/'bootstrap-proof.json').read_text())
  if not proof['forward_finite'] or (device=='cuda' and not gpu_proof(proof)):raise ValueError('forward_proof_failed')
  memory=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:')))*1024
  stat=os.statvfs(root);proof.update(state='READY',infra_id=c['infra_id'],provider_instance_id=c['provider_instance_id'],intent_sha256=c['intent_sha256'],artifact_sha256=c['artifact_sha256'],memory_bytes=memory,storage_bytes=stat.f_bavail*stat.f_frsize,root=str(root),device=device,completed_at=time.time())
  (root/'bootstrap-result.json').write_text(json.dumps(proof));state['stage']='READY';capture_serial_diagnostic('READY')
  # The app is launched after this proof is published. Keep the bounded,
  # sanitized serial observer alive through the first deployment attempt so
  # pre-auth SSH failures remain diagnosable even if SSH itself becomes unusable.
  state['stage']='POST_READY';stop.wait(max(0,min(360,deadline-time.time())));stop.set();return proof
def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args();c=json.loads(Path(a.config).read_text())
 try:bootstrap(c)
 except Exception:
  capture_serial_diagnostic('FAILED')
  root=Path('/var/tmp')/c['infra_id'];(root/'bootstrap-result.json').write_text(json.dumps({'state':'FAILED','error_code':'bootstrap_failed'}));raise SystemExit(1)
if __name__=='__main__':main()
