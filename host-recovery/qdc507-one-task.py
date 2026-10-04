#!/usr/bin/python3.8
"""One explicit, bounded QDC507 manual recovery run. Operator confirms no calls."""
import fcntl, hashlib, json, os, pathlib, stat, subprocess, sys, time
P=pathlib.Path
REC=P('/usr/local/sbin/qdc507-manual-recover.py')
REF=P('/usr/local/sbin/qdc507-adb-refresh.py')
AUDIO=P('/usr/local/sbin/qdc507-host-audio-repair.py')
DOCKER=P('/volume2/@appstore/Docker/usr/bin/docker')
CONTAINER='qdc507-hardware'
LOG=P('/var/lib/qdc507-manual-recover-last.txt')
LOCK=P('/run/qdc507-manual-task.lock')
USB=P('/sys/bus/usb/devices')
GOOD={'recover':'a4d7f674429f32ca4a3195f7d1b5dca7c7e10e565d13d8825ae12139a96aefc6',
      'refresh':'85a37f726ba7b51a3f31363eccb990fa494213d13fa1dea9bfb39506a8e1511b',
      'audio':'f12b7c642e10bb5e8f2f88109a8cbae13a07127444e688a653b577e06677f906',
      'docker':'273ad259f3dc771edcb185e0fc7d5aefd307615f8e2b256d0b5774482475253c'}
class Stop(Exception): pass
def need(ok,why):
 if not ok: raise Stop(why)
def append(line):
 with LOG.open('a') as f: f.write(str(line)+'\n')
def verify(p,want):
 for q in [*reversed(p.parents),p]:
  s=q.lstat(); need(s.st_uid==0 and not s.st_mode&0o022 and not stat.S_ISLNK(s.st_mode),'UNTRUSTED_FILE:'+str(q))
 s=p.stat(); need(stat.S_ISREG(s.st_mode) and bool(s.st_mode&stat.S_IXUSR),'NOT_EXECUTABLE:'+str(p))
 need(hashlib.sha256(p.read_bytes()).hexdigest()==want,'HASH_MISMATCH:'+str(p))
def gen():
 out=[]
 for d in USB.iterdir():
  try:
   if (d/'idVendor').read_text().strip().lower()=='2ca3' and (d/'idProduct').read_text().strip().lower()=='4006':
    out.append((d.name,(d/'busnum').read_text().strip(),(d/'devnum').read_text().strip(),(d/'serial').read_text().strip()))
  except (OSError,ValueError): pass
 need(len(out)==1,'USB_QDC507_NOT_UNIQUE'); return out[0]
