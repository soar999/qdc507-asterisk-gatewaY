#!/usr/bin/python3.8
"""QDC507 cold boot gate: trusted prep -> host audio -> qtest; manual USB recovery unchanged."""
import fcntl,hashlib,os,pathlib,subprocess,sys,time,json
P=pathlib.Path
BOOT=P('/usr/local/sbin/qdc507-boot-prep.py')
AUDIO=P('/usr/local/sbin/qdc507-host-audio-repair.py')
BOOT_ADB=P('/usr/local/sbin/qdc507-boot-adb.py')
QTEST=P('/usr/local/sbin/qdc507-coldstart-start-qtest.py')
ADB=P('/usr/local/libexec/qdc507/adb')
LOCK=P('/run/qdc507-coldstart.lock');LOG=P('/var/lib/qdc507-coldstart-last.txt')
PINS={AUDIO:'f12b7c642e10bb5e8f2f88109a8cbae13a07127444e688a653b577e06677f906',QTEST:'41083e9d25e60c04485b280fcf52f9df6660da3286a3cdad7a8b9977a64adf45',ADB:'a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01',BOOT_ADB:'258bb517af7d5de432af9e8424f68e5403c32dfa0394cc98c87abb19eda245de'}
BOOT_PIN='da7add8f2a34f718a6f0ea73374c45508a0980ba19007259a2289c9356d16bd5'
class Halt(Exception):pass
def need(ok,why):
 if not ok:raise Halt(why)
def log(s):
 with LOG.open('a') as f:f.write(s+'\n')
def trusted(p,h):
 for x in [*reversed(p.parents),p]:
  s=x.lstat();need(s.st_uid==0 and not s.st_mode&0o022 and not x.is_symlink(),'UNTRUSTED:'+str(x))
 need(hashlib.sha256(p.read_bytes()).hexdigest()==h,'HASH_CHANGED:'+str(p))
def run(p,timeout):
 c=subprocess.run(['/usr/bin/python3.8',str(p)],env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root'},capture_output=True,stdin=subprocess.DEVNULL,timeout=timeout)
 need(c.returncode==0,'STAGE_FAILED:'+p.name)
def identity():
 ds=[]
 for d in P('/sys/bus/usb/devices').iterdir():
  try:
   if (d/'idVendor').read_text().strip()=='2ca3' and (d/'idProduct').read_text().strip()=='4006':ds.append(d)
  except OSError:pass
 need(len(ds)==1,'USB_NOT_UNIQUE');d=ds[0]
 return(d.name,(d/'busnum').read_text().strip(),(d/'devnum').read_text().strip(),(d/'serial').read_text().strip())
def main():
 need(os.geteuid()==0,'ROOT_REQUIRED')
 need(subprocess.run(['/bin/uname','-r'],capture_output=True,text=True).stdout.strip()=='4.4.180+','KERNEL_CHANGED')
 trusted(BOOT,BOOT_PIN)
 for f,h in PINS.items():trusted(f,h)
 fd=os.open(str(LOCK),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'r+') as lock:
  s=os.fstat(lock.fileno());need(s.st_uid==0 and not s.st_mode&0o077,'LOCK_UNTRUSTED')
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  run(BOOT,95);g=identity();log('STAGE=IF2_READY')
  run(AUDIO,45);need(identity()==g,'USB_CHANGED_AFTER_AUDIO');log('STAGE=HOST_AUDIO_OK')
  run(BOOT_ADB,35);need(identity()==g,'USB_CHANGED_AFTER_ADB');log('STAGE=ADB_LISTENER_READY')
  # Docker may become ready after DSM starts this boot task. Individual failures retry;
  # the overall monotonic deadline bounds the boot wait, including slow CLI attempts.
  docker='/volume2/@appstore/Docker/usr/bin/docker'
  deadline=time.monotonic()+900
  attempts=0
  while time.monotonic()<deadline:
   need(identity()==g,'USB_CHANGED_WHILE_WAITING_DOCKER')
   attempts+=1
   try:
    r=subprocess.run([docker,'info'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root'})
    if r.returncode==0:
     log('DOCKER_READY_ATTEMPTS='+str(attempts))
     break
   except subprocess.TimeoutExpired:
    pass
   time.sleep(2)
  else:raise Halt('DOCKER_NOT_READY_900S')
  need(identity()==g,'USB_CHANGED_WHILE_WAITING_DOCKER')
  run(QTEST,130);need(identity()==g,'USB_CHANGED_AFTER_QTEST');log('STAGE=QTEST_RUNNING_IF2_MATCH')
  log('COLDSTART_STATIC_OK=1')
if __name__=='__main__':
 LOG.write_text('TIME='+time.strftime('%F %T %z')+'\n')
 try:main();rc=0
 except Exception as e:log('DEFER='+str(e));rc=2
 log('EXIT='+str(rc));LOG.chmod(0o644);print('QDC507_COLDSTART_EXIT='+str(rc));sys.exit(rc)
