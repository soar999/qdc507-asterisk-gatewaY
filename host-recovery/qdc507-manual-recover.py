#!/usr/bin/python3.8
"""QDC507 manual recovery, DSM root only. Operator keeps all QDC calls away."""
import argparse, fcntl, hashlib, json, os, pathlib, re, stat, subprocess, sys, time
P=pathlib.Path
DOCKER=P('/volume2/@appstore/Docker/usr/bin/docker')
ADB=P('/usr/local/libexec/qdc507/adb')
RUNTIME=P('/usr/local/libexec/qdc507/runtime')
LOCK=P('/run/qdc507-manual-recovery.lock')
USBROOT=P('/sys/bus/usb/devices')
IMAGE='sha256:f18b0d39ab4f0849aaf92ea4327d150881eb7dc89790d599a806ed39ab9a923b'
HASHES={
 str(DOCKER):'273ad259f3dc771edcb185e0fc7d5aefd307615f8e2b256d0b5774482475253c',
 '/usr/bin/python3.8':'8e673264e8dcac0b473a50baeff923c90e539c6317d9877193f00e1ae8677444',
 str(ADB):'a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01',
 str(RUNTIME/'qdc507_aprv3.ko'):'3d82d3dec4f1e323201bba87156df9d41438e08314097353f2607f9117211d4a',
 str(RUNTIME/'qdc507_voice.ko'):'ed3821682d5309969a01c764192c83feff9669c61ef237c69475cd1619cf296c',
 str(RUNTIME/'mavo-pcm-bridge.armv7'):'88d47c15e61d1428a59c821fed804c2e6490e82859a085062f21966b58d167fc'}
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','ADB_SERVER_SOCKET':'tcp:127.0.0.1:5037'}
class Defer(Exception): pass
def must(test,msg):
 if not test: raise Defer(msg)
def trusted(p):
 p=P(p); must(p.is_absolute(),'untrusted relative path')
 for x in [*reversed(p.parents),p]:
  st=x.lstat(); must(st.st_uid==0 and not st.st_mode&0o022 and not stat.S_ISLNK(st.st_mode),'untrusted owner/mode/link: '+str(x))
 must(p.is_file(),'untrusted file type: '+str(p))
 return p
def verify_install():
 must(os.geteuid()==0,'DSM root required')
 trusted('/usr/bin/python3.8'); trusted('/volume2/@appstore/Docker/usr/bin/docker'); trusted(__file__)
 for path,want in HASHES.items():
  trusted(path)
  must(hashlib.sha256(P(path).read_bytes()).hexdigest()==want,'dependency hash changed: '+path)
 for x in ('/usr/local/libexec/qdc507','/usr/local/libexec/qdc507/runtime'):
  st=P(x).lstat(); must(st.st_uid==0 and not st.st_mode&0o022 and stat.S_ISDIR(st.st_mode),'runtime directory not trusted')
