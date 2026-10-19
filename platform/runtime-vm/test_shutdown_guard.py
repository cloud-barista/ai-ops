import unittest
from shutdown_guard import guard_delay
class GuardTests(unittest.TestCase):
 def test_expired_shutdown_is_immediate(self):self.assertEqual(guard_delay(10,20),0)
 def test_lifetime_cannot_be_extended(self):
  self.assertEqual(guard_delay(100,50),50)
  with self.assertRaises(ValueError):guard_delay(10000,0)
if __name__=='__main__':unittest.main()