def cmd(args,timeout):
 try: c=subprocess.run([str(x) for x in args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout,
                       env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root'})
 except (OSError,subprocess.TimeoutExpired): raise Stop('COMMAND_FAILED_OR_TIMEOUT')
 return c
def container_running():
 c=cmd([DOCKER,'inspect','--format','{{.State.Running}} {{.State.Paused}}',CONTAINER],15)
 return c.returncode==0 and c.stdout.strip()=='true false'

def recovery():
 # Let the recovery program finish its own finally block (which restarts qtest).
 c=cmd(['/usr/bin/python3.8',REC,'--recover','--confirm','RECOVER-QDC507'],None)
 if c.returncode:
  msg=c.stderr.strip()
  # Only this known missing-ADB result permits exactly one trusted server refresh.
  if c.returncode==2 and msg=='DEFER: ADB must show exactly one online device': return 'ADB_OFFLINE'
  raise Stop('RECOVERY_DEFER:'+msg[:300])
 try: obj=json.loads(c.stdout.strip())
 except ValueError: raise Stop('RECOVERY_JSON_INVALID')
 need(isinstance(obj,dict),'RECOVERY_JSON_NOT_OBJECT')
 action=obj.get('action'); need(action in ('IF2_ALIAS_REPAIRED','QTEST_RESTARTED','IF5_UNBOUND','VERIFIED'),'UNKNOWN_RECOVERY_ACTION')
 return action
def refresh():
 # The helper has its own bounded 20-attempt loop; do not kill it mid-daemon start.
 c=cmd(['/usr/bin/python3.8',REF],None)
 try: obj=json.loads(c.stdout.strip())
 except ValueError: raise Stop('ADB_REFRESH_INVALID:'+c.stderr[:150])
 need(isinstance(obj,dict),'ADB_REFRESH_JSON_NOT_OBJECT')
 need(c.returncode==0 and obj.get('status')=='ADB_REFRESH_OK','ADB_REFRESH_DEFER:'+str(obj.get('reason','unknown'))[:200])
def main():
 need(os.geteuid()==0,'ROOT_REQUIRED')
 verify(REC,GOOD['recover']); verify(REF,GOOD['refresh']); verify(AUDIO,GOOD['audio']); verify(DOCKER,GOOD['docker'])
 need(LOCK.parent.stat().st_uid==0 and not LOCK.parent.stat().st_mode&0o022,'LOCK_DIR_UNTRUSTED')
 fd=os.open(str(LOCK),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'r+') as f:
  lock_stat=os.fstat(f.fileno())
  need(lock_stat.st_uid==0 and not lock_stat.st_mode&0o077,'LOCK_UNTRUSTED')
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  LOG.write_text('TIME='+time.strftime('%F %T %z')+'\nMANUAL_NO_CALLS_ASSUMED=1\n')
  need(container_running(),'CONTAINER_NOT_RUNNING_AT_START')
  append('CONTAINER_RUNNING_AT_START=1')
  append('WAIT_USB_SETTLE=40'); time.sleep(40)
  before=gen(); append('USB_GENERATION='+before[0]+':'+before[2])
  audio=cmd(['/usr/bin/python3.8',AUDIO],60)
  need(audio.returncode==0,'HOST_AUDIO_REPAIR_FAILED')
  need(gen()==before,'USB_GENERATION_CHANGED_AFTER_AUDIO')
  append('HOST_AUDIO_READY=1')
  need(container_running(),'CONTAINER_STOPPED_DURING_AUDIO_PREP')
  done=set(); actions=0
  # Max 4 changes: alias, qtest, IF5, ADB; voice deployment occurs inside final recovery.
  for iteration in range(6):
   need(gen()==before,'USB_GENERATION_CHANGED')
   result=recovery(); append('STEP='+result)
   need(gen()==before,'USB_GENERATION_CHANGED_AFTER_STEP')
   if result=='VERIFIED':
    append('STATIC_RECOVERY_OK=1'); return 0
   if result=='ADB_OFFLINE':
    need('ADB_REFRESH' not in done,'ADB_STILL_OFFLINE_AFTER_REFRESH')
    # Refresh is limited to IF5 genuinely released; never mask an unknown driver.
    iface=USB/(before[0]+':1.5'); need(iface.exists(),'IF5_MISSING_FOR_ADB_REFRESH')
    link=iface/'driver'; need(not link.is_symlink() or link.resolve(strict=True).name=='usbfs','IF5_NOT_AVAILABLE_FOR_ADB_REFRESH')
    refresh(); append('STEP=ADB_REFRESHED'); done.add('ADB_REFRESH')
   else:
    need(result not in done,'RECOVERY_STEP_REPEATED:'+result)
    done.add(result)
   actions+=1; need(actions<=4,'RECOVERY_STEP_LIMIT')
  raise Stop('RECOVERY_ITERATION_LIMIT')
if __name__=='__main__':
 try:
  rc=main()
 except BlockingIOError:
  print('DEFER: another manual recovery is running',file=sys.stderr)
  sys.exit(3)
 except (Stop,OSError,ValueError) as exc:
  append('DEFER='+str(exc)[:400]); rc=2
 append('EXIT='+str(rc)); LOG.chmod(0o644)
 print('QDC507_MANUAL_EXIT='+str(rc)+' LOG='+str(LOG)); sys.exit(rc)
