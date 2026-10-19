import unittest
import json
from types import SimpleNamespace
import bootstrap
from bootstrap import validate_bootstrap, gpu_proof
class BootstrapTests(unittest.TestCase):
 def test_bootstrap_requires_exact_new_guest_identity(self):
  with self.assertRaises(ValueError):validate_bootstrap({'infra_id':'lab','provider_instance_id':'i-0123456789abcdef0'},'i-0123456789abcdef0')
  with self.assertRaises(ValueError):validate_bootstrap({'infra_id':'khu-poc-123','provider_instance_id':'i-0123456789abcdef0'},'i-other')
 def test_cuda_model_and_input_devices_required(self):
  self.assertTrue(gpu_proof({'cuda_available':True,'model_device':'cuda:0','input_device':'cuda:0','forward_finite':True}))
  for key in ['cuda_available','forward_finite']:
   self.assertFalse(gpu_proof({'cuda_available':True,'model_device':'cuda:0','input_device':'cuda:0','forward_finite':True, key:False}))
  self.assertFalse(gpu_proof({'cuda_available':True,'model_device':'cpu','input_device':'cuda:0','forward_finite':True}))
 def test_serial_diagnostic_exposes_only_fixed_stage_and_ssh_signals(self):
  self.assertTrue(hasattr(bootstrap,'capture_serial_diagnostic'))
  calls=[]
  def runner(args,**kwargs):
   calls.append((args,kwargs))
   if args[:2]==['systemctl','is-active']:return SimpleNamespace(returncode=0,stdout='active\n')
   if args[:3]==['sudo','-n','journalctl']:
    return SimpleNamespace(returncode=0,stdout='\n'.join([
     json.dumps({'__REALTIME_TIMESTAMP':'1791973435000000','MESSAGE':'Accepted publickey for ubuntu from 1.2.3.4 private_key=SECRET'}),
     json.dumps({'__REALTIME_TIMESTAMP':'1791973436000000','MESSAGE':'fatal: Timeout before authentication for 1.2.3.4 port 22'}),
     json.dumps({'__REALTIME_TIMESTAMP':'1791973437000000','MESSAGE':'error: kex_exchange_identification: read: Connection reset by peer'})]))
   return SimpleNamespace(returncode=0,stdout='')
  value=bootstrap.capture_serial_diagnostic('TORCH_INSTALL',runner=runner)
  self.assertTrue(value)
  line=calls[-1][1]['input']
  self.assertTrue(line.startswith('KHU_BOOTSTRAP_DIAG='))
  self.assertIn('TORCH_INSTALL',line)
  payload=json.loads(line.removeprefix('KHU_BOOTSTRAP_DIAG='))
  self.assertEqual(payload['events'],[{'at':1791973436,'kind':'PREAUTH_TIMEOUT'},{'at':1791973437,'kind':'KEX_RESET'}])
  self.assertNotIn('1.2.3.4',line)
  self.assertNotIn('SECRET',line)
  self.assertEqual(calls[-1][0],['sudo','-n','tee','/dev/ttyS0'])
  self.assertIn('--output=json',calls[-2][0])
 def test_serial_diagnostic_rejects_arbitrary_stage(self):
  self.assertTrue(hasattr(bootstrap,'capture_serial_diagnostic'))
  with self.assertRaises(ValueError):bootstrap.capture_serial_diagnostic('secret=value')
 def test_post_ready_stage_is_allowlisted(self):
  self.assertIn('POST_READY',bootstrap.DIAG_STAGES)
if __name__=='__main__':unittest.main()
