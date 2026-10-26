#!/usr/bin/python3.8
"""QDC507 DSM7.1 hotplug coordinator (no Docker recreation, no kernel reload).
  --check      read-only baseline/device audit
  --adopt      narrowly re-pin audited wait40 oneclick revision (no business changes)
  --recover    one manual recovery via existing audited oneclick
  --watch      foreground watchdog; never auto-retries after any failed recovery
"""
import fcntl, hashlib, json, os, stat, subprocess, sys, tempfile, time
from pathlib import Path

os.umask(0o077)
os.environ['PATH'] = '/volume2/@appstore/Docker/usr/bin:/usr/local/bin:/usr/local/sbin:/usr/syno/bin:/usr/syno/sbin:/usr/sbin:/usr/bin:/sbin:/bin'
R = Path('/volume2/docker/asterisk/qdc507-16k-lab')
ONE = R / 'qdc507-dynusb-oneclick-v2.py'
BASE = R / 'qdc507-dynusb-oneclick-baseline-v1.json'
PENDING = R / 'qdc507-dynusb-oneclick-pending-v1.json'
MODULE = Path('/usr/local/libexec/qdc507/boot/option.ko')
GUARD = R / 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
IF = R / 'qdc507-if-role-manager-v1.py'
NODES = R / 'qdc507-device-node-sync-v1.py'
PREP = Path('/usr/local/sbin/qdc507-boot-adb.py')
NEW_DRIVER = '395918cff6c00e82cd8fbdfd99b8b2420ac9089605f6df0cc8a80550fd6d98cc'
ONE_OLD = '26ad78517e78b37b64bc78d9d20a6f55679e3e140f77247264693f5a0f1d5d26'
ONE_WAIT40 = 'd3817c52f19e6f50d03605ee4e478c07e022b442091f0b90d64f52d6a2a4160c'
C0 = 'cd1f8d1ccc3d3cd465cfefc1c87cc8521cb9979c95333d0f4e60b6f829115fd4'
IID = 'sha256:1d55612e888a736f372f275a47d6ae3f66f7843fe27ea6673ba68f6a8281e6ca'
LOCK = '/run/qdc507-hotplug-coordinator.lock'
WATCH_LOG = R / 'qdc507-hotplug-watch-v1.log'

class Stop(Exception): pass

def need(b, why):
    if not b: raise Stop(why)

def goodfile(p):
    s = p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not (s.st_mode & 0o022),
         'UNSAFE_FILE_' + p.name)
    return p

def sha(p):
    return hashlib.sha256(goodfile(p).read_bytes()).hexdigest()

def lock():
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    s = os.fstat(fd)
    need(s.st_uid == 0 and not s.st_mode & 0o077, 'BAD_WATCH_LOCK')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd

def baseline():
    goodfile(BASE)
    b = json.loads(BASE.read_text())
    need(b.get('format') == 'qdc507-dynusb-oneclick-v1' and
         b.get('container_id') == C0 and b.get('image_id') == IID and
         b.get('mode') == '16k', 'BASELINE_ID_MODE_DRIFT')
    need(isinstance(b.get('serial'), str) and b['serial'], 'SERIAL_PIN_MISSING')
    need(isinstance(b.get('usb_generation'), list) and len(b['usb_generation']) == 4,
         'USB_GENERATION_FORMAT_CHANGED')
    need(isinstance(b.get('scripts'),dict),'BASELINE_SCRIPT_HASHES_MISSING')
    return b

def validate(allow_old=False):
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(os.uname().release == '4.4.180+', 'KERNEL_CHANGED')
    need(sha(MODULE) == NEW_DRIVER, 'PERSISTENT_DRIVER_PIN_CHANGED')
    current = sha(ONE)
    need(current == ONE_WAIT40, 'WAIT40_SCRIPT_UNRECOGNIZED')
    b = baseline()
    for p in (IF, NODES, GUARD, PREP):
        need(b['scripts'].get(p.name) == sha(p), 'DEPENDENCY_PIN_DRIFT_'+p.name)
    pin = b['scripts'].get(ONE.name)
    if allow_old:
        need(pin in (ONE_WAIT40, ONE_OLD), 'ONECLICK_BASELINE_UNKNOWN')
    else:
        need(pin == current, 'ONECLICK_BASELINE_SELF_PIN_STALE_RUN_ADOPT')
    need(not PENDING.exists() and not PENDING.is_symlink(), 'ONECLICK_PENDING_REQUIRES_MANUAL_REVIEW')
    for p in ('qdc507-dynusb-mode-switch-pending.json',
              'qdc507-usb-recovery-v4-pending.json'):
        need(not (R/p).exists(), 'OTHER_RECOVERY_PENDING_'+p)
    return b, pin

