"""Guest-only deadline helper. Never installed or executed on the lab host."""
import time, subprocess
def guard_delay(deadline,now):
 delay=max(0,float(deadline)-float(now))
 if delay>2700:raise ValueError('invalid_guest_deadline')
 return delay
def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--deadline',type=float,required=True);a=p.parse_args()
 time.sleep(guard_delay(a.deadline,time.time()))
 subprocess.run(['sudo','-n','shutdown','-h','now'],check=True,timeout=15)
if __name__=='__main__':main()
