import importlib.util
import json
import struct
import unittest
from pathlib import Path


class ObserverTests(unittest.TestCase):
 def load(self):
  path=Path(__file__).with_name('ssh_observer.py')
  self.assertTrue(path.exists(),'earliest guest observer must exist')
  spec=importlib.util.spec_from_file_location('ssh_observer',path)
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  return module
 def test_packet_metadata_identifies_direction_without_payload_or_addresses(self):
  observer=self.load()
  ethernet=b'\0'*12+b'\x08\x00'
  ip=struct.pack('!BBHHHBBH4s4s',0x45,0,43,1,0,61,6,0,b'\x01\x02\x03\x04',b'\x05\x06\x07\x08')
  tcp=struct.pack('!HHIIBBHHH',54321,22,123,456,0x50,0x18,0,0,0)
  record=observer.packet_metadata(ethernet+ip+tcp+b'KEY',b'salt',1234)
  self.assertEqual(record['direction'],'IN');self.assertEqual(record['kind'],'DATA');self.assertEqual(record['payload_bytes'],3)
  self.assertNotIn('KEY',json.dumps(record));self.assertNotIn('1.2.3.4',json.dumps(record));self.assertNotIn('54321',json.dumps(record))
  self.assertEqual(len(record['connection']),12)
 def test_journal_preserves_safe_cause_and_time_only(self):
  observer=self.load()
  raw=json.dumps({'MESSAGE':'fatal: Timeout before authentication for 1.2.3.4 private_key=SECRET','__REALTIME_TIMESTAMP':'1791973436000000'})
  result=observer.journal_events(raw)
  self.assertEqual(result,[{'at':1791973436,'kind':'PREAUTH_TIMEOUT'}])
  self.assertNotIn('SECRET',json.dumps(result))


if __name__=='__main__':unittest.main()
