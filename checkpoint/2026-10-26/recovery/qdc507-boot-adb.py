#!/usr/bin/python3.8
"""Root-owned QDC507 ADB server gate for boot, preserving existing qtest subnet."""
import hashlib,os,pathlib,subprocess,sys,time
P=pathlib.Path;ADB=P('/usr/local/libexec/qdc507/adb');LOG=P('/var/lib/qdc507-boot-adb-last.txt')
PIN='a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01'
class Halt(Exception):pass
def need(ok,msg):
 if not ok:raise Halt(msg)
def run(args,timeout=15):
 c=subprocess.run(args,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','ADB_SERVER_SOCKET':'tcp:127.0.0.1:5037'},capture_output=True,stdin=subprocess.DEVNULL,timeout=timeout)
 need(c.returncode==0,'CMD_FAILED:'+str(args[0]));return c.stdout.decode(errors='replace')
def probe():
 c=subprocess.run(['/bin/netstat','-lnt'],capture_output=True,text=True,timeout=10)
 return c.returncode==0 and any(':5037 ' in line for line in c.stdout.splitlines())
def main():
 need(os.geteuid()==0,'ROOT_REQUIRED')
 need(ADB.stat().st_uid==0 and hashlib.sha256(ADB.read_bytes()).hexdigest()==PIN,'ADB_UNTRUSTED')
 # Firewall lifecycle is outside this boot workflow; no iptables writes.
 print('FIREWALL_UNCHANGED=1')
 if not probe():
  P('/tmp/qdc507-adb-home').mkdir(mode=0o700,parents=True,exist_ok=True)
  with P('/var/lib/qdc507-boot-adb-server.log').open('ab') as f:
   env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/tmp/qdc507-adb-home'}
   subprocess.Popen([str(ADB),'-a','-P','5037','nodaemon','server'],stdin=subprocess.DEVNULL,stdout=f,stderr=f,env=env,start_new_session=True)
  for _ in range(15):
   if probe():break
   time.sleep(1)
 need(probe(),'ADB_PORT_NOT_READY')
 print('ADB_LISTENER_READY=1')
if __name__=='__main__':
 with LOG.open('w') as f:
  old=sys.stdout;sys.stdout=f
  try:print('TIME='+time.strftime('%F %T %z'));main();print('EXIT=0');rc=0
  except Exception as e:print('DEFER='+str(e));print('EXIT=2');rc=2
  finally:sys.stdout=old
 LOG.chmod(0o644);print('BOOT_ADB_EXIT='+str(rc));sys.exit(rc)
