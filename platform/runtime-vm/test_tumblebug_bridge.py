import unittest,base64,re,hashlib
from unittest.mock import patch
import tumblebug_bridge as bridge_module
from tumblebug_bridge import create_payload, owned_resource, Bridge
import urllib.error
import tempfile,json,contextlib,io
from pathlib import Path

class BridgeTests(unittest.TestCase):
 def test_banner_and_kex_are_distinct_from_tcp_only_failures(self):
  for log,phase in [('debug1: Connection established.\ndebug1: Remote protocol version 2.0, remote software version OpenSSH_9.6','SERVER_BANNER'),('debug1: Remote protocol version 2.0\ndebug1: SSH2_MSG_KEXINIT received','KEX_STARTED')]:
   self.assertEqual(bridge_module.ssh_evidence(log,'RESET'),{'phase':phase,'reason':'RESET'})
 def setUp(self):
  self.intent={'infra_id':'khu-poc-123','intent_sha256':'a'*64,'candidate_id':'m6a.2xlarge','region':'us-west-2','image_id':'ami-123','root_disk_gib':30,'max_lifetime_seconds':1800,'deadline_at':'2026-10-05T10:00:00+00:00'}
 def test_create_has_single_owned_node_and_guard_before_install(self):
  r=create_payload(self.intent)
  self.assertEqual(r['nodeGroups'][0]['nodeGroupSize'],1)
  self.assertEqual(r['label']['khu-intent'],self.intent['intent_sha256'])
  program=base64.b64decode(re.search(r'b64decode\("([A-Za-z0-9+/=]+)"',r['postCommands'][0]['command'][0])[1]).decode()
  self.assertIn("['sudo','-n','shutdown'",program);self.assertIn('/var/tmp/khu-poc-123',program);self.assertEqual(r['installMonAgent'],'no')
 def test_a10g_fallback_keeps_gpu_disk_and_exact_spec(self):
  payload=create_payload({**self.intent,'candidate_id':'g5.xlarge','root_disk_gib':100})
  self.assertEqual(payload['nodeGroups'][0]['specId'],'aws+us-west-2+g5.xlarge')
  self.assertEqual(payload['nodeGroups'][0]['rootDiskSize'],100)
  with self.assertRaises(ValueError):create_payload({**self.intent,'candidate_id':'g5.xlarge','root_disk_gib':30})
 def test_guest_guard_schedules_one_deferred_refresh_of_an_existing_ssm_agent_without_rebooting(self):
  intent={**self.intent,'deadline_at':(bridge_module.datetime.datetime.now(bridge_module.datetime.timezone.utc)+bridge_module.datetime.timedelta(seconds=600)).isoformat()}
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);calls=[]
   command=bridge_module.guard_command(intent)
   program=base64.b64decode(re.search(r'b64decode\("([A-Za-z0-9+/=]+)"',command)[1]).decode().replace('/var/tmp/khu-poc-123',root.as_posix())
   def run(args,**kwargs):
    calls.append(args)
    return type('Result',(),{'returncode':0,'stdout':'active\n'})()
   with patch.object(bridge_module.subprocess,'run',run),contextlib.redirect_stdout(io.StringIO()):
    exec(compile(program,'guard-fixture','exec'),{});exec(compile(program,'guard-fixture','exec'),{})
   refresh=[args for args in calls if 'systemd-run' in args and '--on-active=90s' in args]
   self.assertEqual(len(refresh),1)
   self.assertEqual(refresh[0][-3:],['systemctl','try-restart','snap.amazon-ssm-agent.amazon-ssm-agent.service'])
   self.assertNotIn('reboot',program)
 def test_observer_is_a_bounded_transient_service_with_a_startup_receipt(self):
  with patch.object(bridge_module,'SSH_OBSERVER_B64',base64.b64encode(b'# observer fixture').decode()):
   command=bridge_module.guard_command(self.intent)
  program=base64.b64decode(re.search(r'b64decode\("([A-Za-z0-9+/=]+)"',command)[1]).decode()
  self.assertIn('systemd-run',program);self.assertIn('RuntimeMaxSec=1200',program)
  self.assertIn('ssh-observer-ready.json',program)
 def test_injected_observer_code_does_not_rewrite_startup_receipt_markers(self):
  intent={**self.intent,'deadline_at':(bridge_module.datetime.datetime.now(bridge_module.datetime.timezone.utc)+bridge_module.datetime.timedelta(seconds=60)).isoformat()}
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   with patch.object(bridge_module,'SSH_OBSERVER_B64',base64.b64encode(b'# observer fixture').decode()):
    command=bridge_module.guard_command(intent)
   program=base64.b64decode(re.search(r'b64decode\("([A-Za-z0-9+/=]+)"',command)[1]).decode().replace('/var/tmp/khu-poc-123',root.as_posix())
   def run(args,**kwargs):
    if 'systemd-run' in args:(root/'ssh-observer-ready.json').write_text(json.dumps({'ready':True}))
    return type('Result',(),{'returncode':0,'stdout':'active' if args[0]=='systemctl' else ''})()
   output=io.StringIO()
   with patch.object(bridge_module.subprocess,'run',run),contextlib.redirect_stdout(output):exec(compile(program,'guard-fixture','exec'),{})
   self.assertIn('KHU_GUEST_OBSERVER_READY\n',output.getvalue())
   self.assertIn('KHU_GUEST_OBSERVER_STATE=active\n',output.getvalue())
 def test_foreign_label_is_never_a_cleanup_target(self):
  with self.assertRaises(ValueError):owned_resource(self.intent,{'id':'khu-poc-123','label':{'khu-intent':'b'*64},'node':[]})
 def test_only_exact_owned_node_identity_is_returned(self):
  info={'id':'khu-poc-123','label':{'khu-intent':'a'*64},'node':[{'id':'app-1','cspResourceId':'i-0123456789abcdef0','cspResourceName':'sp-name','connectionName':'aws-us-west-2','publicIP':'1.2.3.4'}]}
  self.assertEqual(owned_resource(self.intent,info)['provider_instance_id'],'i-0123456789abcdef0')
  info['node'].append(dict(info['node'][0]));self.assertRaises(ValueError,owned_resource,self.intent,info)
 def test_ssm_command_preparation_preserves_ownership_and_rejects_arbitrary_actions(self):
  b=Bridge.__new__(Bridge);b.require_owned=lambda i:{'provider_instance_id':'i-0123456789abcdef0','ssh_user':'ubuntu'}
  request={**self.intent,'provider_instance_id':'i-0123456789abcdef0','guest_action':'guest_bootstrap_status'}
  result=b.run('guest_command_prepare',request)
  self.assertEqual(result['user'],'ubuntu');self.assertIn('bootstrap-result.json',result['command']);self.assertNotIn('private_key',json.dumps(result))
  request['guest_action']='terminate'
  with self.assertRaisesRegex(ValueError,'unsupported_guest_action'):b.run('guest_command_prepare',request)
 def test_rejected_vm_metadata_cleanup_refuses_any_nlb_or_sibling_infra(self):
  for nlbs,sibling in [([],False),([{'id':'nlb-owned'}],False),([],True)]:
   b=Bridge.__new__(Bridge);calls=[];uid='tbowned12345678901234';info={'id':self.intent['infra_id'],'label':{'khu-intent':self.intent['intent_sha256']},'status':'Failed:1','node':[{'uid':uid,'status':'Failed','cspResourceId':'','systemMessage':'InsufficientInstanceCapacity'}]};b.info=lambda i:info
   def tb(path,method='GET',data=None,timeout=20):
    calls.append((path,method))
    if path.endswith('/nlb'):return {'nlb':nlbs}
    if path=='/infra?option=id':return {'output':[self.intent['infra_id']]+([self.intent['infra_id']+'-nlb'] if sibling else [])}
    if path.endswith('-nlb'):
     if sibling:return {'id':'unexpected-sibling'}
     raise urllib.error.HTTPError('fixture',404,'missing',{},None)
    return {}
   b.tb=tb;request={**self.intent,'provider_name':uid}
   if nlbs or sibling:
    with self.assertRaisesRegex(ValueError,'rejected_metadata_has_nlb'):b.run('clear_rejected',request)
    self.assertFalse(any(method=='DELETE' for _,method in calls))
   else:self.assertTrue(b.run('clear_rejected',request)['metadata_deleted']);self.assertIn(('/infra/'+self.intent['infra_id']+'?option=force','DELETE'),calls)
 def test_static_create_uses_only_reviewed_existing_network_ids_and_returned_user(self):
  binding={'connection_name':'aws-us-west-2','vnet_id':'existing-vnet','subnet_id':'existing-subnet','security_group_id':'existing-sg','ssh_key_id':'existing-key'}
  payload=bridge_module.static_payload(self.intent,binding);node=payload['nodeGroups'][0]
  self.assertEqual(node['vNetId'],'existing-vnet');self.assertEqual(node['subnetId'],'existing-subnet');self.assertEqual(node['securityGroupIds'],['existing-sg']);self.assertEqual(node['sshKeyId'],'existing-key');self.assertNotIn('nodeUserName',node);self.assertNotIn('vNetTemplateId',node);self.assertEqual(payload['label']['khu-intent'],self.intent['intent_sha256'])
 def test_network_review_reads_exact_unused_reused_resources_and_never_returns_key_material(self):
  source='run-3488fb68-3f17-4fa1-aa47-52293f174563';prefix='khu-poc-'+source[4:];refs={'source_run_id':source,'availability_zone':'us-west-2b','vnet_id':'base-vnet','subnet_id':'subnet-b','security_group_id':prefix+'-app','ssh_key_id':prefix+'-aws-us-west-2'}
  b=Bridge.__new__(Bridge);vnet={'connectionName':'aws-us-west-2','status':'Available','cspResourceId':'vpc-owned','subnetInfoList':[{'id':'subnet-b','zone':'us-west-2b','cspResourceId':'subnet-owned'}]};sg={'connectionName':'aws-us-west-2','vNetId':'base-vnet','cspResourceId':'sg-owned','associatedObjectList':[]};key={'connectionName':'aws-us-west-2','cspResourceName':'tb-key','privateKey':'PRIVATE_DO_NOT_RETURN'}
  b.tb=lambda path:vnet if '/vNet/' in path else sg if '/securityGroup/' in path else key
  result=b.run('network_review',{**self.intent,'availability_zone':'us-west-2b','network_reuse':refs});self.assertEqual(result['csp_subnet_id'],'subnet-owned');self.assertNotIn('PRIVATE_DO_NOT_RETURN',json.dumps(result));sg['associatedObjectList']=['other-vm']
  with self.assertRaisesRegex(ValueError,'network_binding_in_use'):b.run('network_review',{**self.intent,'availability_zone':'us-west-2b','network_reuse':refs})
 def test_missing_tb_metadata_uses_only_exact_cached_spider_provider_identity(self):
  b=Bridge.__new__(Bridge);calls=[]
  def tb(*a,**k):raise urllib.error.HTTPError('fixture',404,'missing',{},None)
  def spider(path,method='GET',data=None):
   calls.append(method)
   return {'IId':{'NameId':'owned-spider','SystemId':'i-0123456789abcdef0'}} if method=='GET' else {'Status':'Terminating'}
  b.tb=tb;b.spider=spider
  i={**self.intent,'provider_instance_id':'i-0123456789abcdef0','spider_name':'owned-spider','connection_name':'aws-us-west-2'}
  self.assertTrue(b.run('terminate',i)['termination_requested']);self.assertEqual(calls,['GET','DELETE'])
  calls.clear();i['provider_instance_id']='i-fffffffffffffffff'
  with self.assertRaises(ValueError):b.run('terminate',i)
  self.assertEqual(calls,['GET'])
 def test_native_guest_command_requires_the_exact_pinned_host_key_and_keeps_private_key_out_of_arguments(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  saved=[]
  def memory(contents):saved.append(contents);return 80+len(saved)
  def command(args,**kwargs):
   self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('IdentitiesOnly=yes',args);self.assertIn('UserKnownHostsFile=/proc/'+str(bridge_module.os.getpid())+'/fd/82',args)
   self.assertNotIn('PRIVATE_FIXTURE',' '.join(args))
   return type('Result',(),{'returncode':0,'stdout':'{"state":"PREPARING"}','stderr':''})()
  with patch.object(bridge_module,'memory_file',memory,create=True),patch.object(bridge_module.subprocess,'run',command),patch.object(bridge_module.os,'close'):
   self.assertEqual(bridge_module.direct_guest_command(credential,'read-only-fixture'),' {"state":"PREPARING"}'.strip())
  self.assertEqual(saved[0],b'PRIVATE_FIXTURE');self.assertIn(b'34.1.2.3 ecdsa-sha2-nistp256 ',saved[1])
  credential['host_key_fingerprint']='SHA256:WRONG'
  with self.assertRaisesRegex(ValueError,'host_key'):bridge_module.direct_guest_command(credential,'read-only-fixture')
 def test_ssh_username_comes_from_the_owned_vm_when_the_key_resource_has_no_username(self):
  info={'id':'khu-poc-123','label':{'khu-intent':'a'*64},'node':[{'id':'app-1','cspResourceId':'i-0123456789abcdef0','cspResourceName':'sp-name','connectionName':'aws-us-west-2','publicIP':'34.1.2.3','sshKeyId':'owned-key','nodeUserName':'cb-user'}]}
  b=Bridge.__new__(Bridge);b.info=lambda i:info
  b.tb=lambda path:{'fingerprint':'SHA256:pinned','keyType':'ecdsa-sha2-nistp256','hostKey':'wire'} if path.endswith('/sshHostKey') else {'privateKey':'private-fixture','keyValueList':[]}
  c=b.run('credential',self.intent);self.assertEqual(c['user'],'cb-user');self.assertEqual(c['host'],'34.1.2.3')
 def test_remote_assertion_is_a_safe_diagnostic_without_returning_remote_traceback(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  result=type('Result',(),{'returncode':1,'stdout':'','stderr':'Traceback: password=DO_NOT_PRINT\nAssertionError'})()
  with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',return_value=result),patch.object(bridge_module.os,'close'):
   with self.assertRaisesRegex(ValueError,'^guest_remote_assertion_failed$'):bridge_module.direct_guest_command(credential,'fixture')
 def test_generated_bootstrap_launcher_roundtrips_artifact_and_detaches_with_closed_standard_input(self):
  request={**self.intent,'provider_instance_id':'i-0123456789abcdef0','artifact':{'files':{'bert_worker.py':'# Korean UTF8: 경희대\n'},'manifest':{}},'artifact_sha256':'b'*64,'device':'cpu','bootstrap_source':'# bootstrap fixture\n'}
  b=Bridge.__new__(Bridge)
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'guard.json').write_text(json.dumps({'armed':True}))
   def command(i,value):
    program=base64.b64decode(re.search(r'b64decode\("([A-Za-z0-9+/=]+)"',value)[1]).decode().replace('/var/tmp/khu-poc-123',root.as_posix())
    output=io.StringIO()
    namespace={}
    with patch.object(bridge_module.subprocess,'Popen') as popen,contextlib.redirect_stdout(output):exec(compile(program,'guest-launcher','exec'),namespace)
    self.assertTrue(popen.call_args.kwargs['start_new_session']);self.assertEqual(popen.call_args.kwargs['stdin'],bridge_module.subprocess.DEVNULL)
    namespace['out'].close()
    return output.getvalue()
   b.cmd=command
   self.assertEqual(b.run('guest_bootstrap',request)['state'],'STARTED')
   self.assertEqual((root/'bootstrap.py').read_text(),request['bootstrap_source']);self.assertEqual(json.loads((root/'bootstrap-config.json').read_text())['artifact'],request['artifact'])

 def test_launcher_failure_identifies_guard_or_spawn_without_leaking_the_exception_text(self):
  request={**self.intent,'provider_instance_id':'i-0123456789abcdef0','artifact':{},'artifact_sha256':'b'*64,'device':'cpu','bootstrap_source':'# fixture\n'}
  b=Bridge.__new__(Bridge)
  for armed,expected in [(False,'READ_GUARD'),(True,'START_PROCESS')]:
   with self.subTest(stage=expected),tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    if armed:(root/'guard.json').write_text(json.dumps({'armed':True}))
    def command(i,value):
     program=base64.b64decode(re.search(r'b64decode\("([A-Za-z0-9+/=]+)"',value)[1]).decode().replace('/var/tmp/khu-poc-123',root.as_posix())
     output,errors=io.StringIO(),io.StringIO();namespace={};failure=None
     try:
      with patch.object(bridge_module.subprocess,'Popen',side_effect=OSError('private_key=DO_NOT_PRINT')),contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
       try:exec(compile(program,'guest-launcher','exec'),namespace)
       except (Exception,SystemExit) as error:failure=error
     finally:
      if namespace.get('out') is not None:namespace['out'].close()
     self.assertIsInstance(failure,SystemExit);self.assertEqual(failure.code,1);self.assertEqual(errors.getvalue().strip(),'KHU_BOOTSTRAP_FAILED:'+expected);self.assertNotIn('DO_NOT_PRINT',errors.getvalue());self.assertNotIn('KHU_BOOTSTRAP_STARTED',output.getvalue())
     return 'fixture-complete'
    b.cmd=command
    with self.assertRaisesRegex(ValueError,'bootstrap_start_failed'):b.run('guest_bootstrap',request)

 def test_native_guest_command_preserves_the_fixed_bootstrap_failure_stage(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  result=type('Result',(),{'returncode':1,'stdout':'','stderr':'KHU_BOOTSTRAP_FAILED:START_PROCESS\nprivate_key=DO_NOT_PRINT'})()
  with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',return_value=result),patch.object(bridge_module.os,'close'):
   with self.assertRaisesRegex(ValueError,'^guest_bootstrap_process_start_failed$'):bridge_module.direct_guest_command(credential,'fixture')

 def test_ssh_failure_distinguishes_handshake_reset_from_reset_after_command_without_raw_debug_output(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  for debug,phase in [('debug1: Connecting to 34.1.2.3 [34.1.2.3] port 22.\ndebug1: Connection established.\nkex_exchange_identification: read: Connection reset by peer','TCP_CONNECTED'),('debug1: Connection established.\nAuthenticated to 34.1.2.3 using publickey.\ndebug1: Sending command: private_key=DO_NOT_PRINT\nclient_loop: send disconnect: Connection reset by peer','COMMAND_SENT')]:
   with self.subTest(phase=phase):
    result=type('Result',(),{'returncode':255,'stdout':'','stderr':debug})()
    with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',return_value=result) as process,patch.object(bridge_module.os,'close'),patch.object(bridge_module.time,'sleep'):
     with self.assertRaisesRegex(ValueError,'^guest_ssh_connection_failed$') as failure:bridge_module.direct_guest_command(credential,'fixture')
    self.assertIn('-v',process.call_args.args[0]);self.assertEqual(getattr(failure.exception,'ssh_diagnostic',None),{'phase':phase,'reason':'RESET'})
    self.assertNotIn('DO_NOT_PRINT',json.dumps(vars(failure.exception)))

 def test_pre_auth_reset_reconnects_with_the_same_identity_before_any_remote_command_runs(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  reset=type('Result',(),{'returncode':255,'stdout':'','stderr':'debug1: Connection established.\nkex_exchange_identification: read: Connection reset by peer'})()
  success=type('Result',(),{'returncode':0,'stdout':'KHU_BOOTSTRAP_STARTED','stderr':''})()
  with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',side_effect=[reset,success]) as process,patch.object(bridge_module.os,'close'),patch.object(bridge_module.time,'sleep') as pause:
   try: result=bridge_module.direct_guest_command(credential,'one-owned-bootstrap-command')
   except bridge_module.GuestSshFailure:self.fail('An unauthenticated reset must reconnect without retrying an executed command')
  self.assertEqual(result,'KHU_BOOTSTRAP_STARTED');self.assertEqual(process.call_count,2)
  self.assertEqual(process.call_args_list[0].args,process.call_args_list[1].args)
  self.assertEqual(pause.call_args_list[0].args,(2,))

 def test_repeated_pre_auth_resets_have_four_attempts_and_keep_safe_failure_evidence(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  reset=type('Result',(),{'returncode':255,'stdout':'','stderr':'debug1: Connection established.\nkex_exchange_identification: read: Connection reset by peer\nprivate_key=DO_NOT_PRINT'})()
  with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',return_value=reset) as process,patch.object(bridge_module.os,'close'),patch.object(bridge_module.time,'sleep') as pause:
   with self.assertRaises(bridge_module.GuestSshFailure) as failure:bridge_module.direct_guest_command(credential,'one-owned-bootstrap-command')
  self.assertEqual(process.call_count,4);self.assertEqual([c.args[0] for c in pause.call_args_list],[2,5,10])
  self.assertEqual(failure.exception.ssh_diagnostic,{'phase':'TCP_CONNECTED','reason':'RESET'})
  self.assertNotIn('DO_NOT_PRINT',json.dumps(vars(failure.exception)))

 def test_authenticated_or_command_sent_reset_is_never_retried(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  for sent in [False,True]:
   debug='debug1: Connection established.\nAuthenticated to 34.1.2.3 using publickey.\n'+('debug1: Sending command: fixture\n' if sent else '')+'Connection reset by peer'
   reset=type('Result',(),{'returncode':255,'stdout':'','stderr':debug})()
   with self.subTest(command_sent=sent),patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',return_value=reset) as process,patch.object(bridge_module.os,'close'),patch.object(bridge_module.time,'sleep') as pause:
    with self.assertRaises(bridge_module.GuestSshFailure):bridge_module.direct_guest_command(credential,'one-owned-bootstrap-command')
   self.assertEqual(process.call_count,1);pause.assert_not_called()

 def test_reconnections_share_the_existing_seventy_second_deadline(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  reset=type('Result',(),{'returncode':255,'stdout':'','stderr':'debug1: Connection established.\nConnection reset by peer'})();clock=[0.0];timeouts=[]
  def command(*args,**kwargs):
   timeouts.append(kwargs['timeout']);clock[0]+=60 if len(timeouts)==1 else 8;return reset
  def pause(seconds):clock[0]+=seconds
  with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',side_effect=command),patch.object(bridge_module.os,'close'),patch.object(bridge_module.time,'monotonic',side_effect=lambda:clock[0]),patch.object(bridge_module.time,'sleep',side_effect=pause):
   with self.assertRaises(bridge_module.GuestSshFailure):bridge_module.direct_guest_command(credential,'one-owned-bootstrap-command')
  self.assertEqual(timeouts,[70,8]);self.assertEqual(clock[0],70)

 def test_ssh_timeout_preserves_last_observed_phase_and_bridge_emits_only_fixed_fields(self):
  wire=base64.b64encode(b'host-key-fixture').decode();fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key-fixture').digest()).decode().rstrip('=')
  credential={'host':'34.1.2.3','user':'cb-user','private_key':'PRIVATE_FIXTURE','host_key':'ecdsa-sha2-nistp256 '+wire,'host_key_fingerprint':fingerprint}
  timeout=bridge_module.subprocess.TimeoutExpired(['private_key=DO_NOT_PRINT'],70,stderr=b'Authenticated to 34.1.2.3 using publickey.\nprivate_key=DO_NOT_PRINT')
  with patch.object(bridge_module,'memory_file',side_effect=[81,82]),patch.object(bridge_module.subprocess,'run',side_effect=timeout),patch.object(bridge_module.os,'close'):
   try:bridge_module.direct_guest_command(credential,'fixture')
   except Exception as error:failure=error
  output=io.StringIO()
  with patch.object(bridge_module,'Bridge') as bridge,patch.object(bridge_module.sys,'stdin',io.StringIO(json.dumps({'action':'guest_bootstrap','input':{}}))),contextlib.redirect_stdout(output):
   bridge.return_value.run.side_effect=failure
   with self.assertRaises(SystemExit):bridge_module.main()
  payload=json.loads(output.getvalue())
  self.assertEqual(payload,{'error':{'code':'provider_unavailable','diagnostic_code':'bridge_timeout','ssh_diagnostic':{'phase':'AUTHENTICATED','reason':'TIMEOUT'}}})
  self.assertNotIn('DO_NOT_PRINT',output.getvalue())

 def test_guest_ssh_probe_runs_only_fixed_read_only_commands_and_stops_after_failure(self):
  for failed in [False,True]:
   with self.subTest(failed=failed):
    b=Bridge.__new__(Bridge);calls=[]
    info={'id':self.intent['infra_id'],'label':{'khu-intent':self.intent['intent_sha256']},'node':[{'id':'app-1','cspResourceId':'i-0123456789abcdef0','cspResourceName':'sp-name','connectionName':'aws-us-west-2','publicIP':'34.1.2.3','sshKeyId':'owned-key','nodeUserName':'cb-user'}]}
    b.info=lambda i:info;b.tb=lambda path:{'fingerprint':'SHA256:pinned','keyType':'ssh-ed25519','hostKey':'wire'} if path.endswith('/sshHostKey') else {'privateKey':'PRIVATE_FIXTURE'}
    def command(c,value):
     calls.append(value)
     self.assertNotRegex(value,r'(?i)install|mkdir|write|shutdown|curl|wget')
     if failed:raise ValueError('guest_ssh_connection_failed')
     return 'KHU_SSH_PROBE_OK\n'
    with patch.object(bridge_module,'direct_guest_command',command):result=b.run('guest_ssh_probe',self.intent)
    self.assertEqual(result['state'],'COMPLETE');self.assertEqual(len(calls),1 if failed else 2)
    self.assertEqual(result['probes'][0]['status'],'FAILED' if failed else 'PASSED')
    if not failed:self.assertEqual(len(calls[1].encode()),35548);self.assertEqual(result['probes'][1]['name'],'BOOTSTRAP_SIZE')
    self.assertNotIn('PRIVATE_FIXTURE',json.dumps(result))

 def test_preflight_reports_observed_root_space_and_keeps_the_five_gib_guard(self):
  b=Bridge.__new__(Bridge);b.tb=lambda path:{'infra':[]};b.tbdir=Path('/fixture/tumblebug')
  def command(args,**kwargs):return type('Status',(),{'returncode':0,'stdout':'{"sealed":false}' if args[:3]==['docker','exec','openbao'] else '/fixture/docker\n'})()
  for free in [4*1024**3,5*1024**3]:
   with self.subTest(free=free):
    with patch.object(bridge_module.subprocess,'run',side_effect=command),patch.object(bridge_module.shutil,'disk_usage',return_value=type('Usage',(),{'free':free})()),patch.object(bridge_module.shutil,'which',return_value='ssh'),patch.object(bridge_module.os,'memfd_create',create=True):
     result=b.run('preflight',self.intent)
    self.assertEqual(result['checks']['lab_root_free'],free>=5*1024**3)
    self.assertEqual(result['storage']['lab_root'],{'path':'/var/tmp','free_bytes':free,'required_free_bytes':5*1024**3})

 def test_low_service_storage_blocks_creation_even_when_var_tmp_has_enough_space(self):
  b=Bridge.__new__(Bridge);b.tb=lambda path:{'infra':[]};b.tbdir=Path('/fixture/tumblebug')
  def command(args,**kwargs):
   return type('Status',(),{'returncode':0,'stdout':'{"sealed":false}' if args[:3]==['docker','exec','openbao'] else '/fixture/docker\n'})()
  for service_free in [21*1024**2,5*1024**3]:
   with self.subTest(service_free=service_free):
    usage=lambda path:type('Usage',(),{'free':6*1024**3 if str(path)=='/var/tmp' else service_free})()
    with patch.object(bridge_module.subprocess,'run',side_effect=command),patch.object(bridge_module.shutil,'disk_usage',side_effect=usage),patch.object(bridge_module.shutil,'which',return_value='ssh'),patch.object(bridge_module.os,'memfd_create',create=True):
     result=b.run('preflight',self.intent)
    self.assertTrue(result['checks']['lab_root_free'])
    self.assertEqual(result['ready'],service_free>=5*1024**3)
    self.assertEqual(result['checks']['lab_service_storage_free'],service_free>=5*1024**3)
    self.assertEqual(result['storage']['lab_services']['openbao_data']['free_bytes'],service_free)

 def test_unknown_service_storage_cannot_be_treated_as_ready(self):
  b=Bridge.__new__(Bridge);b.tb=lambda path:{'infra':[]};b.tbdir=Path('/fixture/tumblebug')
  def command(args,**kwargs):
   return type('Status',(),{'returncode':0,'stdout':'{"sealed":false}' if args[:3]==['docker','exec','openbao'] else '/fixture/docker\n'})()
  def usage(path):
   if str(path)=='/fixture/docker':raise PermissionError('secret=DO_NOT_PRINT')
   return type('Usage',(),{'free':6*1024**3})()
  with patch.object(bridge_module.subprocess,'run',side_effect=command),patch.object(bridge_module.shutil,'disk_usage',side_effect=usage),patch.object(bridge_module.shutil,'which',return_value='ssh'),patch.object(bridge_module.os,'memfd_create',create=True):
   result=b.run('preflight',self.intent)
  self.assertFalse(result['ready']);self.assertFalse(result['checks']['lab_service_storage_free'])
  self.assertFalse(result['storage']['lab_services']['docker_data']['available'])
  self.assertNotIn('DO_NOT_PRINT',json.dumps(result))

if __name__=='__main__':unittest.main()
