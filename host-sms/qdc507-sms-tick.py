#!/usr/bin/python3.8
"""Run pinned NAS SMS queue consumer once, serialized, with sanitized status log."""
import fcntl,hashlib,os,pathlib,subprocess,time,sys
P=pathlib.Path
CONSUMER=P('/usr/local/sbin/qdc507-sms-queue-deliver-pinned.py')
DOCKER=P('/usr/local/bin/docker')
BASE=P('/volume2/docker/asterisk/qdc507-current/v2/sms-queue')
LOCK=P('/run/qdc507-sms-queue-tick.lock')
REPORT=P('/var/lib/qdc507-sms-queue-tick-last.txt')
PIN='f03d5fc3dfef44bd88a2c77aaa1d609d087db607009394cc1641c961781fc81a'
DOCKER_PIN='273ad259f3dc771edcb185e0fc7d5aefd307615f8e2b256d0b5774482475253c'
def count():return len(list((BASE/'pending').glob('*.body')))
def report(*rows):
 REPORT.write_text('TIME='+time.strftime('%F %T %z')+'\n'+'\n'.join(rows)+'\n');REPORT.chmod(0o644)
def running_old_loop():
 for p in P('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   a=(p/'cmdline').read_bytes().split(b'\0')
   if any(x==b'/volume2/docker/asterisk/sms-queue-loop.sh' for x in a):return True
  except (OSError,PermissionError):continue
 return False
def main():
 if os.geteuid()!=0:raise RuntimeError('ROOT_REQUIRED')
 if hashlib.sha256(CONSUMER.read_bytes()).hexdigest()!=PIN:raise RuntimeError('CONSUMER_HASH_CHANGED')
 if hashlib.sha256(DOCKER.read_bytes()).hexdigest()!=DOCKER_PIN:raise RuntimeError('DOCKER_HASH_CHANGED')
 fd=os.open(str(LOCK),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'r+') as f:
  s=os.fstat(f.fileno())
  if s.st_uid!=0 or s.st_mode&0o077:raise RuntimeError('LOCK_UNTRUSTED')
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  if running_old_loop():raise RuntimeError('LEGACY_QUEUE_LOOP_RUNNING')
  before=count()
  if before==0:report('PENDING_BEFORE=0','PENDING_AFTER=0','STATUS=EMPTY');return
  c=subprocess.run(['/usr/bin/python3.8',str(CONSUMER)],env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root'},stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  after=count()
  report('PENDING_BEFORE='+str(before),'PENDING_AFTER='+str(after),'CONSUMER_EXIT='+str(c.returncode),'STATUS='+('ATTEMPTED_NOT_PHONE_CONFIRMED' if c.returncode==0 else 'ERROR'))
  if c.returncode:raise RuntimeError('CONSUMER_NONZERO')
if __name__=='__main__':
 try:main()
 except Exception as e:
  report('STATUS=DEFER','REASON='+str(e)[:120]);sys.exit(2)
