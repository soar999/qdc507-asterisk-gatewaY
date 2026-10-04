#!/usr/bin/python3.8
"""Root-only QDC507 boot preparation. Never starts Docker or performs hotplug recovery."""
import fcntl,hashlib,os,pathlib,stat,subprocess,sys,time
P=pathlib.Path
BIN=P('/usr/local/libexec/qdc507/boot')
LOCK=P('/run/qdc507-boot-prep.lock');REPORT=P('/var/lib/qdc507-boot-prep-last.txt')
ADB=P('/usr/local/libexec/qdc507/adb');HELP=BIN/'qdc507-bind-if2.sh'
RULE=P('/etc/udev/rules.d/99-qdc507-permissions.rules')
RULE_PIN='858223cfef8de10a08437e940461646c30aa33c0b90c9d9057ab448016424a85'
KOS={'usb_wwan.ko':'dff9cf76bb317e484b2314ad0a617d29106e6ce1d751417e33f692eb9a633002','option.ko':'944696cdcebaf0cf3195e2029834b83e12c35d30200801f52c48f849e46025bc'}
MODULES=('usbserial','usb_wwan','option','soundcore','snd','snd_timer','snd_pcm','snd_hwdep','snd_seq_device','snd_rawmidi','snd_usbmidi_lib','snd_usb_audio')
class Halt(Exception):pass
def need(ok,why):
 if not ok:raise Halt(why)
def say(s):
 with REPORT.open('a') as f:f.write(s+'\n')
def trusted(p,h):
 for x in [*reversed(p.parents),p]:
  s=x.lstat();need(s.st_uid==0 and not s.st_mode&0o022 and not stat.S_ISLNK(s.st_mode),'UNTRUSTED:'+str(x))
 need(hashlib.sha256(p.read_bytes()).hexdigest()==h,'HASH_CHANGED:'+str(p))
def loaded(name):return any(x.startswith(name+' ') for x in P('/proc/modules').read_text().splitlines())
def call(argv,timeout=15):
 r=subprocess.run(list(map(str,argv)),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root'},timeout=timeout)
 need(r.returncode==0,'CMD_FAILED:'+str(argv[0]));return r.stdout.decode(errors='replace').strip()
def module(name):
 if loaded(name):return
 f=({'usb_wwan':'usb_wwan.ko','option':'option.ko'}.get(name,name.replace('_','-')+'.ko'))
 s=P('/lib/modules')/f
 if not s.is_file():s=BIN/f
 need(s.is_file(),'MODULE_FILE_MISSING:'+name)
 if s.parent==BIN:trusted(s,KOS[f])
 else:need(s.stat().st_uid==0 and not s.stat().st_mode&0o022,'MODULE_UNTRUSTED:'+name)
 call(['/sbin/insmod',s],timeout=12)
 need(loaded(name),'MODULE_NOT_LOADED:'+name)
def usb():
 xs=[]
 for d in P('/sys/bus/usb/devices').iterdir():
  try:
   if (d/'idVendor').read_text().strip()=='2ca3' and (d/'idProduct').read_text().strip()=='4006':xs.append(d)
  except OSError:pass
 need(len(xs)==1,'USB_NOT_UNIQUE');return xs[0]
def main():
 need(os.geteuid()==0,'ROOT_REQUIRED')
 need(call(['/bin/uname','-r'])=='4.4.180+','KERNEL_CHANGED')
 for n,h in KOS.items():trusted(BIN/n,h)
 trusted(HELP,'a124db46bcba0a36a3df5a1bd186bc645472d3a91a351004f85cc3f453737f69')
 trusted(ADB,'a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01')
 trusted(RULE,RULE_PIN)
 fd=os.open(str(LOCK),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'r+') as l:
  fcntl.flock(l,fcntl.LOCK_EX|fcntl.LOCK_NB)
  for n in ('usbserial','usb_wwan','option'):module(n)
  # option's broad new_id may also grab sound interfaces: audio repair runs next.
  at=call(['/bin/sh',HELP],timeout=40)
  need(at.startswith('/dev/ttyUSB'),'IF2_HELPER_NO_TTY')
  for n in MODULES[3:]:module(n)
  d=usb();i=P('/sys/bus/usb/devices')/(d.name+':1.2')
  need((i/'bInterfaceNumber').read_text().strip()=='02','IF2_DESCRIPTOR_CHANGED')
  a=P('/dev/qdc507-at');need(a.is_symlink() and a.resolve(strict=True)==P(at),'IF2_ALIAS_BAD')
  st=a.stat();need(stat.S_ISCHR(st.st_mode),'IF2_NOT_CHAR')
  need(st.st_gid==1000 and stat.S_IMODE(st.st_mode)==0o660,'IF2_NODE_PERMISSION_NOT_READY')
  print('USB_GENERATION='+d.name+':'+(d/'devnum').read_text().strip())
  print('IF2_READY=1')
if __name__=='__main__':
 try:
  with REPORT.open('w') as f:
   old=sys.stdout;sys.stdout=f
   try:print('TIME='+time.strftime('%F %T %z'));main();print('EXIT=0');code=0
   finally:sys.stdout=old
 except Exception as e:say('DEFER='+str(e));say('EXIT=2');code=2
 REPORT.chmod(0o644);print('BOOT_PREP_EXIT='+str(code));sys.exit(code)
