#!/usr/bin/python3.8
"""Staged NAS startup entry: --selftest, --check, --boot.
Not enabled by extraction.  --boot is a real recovery action and must not be
run on a working NAS before a controlled first boot validation.
"""
import fcntl, hashlib, os, re, stat, subprocess, sys, time
from pathlib import Path
LAB=Path('/volume2/docker/asterisk/qdc507-16k-lab')
OLD=LAB/'qdc507-coldboot-memoryfix-v221.py'
NEW=LAB/'qdc507-coldboot-memoryfix-dsm722-staged.py'
OLD_SHA='87f8e033b95a09e13f2a701a18ce6c66931dce342c028b1639da10f20f5afc55'
NEW_SHA='2e83b1a3e108cb5d2f71288373123ea8a2886ba4373cd8d970617443dd900e7e'
WATCH=LAB/'qdc507-adb-watch-alert-memoryfix-v2.py'
AUDIT=LAB/'qdc507-memoryfix-v22-audit.py'
PY='/usr/bin/python3.8'
OLD_WATCH='qdc507-adb-watch-alert-v2.1.py'
EXPECTED_MARKER='WATCH_V2_3_AUTO_RECOVERY_STARTED'

class Stop(Exception):pass
def need(cond,why):
    if not cond:raise Stop(why)
def trusted_sha(path, expected):
    s=path.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and
         not stat.S_IMODE(s.st_mode)&0o022,'FILE_UNTRUSTED_'+path.name)
    need(hashlib.sha256(path.read_bytes()).hexdigest()==expected,
         'SHA_CHANGED_'+path.name)