def usb_sample(serial):
    root=Path('/sys/bus/usb/devices')
    found=[]
    for d in root.iterdir():
        try:
            if (d/'idVendor').read_text().strip().lower() == '2ca3' and \
               (d/'idProduct').read_text().strip().lower() == '4006':
                found.append(d)
        except OSError: pass
    need(len(found) <= 1, 'MULTIPLE_QDC507_PRESENT')
    if not found: return None
    d=found[0]
    try:
        s=(d/'serial').read_text().strip()
        bus=(d/'busnum').read_text().strip()
        dev=(d/'devnum').read_text().strip()
    except OSError: return None
    need(s.lower() == serial.lower(), 'DEVICE_SERIAL_CHANGED_NO_RECOVERY')
    return [d.name, bus, dev, s]

def command(*args, timeout=45):
    return subprocess.run(args, stdin=subprocess.DEVNULL, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)

def is_healthy():
    p=command('/usr/bin/python3.8','-I','-B',str(ONE),'--check',timeout=95)
    if p.returncode:
        print(p.stdout[-2500:],flush=True)
    need(p.returncode==0 and 'READ_ONLY_CHECK_COMPLETE=1' in p.stdout,
         'ONECLICK_READ_ONLY_CHECK_FAILED')
    return p.stdout

def check():
    b, pin=validate(allow_old=True)
    sample=usb_sample(b['serial'])
    print('NEW_OPTION_ON_DISK_OK=1')
    print('DEPENDENCY_PINS_OK=1')
    print('WAIT40_SELF_PIN=' + ('CURRENT' if pin==ONE_WAIT40 else 'OLD_NEEDS_ADOPT'))
    print('BASELINE_GENERATION=' + ':'.join(map(str,b['usb_generation'][:3])))
    print('USB_PRESENT='+str(int(sample is not None)))
    if sample:
        print('CURRENT_GENERATION=' + ':'.join(sample[:3]))
        print('GENERATION_CHANGED='+str(int(sample != b['usb_generation'])))
    if pin==ONE_WAIT40 and sample is not None and sample==b['usb_generation']:
        is_healthy()
        print('ONECLICK_READ_ONLY_HEALTH_OK=1')
    print('READ_ONLY_COORDINATOR_CHECK_OK=1')

