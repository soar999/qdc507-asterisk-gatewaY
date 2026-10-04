#!/usr/bin/python3.8
"""One-shot DSM host QDC507 USB-audio repair; no Docker/ADB/AT access."""
import fcntl, os, pathlib, stat, sys, time
P=pathlib.Path
BASE=P('/sys/bus/usb/devices')
OPT=P('/sys/bus/usb/drivers/option')
SND=P('/sys/bus/usb/drivers/snd-usb-audio')
LOCK=P('/run/qdc507-host-audio-repair.lock')
REPORT=P('/var/lib/qdc507-host-audio-repair-last.txt')
class Abort(Exception):pass
def need(ok,why):
 if not ok:raise Abort(why)
def log(s):
 with REPORT.open('a') as f:f.write(s+'\n')
def device():
 ds=[]
 for d in BASE.iterdir():
  try:
   if (d/'idVendor').read_text().strip().lower()=='2ca3' and (d/'idProduct').read_text().strip().lower()=='4006':ds.append(d)
  except OSError:pass
 need(len(ds)==1,'USB_NOT_UNIQUE');return ds[0]
def driver(i):
 p=i/'driver';return p.resolve(strict=True).name if p.is_symlink() else 'none'
def shot(d):
 need(d.exists(),'USB_DISAPPEARED')
 return (d.name,(d/'busnum').read_text().strip(),(d/'devnum').read_text().strip(),(d/'serial').read_text().strip())
def audio_ok(d):
 cards=P('/proc/asound/cards').read_text()
 nodes=('controlC0','pcmC0D0p','pcmC0D0c')
 if 'Baiwang' not in cards or not all((P('/dev/snd')/n).is_char_device() for n in nodes):return False
 control=P('/sys/class/sound/controlC0/device').resolve()
 return (d.resolve() in control.parents or control==d.resolve())
def assert_gen(d,g):need(shot(d)==g,'USB_GENERATION_CHANGED_STOP_NO_ROLLBACK')
def bind(path,name):
 with (path/'bind').open('w') as f:f.write(name)
def unbind(path,name):
 with (path/'unbind').open('w') as f:f.write(name)
def run():
 need(os.geteuid()==0,'ROOT_REQUIRED')
 fd=os.open(str(LOCK),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'r+') as f:
  st=os.fstat(f.fileno());need(st.st_uid==0 and not st.st_mode&0o077,'LOCK_UNTRUSTED')
  fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  d=device();g=shot(d)
  log('USB_GENERATION='+g[0]+':'+g[2])
  need('snd_usb_audio ' in P('/proc/modules').read_text(),'SND_USB_AUDIO_NOT_LOADED')
  expected={6:('06','01','01','00'),7:('07','01','02','00'),8:('08','01','02','00')}
  interfaces={}
  for n,e in expected.items():
   i=BASE/(d.name+':1.'+str(n));need(i.is_dir(),'AUDIO_IF_MISSING_'+str(n))
   desc=tuple((i/x).read_text().strip().lower() for x in ('bInterfaceNumber','bInterfaceClass','bInterfaceSubClass','bInterfaceProtocol'))
   need(desc==e,'AUDIO_DESCRIPTOR_MISMATCH_'+str(n));interfaces[n]=i
  original={n:driver(i) for n,i in interfaces.items()}
  need(all(v in ('option','snd-usb-audio','none') for v in original.values()),'UNEXPECTED_DRIVER')
  log('DRIVERS_BEFORE='+','.join('%d:%s'%(n,original[n]) for n in sorted(original)))
  if audio_ok(d) and all(driver(i)=='snd-usb-audio' for i in interfaces.values()):
   log('ALREADY_HEALTHY=1');return
  changed=[]
  try:
   # Release streaming IF7/8 before AudioControl IF6 so ALSA sees the complete device.
   for n in (7,8,6):
    i=interfaces[n]
    assert_gen(d,g)
    if driver(i)=='option':unbind(OPT,i.name);changed.append(n)
    need(driver(i)!='option','OPTION_UNBIND_FAILED_'+str(n))
   assert_gen(d,g)
   # Bind only AudioControl (IF6); snd-usb-audio claims its streaming interfaces itself.
   i=interfaces[6]
   if driver(i)=='none':bind(SND,i.name)
   need(driver(i)=='snd-usb-audio','AUDIO_CONTROL_BIND_FAILED')
   for _ in range(12):
    assert_gen(d,g)
    if audio_ok(d):break
    time.sleep(1)
   need(audio_ok(d),'SOUNDCARD_NODES_MISSING')
   need(all(driver(i)=='snd-usb-audio' for i in interfaces.values()),'AUDIO_DRIVER_DRIFT')
   log('DRIVERS_AFTER='+','.join('%d:%s'%(n,driver(interfaces[n])) for n in sorted(interfaces)))
   log('HOST_AUDIO_OK=1')
  except Exception:
   if shot(d)==g:
    log('ROLLBACK_BEGIN=1')
    # Never tear down an already-created sound card solely to force option back.
    if not audio_ok(d):
     for n in changed:
      i=interfaces[n]
      try:
       if driver(i)=='snd-usb-audio':unbind(SND,i.name)
      except OSError:log('ROLLBACK_UNBIND_FAILED_'+str(n))
     for n in changed:
      i=interfaces[n]
      try:
       if driver(i)=='none':bind(OPT,i.name)
      except OSError:log('ROLLBACK_BIND_FAILED_'+str(n))
    log('ROLLBACK_DRIVERS='+','.join('%d:%s'%(n,driver(interfaces[n])) for n in sorted(interfaces)))
   raise
if __name__=='__main__':
 try:
  REPORT.write_text('TIME='+time.strftime('%F %T %z')+'\n')
  run();log('EXIT=0')
 except Exception as exc:
  log('DEFER='+str(exc));log('EXIT=2');REPORT.chmod(0o644);sys.exit(2)
 REPORT.chmod(0o644)