def run(argv,timeout=180):
    try:
        p=subprocess.run(list(map(str,argv)),stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise Stop('COMMAND_UNAVAILABLE_OR_TIMEOUT_'+type(e).__name__)
    if p.stdout:print(p.stdout,end='' if p.stdout.endswith('\n') else '\n',flush=True)
    need(p.returncode==0,'COMMAND_FAILED_'+str(argv[-2:])+'_RC'+str(p.returncode))
    return p.stdout

def watcher_pids():
    old=[];new=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:args=(p/'cmdline').read_bytes().split(b'\0')
        except (OSError,PermissionError):continue
        aa=[x.decode('utf-8','replace') for x in args if x]
        if '--watch' not in aa:continue
        if str(WATCH) in aa:new.append(int(p.name))
        if str(LAB/OLD_WATCH) in aa:old.append(int(p.name))
    return old,new

def check_sources():
    trusted_sha(OLD,OLD_SHA);trusted_sha(NEW,NEW_SHA)
    o=OLD.read_text();n=NEW.read_text()
    changes = [
        ("os.uname().release == '4.4.180+'", "os.uname().release == '4.4.302+'"),
        ("EXPECTED_OPTION = '395918cff6c00e82cd8fbdfd99b8b2420ac9089605f6df0cc8a80550fd6d98cc'", "EXPECTED_OPTION = '37a3501d8e9eb9a90ad610905bc974458e6c9b3980eaecda20f6142d2b431bdd'"),
        ("OPTION = Path('/usr/local/libexec/qdc507/boot/option.ko')", "OPTION = Path('/usr/local/libexec/qdc507/boot/qdc507-dsm722-option.ko')"),
        ("STAGE = LAB / 'qdc507-dsm71-option-install-stage-v1/manifest.json'", "STAGE = Path('/usr/local/libexec/qdc507/boot/qdc507-dsm722-boot-manifest.json')"),
    ]
    for before, after in changes:
        need(o.count(before)==1, 'COLDBOOT_SOURCE_PATTERN_DRIFT')
        o=o.replace(before,after,1)
    need(n==o,'COLDBOOT_UNEXPECTED_SOURCE_CHANGE')
    trusted_sha(LAB/'qdc507-dsm722-boot-manifest-guard-stage-v3.json',
        'c0817b398ca7850a9c49e2bb1e47122578b41ff3cc7ee5a485cb914fc65cd79a')
    trusted_sha(LAB/'qdc507-boot-prep-dsm722-guard-stage-v3.py',
        '9ea1beb7dc85581779de64be64b9bbbe4958b03e5c4501da376c48c88e49bee5')
    trusted_sha(Path('/usr/local/libexec/qdc507/boot/qdc507-dsm722-option.ko'),
        '37a3501d8e9eb9a90ad610905bc974458e6c9b3980eaecda20f6142d2b431bdd')
    trusted_sha(Path('/usr/local/libexec/qdc507/boot/qdc507-dsm722-usb_wwan.ko'),
        'eef8881c80f5e73dc9cfef6283562106de30fd72a6e0dfdc1653a30a08981075')
    need(WATCH.is_file() and not WATCH.is_symlink() and AUDIT.is_file() and not AUDIT.is_symlink(),
         'WATCH_OR_AUDIT_MISSING')
    print('DSM722_COLDBOOT_FOUR_PATCHES=PASS',flush=True)

def main():
    need(len(sys.argv)==2 and sys.argv[1] in ('--selftest','--static-check','--check','--boot'),
         'USE_SELFTEST_CHECK_OR_BOOT')
    mode=sys.argv[1]
    if mode=='--selftest':
        need(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!='', 'SELF_HASH_UNAVAILABLE')
        print('V221_OFFLINE_ENTRY_SELFTEST=PASS');return
    if mode in ('--check','--boot'):
        raise Stop('DSM722_RUNTIME_NOT_RELEASED_PENDING_BASELINE_AUDIT')
    need(os.geteuid()==0,'ROOT_REQUIRED')
    check_sources()
    if mode=='--static-check':
        print('DSM722_STARTUP_STATIC_CHECK=PASS',flush=True)
        print('NO_RECOVERY_EXECUTED=1',flush=True)
        return
    if mode=='--check':
        run([PY,'-I','-B',str(AUDIT),'--deep'],timeout=240)
        run([PY,'-I','-B',str(NEW),'--check'],timeout=240)
        old,new=watcher_pids()
        need(not old and len(new)<=1,'LEGACY_OR_DUPLICATE_WATCHDOG')
        print('V221_BOOT_ENTRY_READ_ONLY_CHECK=PASS');return
    # --boot: only in an actual startup maintenance window.
    old,new=watcher_pids()
    need(not old and len(new)<=1,'LEGACY_OR_DUPLICATE_WATCHDOG')
    fd=os.open('/run/qdc507-startup-v221.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not(st.st_mode&0o077),
             'STARTUP_LOCK_UNTRUSTED')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Stop('ANOTHER_STARTUP_ACTIVE')
        print('V221_COLDBOOT_BEGIN=1',flush=True)
        run([PY,'-I','-B',str(NEW),'--boot'],timeout=1150)
        print('V221_COLDBOOT_COMPLETE=1',flush=True)
        # Only after successful coldboot + Guard16 verification.
        run([PY,'-I','-B',str(WATCH),'--check-recovery'],timeout=95)
        old,new=watcher_pids()
        need(not old and len(new)<=1,'WATCHDOG_COLLISION_AFTER_BOOT')
        if new:
            print('V221_NEW_WATCHDOG_ALREADY_RUNNING_PID='+str(new[0]));return
        log=LAB/('qdc507-adb-watch-afterboot-'+time.strftime('%Y%m%d-%H%M%S')+'.log')
        with log.open('x') as fp:
            p=subprocess.Popen([PY,'-u','-I','-B',str(WATCH),'--watch'],
                   stdin=subprocess.DEVNULL,stdout=fp,stderr=subprocess.STDOUT,
                   start_new_session=True)
        time.sleep(5)
        need(p.poll() is None,'WATCHDOG_EXITED_EARLY_LOG_'+str(log))
        need(EXPECTED_MARKER in log.read_text(errors='replace'),
             'WATCHDOG_START_MARKER_NOT_FOUND_LOG_'+str(log))
        old,new=watcher_pids()
        need(not old and new==[p.pid], 'WATCHDOG_NOT_UNIQUE')
        print('V221_NEW_WATCHDOG_STARTED_PID='+str(p.pid))
        print('V221_NEW_WATCHDOG_LOG='+str(log))
        print('V221_STARTUP_COMPLETE=1')
    finally:
        os.close(fd)
if __name__=='__main__':
    try: main()
    except (Stop,OSError,ValueError,subprocess.TimeoutExpired) as e:
        print('V221_STARTUP_STOP='+type(e).__name__+':'+str(e)[:300],file=sys.stderr)
        print('NO_AUTOMATIC_RETRY=1',file=sys.stderr)
        sys.exit(2)
