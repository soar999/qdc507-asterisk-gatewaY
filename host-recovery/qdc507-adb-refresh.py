#!/usr/bin/python3.8
"""One-shot ADB server refresh after IF5 option unbind. No USB writes or Docker actions."""
import fcntl, hashlib, json, os, pathlib, stat, subprocess, sys, time
P=pathlib.Path
ADB=P('/usr/local/libexec/qdc507/adb')
USBROOT=P('/sys/bus/usb/devices')
LOG=P('/var/lib/qdc507-manual-adb-last.txt')
LOCK=P('/run/qdc507-manual-recovery.lock')
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/tmp/qdc507-adb-home','ADB_SERVER_SOCKET':'tcp:127.0.0.1:5037'}
class Defer(Exception): pass
def need(ok,msg):
 if not ok: raise Defer(msg)
def run(args,timeout=12,env=ENV):
 try:
  c=subprocess.run(list(map(str,args)),env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
 except (OSError,subprocess.TimeoutExpired): raise Defer('ADB command failed or timed out')
 need(c.returncode==0,'ADB command exited nonzero'); return c.stdout.strip()
def snap():
 devices=[]
 for d in USBROOT.iterdir():
  try:
   if (d/'idVendor').read_text().strip()=='2ca3' and (d/'idProduct').read_text().strip()=='4006': devices.append(d)
  except OSError: pass
 need(len(devices)==1,'QDC507 USB not unique');usb=devices[0]
 iface=P(str(usb)+':1.5')
 need((iface/'bInterfaceNumber').read_text().strip()=='05' and (iface/'bInterfaceClass').read_text().strip()=='ff' and (iface/'bInterfaceSubClass').read_text().strip()=='42' and (iface/'bInterfaceProtocol').read_text().strip()=='01','IF5 descriptor changed')
 drv=iface/'driver'
 need(not drv.is_symlink() or drv.resolve(strict=True).name=='usbfs','IF5 is occupied by a non-ADB driver')
 a=P('/dev/qdc507-at'); need(a.is_symlink() and a.resolve(strict=True).name.startswith('ttyUSB'),'IF2 alias unavailable')
 return (usb.name,(usb/'busnum').read_text().strip(),(usb/'devnum').read_text().strip(),os.stat(a).st_rdev,(usb/'serial').read_text().strip())
def server_pids():
 found=[]
 for p in P('/proc').iterdir():
  if not p.name.isdigit(): continue
  try:
   args=(p/'cmdline').read_bytes().split(b'\0')
   if len(args)<6 or not args[0].endswith(b'/adb') or args[1:6]!=[b'-a',b'-P',b'5037',b'nodaemon',b'server']: continue
   st=p.stat(); need(st.st_uid==0,'ADB server not root-owned')
   exe=p/'exe'; need(hashlib.sha256(exe.read_bytes()).hexdigest()=='a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01','ADB server executable changed')
   found.append(int(p.name))
  except (ProcessLookupError,FileNotFoundError): continue
 need(len(found)<=1,'more than one ADB server'); return found

def main():
 need(os.geteuid()==0,'root required')
 st=ADB.stat(); need(st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o700,'ADB install mode changed')
 need(hashlib.sha256(ADB.read_bytes()).hexdigest()=='a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01','ADB binary changed')
 with LOCK.open('a+') as f:
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  before=snap(); time.sleep(5); need(snap()==before,'USB generation changed during check')
  existing=server_pids()
  need(len(existing)==1,'existing ADB daemon not uniquely identified; manual review required')
  run([ADB,'kill-server'],10)
  need(snap()==before,'USB generation changed after ADB stop')
  for _ in range(10):
   if not server_pids(): break
   time.sleep(0.5); need(snap()==before,'USB changed while stopping old ADB daemon')
  need(not server_pids(),'old ADB daemon survived kill-server')
  service_env=dict(ENV); service_env.pop('ADB_SERVER_SOCKET')
  with open('/var/lib/qdc507-manual-adb-server.log','ab') as log:
   subprocess.Popen([str(ADB),'-a','-P','5037','nodaemon','server'],env=service_env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
  for _ in range(20):
   time.sleep(1); need(snap()==before,'USB generation changed during ADB restart')
   try:
    rows=run([ADB,'devices','-l'],6).splitlines()
    online=[line.split()[0] for line in rows[1:] if len(line.split())>1 and line.split()[1]=='device']
    if len(online)==1 and online[0].lower()==before[4].lower():
     need(run([ADB,'-s',online[0],'shell','id','-u'],8).replace('\r','').strip()=='0','ADB shell not root')
     need(snap()==before,'USB changed after ADB check')
     return {'status':'ADB_REFRESH_OK','usb_devnum':before[2],'next':'RUN_MANUAL_RECOVERY_AGAIN'}
   except Defer: pass
  raise Defer('ADB did not expose the matching online QDC507 within 20 seconds')
if __name__=='__main__':
 try: result=main(); rc=0
 except (Defer,OSError,ValueError,BlockingIOError) as e: result={'status':'DEFER','reason':str(e)};rc=2
 LOG.write_text(json.dumps(result,sort_keys=True)+'\nEXIT='+str(rc)+'\n'); LOG.chmod(0o644)
 print(json.dumps(result,sort_keys=True));sys.exit(rc)
