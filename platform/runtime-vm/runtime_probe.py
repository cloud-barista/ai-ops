"""Fixed read-only guest/process checks plus eight model HTTP requests.

Streamed via pinned SSH on an exact owned VM. Never returns raw system logs,
process arguments, paths, credentials or inference text.
"""
import json,os,time,urllib.request,subprocess
from pathlib import Path

def probe(infra_id,provider_instance_id,intent_sha256):
 root=Path('/var/tmp')/infra_id
 cfg=json.loads((root/'bootstrap-config.json').read_text())
 assert cfg['provider_instance_id']==provider_instance_id and cfg['intent_sha256']==intent_sha256
 memory={k:int(v.split()[0])*1024 for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines()) if k in ('MemTotal','MemAvailable')}
 workers=[]
 for p in Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   args=(p/'cmdline').read_bytes().split(b'\0')
   if str(root/'bert_worker.py').encode() not in args:continue
   state=(p/'stat').read_text().rsplit(')',1)[1].split()[0]
   workers.append({'pid':int(p.name),'state':state})
  except (OSError,ValueError):pass
 health={}
 try:
  with urllib.request.urlopen('http://127.0.0.1:18160/healthz',timeout=3) as r:v=json.load(r);health={'status':r.status,'model_inference':v.get('model_inference') is True,'device':v.get('device'),'flow_id':v.get('flow_id'),'group_id':v.get('group_id'),'replica_id':v.get('replica_id')}
 except Exception as e:health={'failure_type':type(e).__name__}
 passed=0;failed=[]
 for n in range(8):
  try:
   request=urllib.request.Request('http://127.0.0.1:18160/infer',data=json.dumps({'texts':['KHU diagnostic request']}).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(request,timeout=3) as r:v=json.load(r);assert r.status==200 and v.get('model_inference') is True
   passed+=1
  except Exception as e:failed.append({'index':n,'failure_type':type(e).__name__})
 try:os.readlink('/proc/1/exe');pid1_exe='readable'
 except Exception as e:pid1_exe=type(e).__name__
 ssh={}
 try:
  logs=subprocess.run(['sudo','-n','journalctl','-u','ssh','--since=-5min','--no-pager','-o','cat'],capture_output=True,text=True,timeout=5).stdout.lower()
  ssh['signals']={marker:logs.count(marker) for marker in ('accepted publickey','connection reset','unable to negotiate','refusing connection','drop connection','maxstartups','penalty','kex_exchange_identification','no matching','invalid protocol','timeout before authentication','did not receive identification','connection closed','session opened','session closed','disconnected from user','received disconnect from','fatal:','error:')}
  config=subprocess.run(['sudo','-n','/usr/sbin/sshd','-T'],capture_output=True,text=True,timeout=5).stdout
  allowed={'maxstartups','persourcemaxstartups','persourcenetblocksize','persourcepenalties','logingracetime','maxsessions','loglevel'}
  ssh['settings']={k:v for k,v in (line.split(' ',1) for line in config.splitlines()) if k in allowed}
 except Exception as e:ssh={'failure_type':type(e).__name__}
 return {'schema_version':1,'purpose':'OWNED_VM_RUNTIME_DIAGNOSIS','at':time.time(),'memory':memory,'workers':workers,'pid1_exe_access':pid1_exe,'ssh':ssh,'health':health,'local_inference':{'requested':8,'passed':passed,'failed':failed}}