def run(argv,timeout=15,ok=True):
 try: c=subprocess.run([str(a) for a in argv],env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
 except (OSError,subprocess.TimeoutExpired) as e: raise Defer('command timed out/failed: '+str(argv[0])) from e
 if ok: must(c.returncode==0,'command failed '+str(argv[0])+' exit='+str(c.returncode))
 return c.stdout.strip()
def docker(*args,timeout=15): return run([DOCKER,*args],timeout)
def adb(*args,timeout=12): return run([ADB,*args],timeout)
def remote(serial,script,timeout=16):
 # DSM's ADB reports exit 0 for remote shell failure; require a fresh final marker.
 import secrets
 nonce='QDC507_'+secrets.token_hex(12)+'_'
 out=adb('-s',serial,'shell','('+script+') && echo '+nonce+'OK',timeout=timeout)
 rows=[line.strip() for line in out.replace('\r','').splitlines()]
 must(rows and rows[-1]==nonce+'OK' and rows.count(nonce+'OK')==1,'modem shell operation failed')
 return '\n'.join(rows[:-1]).strip()
def inspected(name):
 data=json.loads(docker('inspect',name))
 must(len(data)==1 and data[0]['State']['Running'] and not data[0]['State']['Paused'],
      'container not running: '+name)
 if name=='qdc507-hardware':
  must(data[0]['Image']==IMAGE,'unexpected qdc507 image')
  must(not data[0]['HostConfig']['Privileged'],'unexpected privileged qdc507 container')
 return data[0]
def containers_ready():
 inspected('qdc507-hardware')
def driver(p):
 q=p/'driver'; return q.resolve(strict=True).name if q.is_symlink() else 'unbound'
def txt(p): return p.read_text().strip().lower()
def snapshot():
 devices=[]
 for p in USBROOT.iterdir():
  try:
   if txt(p/'idVendor')=='2ca3' and txt(p/'idProduct')=='4006': devices.append(p)
  except (OSError,ValueError): pass
 must(len(devices)==1,'QDC507 must be unique and present')
 usb=devices[0]; i2=P(str(usb)+':1.2'); i5=P(str(usb)+':1.5')
 must(i2.exists() and i5.exists(),'IF2/IF5 absent')
 def desc(i): return tuple(txt(i/k) for k in ('bInterfaceNumber','bInterfaceClass','bInterfaceSubClass','bInterfaceProtocol'))
 must(desc(i2)==('02','ff','00','00') and desc(i5)==('05','ff','42','01'),'IF descriptor changed')
 ep=[]
 for p in i5.glob('ep_*'):
  if p.is_dir() and txt(p/'bmAttributes')=='02': ep.append(bool(int(txt(p/'bEndpointAddress'),16)&128))
 must(True in ep and False in ep,'IF5 bulk endpoints unavailable')
 ttys=[]
 for p in P('/sys/class/tty').glob('ttyUSB*'):
  try:
   if (p/'device').resolve(strict=True).parent==i2.resolve(strict=True): ttys.append(p.name)
  except OSError: pass
 must(len(ttys)==1 and driver(i2)=='option','IF2 tty/driver unexpected')
 node=P('/dev')/ttys[0]; st=node.stat()
 must(stat.S_ISCHR(st.st_mode) and st.st_gid==1000 and stat.S_IMODE(st.st_mode)==0o660,'IF2 node permissions')
 must(driver(i5) in ('usbfs','option','unbound'),'IF5 driver unexpected')
 return {'usb':usb.name,'bus':txt(usb/'busnum'),'dev':txt(usb/'devnum'),'i2':i2.name,'i5':i5.name,'node':str(node),'rdev':st.st_rdev,'driver':driver(i5)}
def stable(s,secs=35):
 for n in range(secs):
  must(snapshot()==s,'USB changed during stable window'); containers_ready()
  if n!=secs-1: time.sleep(1)
def mapping(s):
 d=inspected('qdc507-hardware')
 mounts=[x for x in (d['HostConfig'].get('Devices') or [])
         if x.get('PathInContainer')=='/dev/ttyUSB0']
 must(len(mounts)==1,'qdc507 device binding missing')
 src=mounts[0]['PathOnHost']
 must(src=='/dev/qdc507-at','qdc507 does not use stable IF2 alias: '+src)

 def same_device(host,guest):
  try:
   st=P(host).stat()
   if not stat.S_ISCHR(st.st_mode):
    return False
   val=docker('exec','qdc507-hardware','stat','-c','%t:%T',guest)
   if re.fullmatch('[0-9a-f]+:[0-9a-f]+',val) is None:
    return False
   got=tuple(int(v,16) for v in val.split(':'))
   want=(os.major(st.st_rdev),os.minor(st.st_rdev))
   return got==want
  except (OSError,Defer):
   return False

 if not same_device('/dev/qdc507-at','/dev/ttyUSB0'):
  return False

 for n in ('controlC0','pcmC0D0p','pcmC0D0c','timer'):
  path='/dev/snd/'+n
  if not same_device(path,path):
   return False

 return True
def alias_ok(s):
 a=P('/dev/qdc507-at')
 try: return a.is_symlink() and a.resolve(strict=True)==P(s['node']) and a.stat().st_rdev==s['rdev']
 except (OSError,RuntimeError): return False

def host_devices_ready(s):
 must(alias_ok(s),'IF2 alias not ready before container restart')

 usb=(USBROOT/s['usb']).resolve(strict=True)

 for n in (6,7,8):
  i=USBROOT/(s['usb']+':1.'+str(n))
  must(i.is_dir() and driver(i)=='snd-usb-audio',
       'host audio interface not ready: IF'+str(n))

 nodes=('controlC0','pcmC0D0p','pcmC0D0c','timer')
 for n in nodes:
  q=P('/dev/snd')/n
  st=q.stat()
  must(stat.S_ISCHR(st.st_mode),'host sound node missing: '+n)
  must(st.st_gid==1000 and stat.S_IMODE(st.st_mode)==0o660,
       'host sound node permissions wrong: '+n)

 ctl=P('/sys/class/sound/controlC0/device').resolve(strict=True)
 must(usb in ctl.parents or ctl==usb,
      'controlC0 belongs to different USB device')

def check():
 containers_ready(); s=snapshot(); bound=mapping(s)
 return {'device':s['usb'],'if2':s['node'],'if5_driver':s['driver'],'alias_correct':alias_ok(s),'qtest_mapping_correct':bound,'status':'CHECK_COMPLETE'}

def docker_guard_alias(s):
 must(snapshot()==s,'USB changed'); containers_ready()
 mapping(s)
 must(snapshot()==s,'USB changed after container check')
def docker_guard(s,need_bound=True):
 must(snapshot()==s,'USB changed'); containers_ready()
 must(alias_ok(s),'IF2 alias requires maintenance'); bound=mapping(s)
 if need_bound: must(bound,'qtest IF2 mapping requires restart')
 must(snapshot()==s,'USB changed after container check')
def online_serial(s):
 lines=adb('devices','-l').splitlines(); dev=[l.split()[0] for l in lines[1:] if len(l.split())>1 and l.split()[1]=='device']
 must(len(dev)==1,'ADB must show exactly one online device'); serial=dev[0]
 usb=P(str(USBROOT/s['usb']))
 must((usb/'serial').is_file() and txt(usb/'serial')==serial.lower(),'ADB serial does not match QDC507 USB device')
 must(remote(serial,'id -u').replace('\r','').strip()=='0','ADB root unavailable')
 return serial
def voice_ready(serial):
 checks=(
  "grep -q '^qdc507_aprv3 ' /proc/modules",
  "grep -q '^qdc507_voice ' /proc/modules",
  'test -r /proc/asound/cards && ! grep -q "no soundcards" /proc/asound/cards',
  'test -x /tmp/djonehub-call/mavo-pcm-bridge.armv7',
  'for n in controlC0 pcmC0D4p pcmC0D4c pcmC0D5p pcmC0D6c; do test -c /dev/snd/$n || exit 1; done',
  'grep -q "ACDB -> Sent VocProc Cal!" /run/djonehub-alsaucm.log',
  'test "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0',
  '/tmp/djonehub-call/mavo-pcm-bridge.armv7 --check')
 try:
  for check in checks: remote(serial,check,timeout=16)
  return True
 except Defer: return False
def deploy_voice(s):
 # Audited pinned files only. Stop qtest during remote sound/UAC calibration.
 must(snapshot()==s,'USB generation changed before voice deployment'); containers_ready()
 serial=online_serial(s)
 must(remote(serial,'uname -r').strip()=='3.18.44','unexpected modem kernel')
 if voice_ready(serial): return 'already-ready'
 # This point is disruptive. Stop and start qtest even if deployment aborts.
 stop_attempted=True
 try:
  docker('stop','-t','25','qdc507-hardware',timeout=45)
  must(not json.loads(docker('inspect','qdc507-hardware'))[0]['State']['Running'],'qtest did not stop')
  def sh(cmd,timeout=14): return remote(serial,cmd,timeout=timeout)
  sh('mkdir -p /tmp/djonehub-call && chmod 700 /tmp/djonehub-call')
  for name in ('qdc507_aprv3.ko','qdc507_voice.ko','mavo-pcm-bridge.armv7'):
   run([ADB,'-s',serial,'push',RUNTIME/name,'/tmp/djonehub-call/'+name],timeout=30)
   must(snapshot()==s,'USB changed while deploying voice runtime')
   sh('test -s /tmp/djonehub-call/'+name)
   digest=sh('sha256sum /tmp/djonehub-call/'+name).split()
   must(len(digest)>=1 and digest[0]==HASHES[str(RUNTIME/name)],'modem runtime file hash mismatch: '+name)
  sh('chmod 644 /tmp/djonehub-call/*.ko && chmod 755 /tmp/djonehub-call/mavo-pcm-bridge.armv7 && test -x /tmp/djonehub-call/mavo-pcm-bridge.armv7')
  sh("! grep -q '^qdc507_afe ' /proc/modules")
  sh("grep -q '^qdc507_aprv3 ' /proc/modules || insmod /tmp/djonehub-call/qdc507_aprv3.ko; grep -q '^qdc507_aprv3 ' /proc/modules")
  sh("grep -q '^qdc507_voice ' /proc/modules || insmod /tmp/djonehub-call/qdc507_voice.ko; grep -q '^qdc507_voice ' /proc/modules")
  sh('n=0; while [ $n -lt 100 ]; do test -c /dev/snd/controlC0 && test -c /dev/snd/pcmC0D4p && test -c /dev/snd/pcmC0D4c && test -c /dev/snd/pcmC0D5p && test -c /dev/snd/pcmC0D6c && exit 0; sleep 0.2; n=$((n+1)); done; exit 1',timeout=35)
  sh('test -r /proc/asound/cards && ! grep -q "no soundcards" /proc/asound/cards')
  # Do not claim calibration from an old success line belonging to a prior module generation.
  sh('! ps | grep -q "[a]lsaucm_test" || exit 12')
  must(snapshot()==s,'USB changed before ALSA calibration')
  sh('rm -f /run/alsaucm_test /run/djonehub-alsaucm.log')
  sh('nohup /usr/bin/alsaucm_test >/run/djonehub-alsaucm.log 2>&1 & n=0; while [ $n -lt 50 ] && [ ! -p /run/alsaucm_test ]; do sleep 0.1; n=$((n+1)); done; test -p /run/alsaucm_test',timeout=16)
  for req in ('open snd_soc_msm_9x07_Tomtom_I2S','set _verb VoLTE','set _enadev Auxpcm Rx','set _enadev Auxpcm Tx'):
   sh('printf "%s\\n" "'+req+'" > /run/alsaucm_test')
  sh('n=0; while [ $n -lt 100 ]; do grep -q "ACDB -> Sent VocProc Cal!" /run/djonehub-alsaucm.log 2>/dev/null && exit 0; sleep 0.1; n=$((n+1)); done; exit 1',timeout=22)
  must(snapshot()==s,'USB changed after ALSA calibration')
  sh('/tmp/djonehub-call/mavo-pcm-bridge.armv7 --check')
  sh('test "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0')
 finally:
  # If stop succeeded, failed, or timed out, inspect actual state and restore qtest.
  if stop_attempted:
   state=json.loads(docker('inspect','qdc507-hardware'))[0]['State']
   if not state['Running']: docker('start','qdc507-hardware',timeout=45)
   must(json.loads(docker('inspect','qdc507-hardware'))[0]['State']['Running'],'qtest not running after voice deployment')
   must(snapshot()==s and mapping(s),'USB or qtest IF2 mapping changed during voice deployment')
 return 'deployed'
def recover():
 initial=check(); s=snapshot()
 must(s['driver'] in ('option','usbfs','unbound'),'unexpected IF5 driver')
 # Operator confirms there are no calls; container and USB state are rechecked.
 stable(s)
 must(snapshot()==s,'USB changed at end of settle'); containers_ready()
 if not alias_ok(s):
  # Verified unique IF2 tty and stable USB generation; replace only the legacy symlink.
  docker_guard_alias(s)
  a=P('/dev/qdc507-at')
  must(not a.exists() or a.is_symlink(),'alias path occupied by a non-symlink')
  tmp=a.with_name(a.name+'.manual-'+str(os.getpid()))
  must(not tmp.exists() and not tmp.is_symlink(),'temporary alias occupied')
  os.symlink(s['node'],tmp)
  try: os.replace(tmp,a)
  finally:
   if tmp.is_symlink(): tmp.unlink()
  must(alias_ok(s),'alias repair failed')
  must(snapshot()==s,'USB changed while repairing alias')
  return dict(initial,action='IF2_ALIAS_REPAIRED',next='RUN_RECOVER_AGAIN')
 if not mapping(s):
  docker_guard(s,False)
  host_devices_ready(s)
  docker('restart','-t','25','qdc507-hardware',timeout=60)
  host_devices_ready(s)
  must(snapshot()==s and mapping(s),
       'qdc507 restart did not repair runtime device mappings')
  return dict(initial,action='QTEST_RESTARTED',next='RUN_RECOVER_AGAIN')
 if s['driver']=='option':
  docker_guard(s)
  P('/sys/bus/usb/drivers/option/unbind').write_text(s['i5'])
  after=snapshot()
  must(all(after[k]==s[k] for k in s if k!='driver') and after['driver'] in ('unbound','usbfs'),'USB changed while IF5 was released')
  return dict(initial,action='IF5_UNBOUND',next='WAIT_35S_AND_RUN_RECOVER_AGAIN')
 docker_guard(s)
 serial=online_serial(s)
 if not voice_ready(serial):
  result=deploy_voice(s)
  must(voice_ready(serial),'voice runtime not ready after deployment')
 else: result='already-ready'
 must(snapshot()==s,'USB changed during voice stage')
 must(mapping(s),'IF2 mapping lost after voice stage')
 return dict(initial,action='VERIFIED',voice=result,adb_root=True,caveat='LIVE_CALL_AND_SMS_NOT_TESTED')
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__)
 mode=p.add_mutually_exclusive_group(required=True)
 mode.add_argument('--check',action='store_true'); mode.add_argument('--recover',action='store_true')
 p.add_argument('--confirm',default='')
 a=p.parse_args(argv)
 try:
  verify_install()
  if a.recover: must(a.confirm=='RECOVER-QDC507','typed confirmation required: RECOVER-QDC507')
  if a.check:
   print(json.dumps(check(),ensure_ascii=False,sort_keys=True)); return 0
  fd=os.open(str(LOCK),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'r+') as f:
   must(fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB) is None,'lock failed')
   print(json.dumps(recover(),ensure_ascii=False,sort_keys=True))
  return 0
 except (Defer,OSError,ValueError,KeyError,TypeError,BlockingIOError) as e:
  print('DEFER: '+str(e),file=sys.stderr); return 2
if __name__=='__main__': sys.exit(main())