def adopt():
    b,pin=validate(allow_old=True)
    if pin==ONE_WAIT40:
        is_healthy()
        print('WAIT40_BASELINE_ALREADY_CURRENT=1')
        return
    # Changes only the self hash after verifying that the complete original
    # known revision was replaced by the known 40s/3s revision.
    need(pin==ONE_OLD,'NOT_APPROVED_WAIT40_TRANSITION')
    sample=usb_sample(b['serial'])
    need(sample==b['usb_generation'], 'USB_CHANGED_DO_NOT_RESEAL')
    need(b['host_boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'HOST_REBOOTED_DO_NOT_RESEAL')
    for p,mark in ((IF,'HOST_USB_READY=1'),(NODES,'READ_ONLY_CHECK_PASSED=1'),
                   (GUARD,'READ_ONLY_CHECK_PASSED=1')):
        q=command('/usr/bin/python3.8','-I','-B',str(p),'--check',timeout=95)
        need(q.returncode==0 and mark in q.stdout,
             'PRE_RESEAL_READ_ONLY_CHECK_FAILED_'+p.name+':'+q.stdout[-250:])
    p=command('docker','inspect','--format','{{.Id}} {{.State.Running}} {{.State.Health.Status}}',
              'qdc507-hardware',timeout=12)
    need(p.returncode==0 and p.stdout.strip()==C0+' true healthy',
         'FORMAL_CONTAINER_NOT_HEALTHY')
    p=command('docker','exec','qdc507-hardware','asterisk','-rx',
              'core show channels count',timeout=12)
    need(p.returncode==0 and '0 active channels' in p.stdout and
         '0 active calls' in p.stdout, 'BUSINESS_NOT_IDLE')
    q=command('systemctl','is-active','qdc507-sms-tick.timer',timeout=12)
    need(q.returncode==0 and q.stdout.strip()=='active', 'SMS_TIMER_NOT_ACTIVE')
    backup=R/'qdc507-dynusb-oneclick-baseline-before-wait40-v1.json'
    need(not backup.exists(),'BASELINE_BACKUP_ALREADY_EXISTS')
    old=BASE.read_bytes()
    fd=os.open(str(backup),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(old);f.flush();os.fsync(f.fileno())
    b['scripts'][ONE.name]=ONE_WAIT40
    fd,tmp=tempfile.mkstemp(prefix='.qdc507-oneclick-pin-',dir=str(R))
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600)
            json.dump(b,f,sort_keys=True,indent=2)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,str(BASE))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    print('WAIT40_BASELINE_SELF_HASH_ADOPTED=1')
    print('BASELINE_BACKUP='+str(backup))
    is_healthy()
    print('ONECLICK_CHECK_AFTER_ADOPTION_OK=1')
    print('PRODUCTION_BUSINESS_UNCHANGED=1')

def recover(log_path=None):
    b,_=validate(allow_old=False)
    print('CALLING_EXISTING_ONECLICK_RECOVERY=1',flush=True)
    if log_path is not None:
        need(not log_path.is_symlink(), 'WATCH_LOG_SYMLINK_REFUSED')
    if log_path is None:
        p=subprocess.run(['/usr/bin/python3.8','-I','-B',str(ONE),'--recover'],
                         stdin=subprocess.DEVNULL, timeout=540)
    else:
        with open(str(log_path),'a') as f:
            f.write('RUN='+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n')
            f.flush()
            p=subprocess.run(['/usr/bin/python3.8','-I','-B',str(ONE),'--recover'],
                             stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,
                             timeout=540)
    need(p.returncode==0,'ONECLICK_RECOVERY_FAILED_STOP_NO_RETRY')
    print('RECOVERY_SUBPROCESS_EXIT_OK=1',flush=True)

def watch():
    validate(allow_old=False)
    print('HOTPLUG_WATCH_FOREGROUND_STARTED=1',flush=True)
    print('AUTO_RECOVERY_ONLY_AFTER_USB_GENERATION_CHANGE=1',flush=True)
    last=None
    since=None
    while True:
        b,_=validate(allow_old=False)
        sample=usb_sample(b['serial'])
        now=time.monotonic()
        if sample is None:
            last=None; since=None
        elif sample!=last:
            last=sample; since=now
        elif since is not None and now-since>=3:
            changed=(sample!=b['usb_generation'] or
                     Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=b['host_boot_id'])
            if changed:
                print('NEW_USB_GENERATION_STABLE_3S=1',flush=True)
                recover(WATCH_LOG)
                after,_=validate(allow_old=False)
                need(after['usb_generation']==sample,'RECOVERY_BASELINE_NOT_UPDATED')
                print('AUTO_RECOVERY_COMPLETE=1',flush=True)
                last=None;since=None
        time.sleep(1)

def main():
    need(len(sys.argv)==2 and sys.argv[1] in ('--check','--adopt','--recover','--watch'),
         'USAGE_CHECK_ADOPT_RECOVER_WATCH')
    fd=lock()
    try:
        mode=sys.argv[1]
        if mode=='--check':check()
        elif mode=='--adopt':adopt()
        elif mode=='--recover':recover()
        else:watch()
    finally:os.close(fd)

if __name__=='__main__':
    try:main()
    except (Stop, OSError, ValueError, KeyError, BlockingIOError, subprocess.TimeoutExpired) as e:
        print('HOTPLUG_COORDINATOR_STOPPED='+str(e)[:400],file=sys.stderr)
        sys.exit(2)
