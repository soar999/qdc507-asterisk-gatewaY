#!/usr/bin/python3.8
"""Safely retire the legacy DSM7.1 coordinator from *manual USB/SIM swap* recovery.

Usage: --check (read only), --install (exact pinned update), --rollback (restore
backed up original files). Does not execute recovery, alter Docker, USB, tasks,
or services. Uses existing shared lock with the installed V221 coldboot.
"""
import ast
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

LAB=Path('/volume2/docker/asterisk/qdc507-16k-lab')
USB=LAB/'qdc507-usb-recover-memoryfix-v22.sh'
AUDIT=LAB/'qdc507-memoryfix-v22-audit.py'
OLD_MANIFEST=LAB/'qdc507-memoryfix-v22-production-20261121.json'
NEW_MANIFEST=LAB/'qdc507-memoryfix-v22-production-20261121-nocoordinator.json'
CORE=LAB/'qdc507-coldboot-memoryfix-v221.py'
STARTUP=LAB/'qdc507-coldboot-startup-v221.py'
USB_CAND=LAB/'qdc507-usb-recover-memoryfix-v22.nocoordinator-20261121.candidate.sh'
AUDIT_CAND=LAB/'qdc507-memoryfix-v22-audit.nocoordinator-20261121.candidate.py'
MAN_CAND=LAB/'qdc507-memoryfix-v22-production-20261121-nocoordinator.candidate.json'
STAMP=LAB/'qdc507-nocoordinator-install-state-v1.json'
OLD_USB_SHA='a9ae0b71f42dc74f829c6827f8e1c3c34404ac0671677c1e0a29a3ab81059cb4'
NEW_USB_SHA='2d38f76958cb362aa789dff16e1c820757586bf66b8c3808e0a5356df69d9d47'
OLD_AUDIT_SHA='da805cb402e59c8b7571ebd096f0a6910a02ebd1dca3888ef7910ab366fdbf6b'
NEW_AUDIT_SHA='cbf113687b1a4d2434b8f422c76be78b592fd09afaea30e1d7a58a4a384b5f5d'
OLD_MAN_SHA='1cfb2b0b1c817e0de93ecd060bf512633612b30dbc5b0b29b10582db444b5ac6'
NEW_MAN_SHA='02d0d7153c6b32a408f46253a7b5a989dbdf5eb5876fb909e421cf8acd20e3b7'
CORE_SHA='afb40eeddafd5148ce86504641dbadf8b99f8af560b847709f82dd9985204a4d'
STARTUP_SHA='4ecb63fcf705358c357c18c16435b4ec1f456cb6861dd9f05f9241d6f3a2f356'
LOCKS=(
    '/run/qdc507-startup-v221.lock',
    '/run/qdc507-usb-sim-swap-v2.lock',
    '/run/qdc507-hotplug-coordinator.lock',
)
class Stop(Exception):pass
def need(ok,msg):
    if not ok:raise Stop(msg)
def sha(data):return hashlib.sha256(data).hexdigest()
def trusted(p, private=False):
    s=p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1
         and not (stat.S_IMODE(s.st_mode) & (0o077 if private else 0o022))
         and 100<=s.st_size<=200000,'UNTRUSTED_'+p.name)
    return p.read_bytes()
def h(p,private=False):return sha(trusted(p,private))
def current_state():
    a=h(AUDIT); u=h(USB)
    if a==OLD_AUDIT_SHA and u==OLD_USB_SHA:return 'ORIGINAL'
    if a==NEW_AUDIT_SHA and u==NEW_USB_SHA:
        need(h(NEW_MANIFEST,True)==NEW_MAN_SHA,'NEW_MANIFEST_DRIFT')
        return 'UPDATED'
    raise Stop('MIXED_OR_UNRECOGNIZED_PRODUCTION_SOURCE_AUDIT_'+a[:12]+'_USB_'+u[:12])
def verify():
    need(os.geteuid()==0 and LAB.is_dir() and not LAB.is_symlink(),
         'ROOT_OR_LAB_INVALID')
    need(h(OLD_MANIFEST,True)==OLD_MAN_SHA,'CURRENT_20261121_MANIFEST_CHANGED')
    need(h(CORE)==CORE_SHA and h(STARTUP)==STARTUP_SHA,
         'INSTALLED_V221_SOURCES_CHANGED')
    need(h(USB_CAND)==NEW_USB_SHA and h(AUDIT_CAND)==NEW_AUDIT_SHA and
         h(MAN_CAND)==NEW_MAN_SHA,'CANDIDATE_HASH_CHANGED')
    newusb=trusted(USB_CAND).decode()
    need("str(ONE),'--recover'" in newusb and
         'str(HOT)' not in newusb and
         "'/run/qdc507-hotplug-coordinator.lock'" in newusb and
         'GUARD16_V2_ENSURE_BEGIN=1' in newusb and
         'USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1' in newusb,
         'USB_DIRECT_RECOVERY_STRUCTURE_CHANGED')
    need(newusb.count("<<'PY'\n")==1 and newusb.endswith('\nPY\n'),
         'USB_PYTHON_EMBED_FORMAT_INVALID')
    inner=newusb.split("<<'PY'\n",1)[1].rsplit('\nPY\n',1)[0]
    ast.parse(inner)
    ast.parse(trusted(AUDIT_CAND).decode())
    shell=subprocess.run(['/bin/sh','-n',str(USB_CAND)],
                          stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=10)
    need(shell.returncode==0,'USB_SHELL_SYNTAX_CHANGED')
    manifest=json.loads(trusted(MAN_CAND))
    need(manifest.get('source_sha256',{}).get(USB.name)==NEW_USB_SHA and
         'qdc507-hotplug-recovery-memoryfix-v22.py' not in
         manifest.get('source_sha256',{}), 'NEW_MANIFEST_RETIRED_PIN_MISSING')
    print('V221_AND_CURRENT_SOURCE_PINS_VERIFIED=1',flush=True)
    print('DIRECT_ONECLICK_SHARED_LOCK_STRUCTURE_VERIFIED=1',flush=True)
    print('AUDIT_AND_MANIFEST_CANDIDATES_VERIFIED=1',flush=True)
def acquire():
    fds=[]
    try:
        for p in LOCKS:
            fd=os.open(p,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            fds.append(fd)
            s=os.fstat(fd)
            need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and
                 not (stat.S_IMODE(s.st_mode)&0o077),
                 'UNTRUSTED_RUNTIME_LOCK_'+Path(p).name)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Stop('RECOVERY_OR_COLD_BOOT_IN_PROGRESS')
        return fds
    except BaseException:
        for fd in reversed(fds):os.close(fd)
        raise
def write_file(path,data,mode=0o600):
    fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as fp:
        os.fchown(fp.fileno(),0,0)
        os.fchmod(fp.fileno(),mode)
        fp.write(data)
        fp.flush();os.fsync(fp.fileno())
def replace(path,data):
    temp=LAB/('.qdc507-nocoordinator-'+str(os.getpid())+'.tmp')
    try:
        write_file(temp,data,0o700)
        os.replace(str(temp),str(path))
        dirfd=os.open(str(LAB),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:
        if temp.exists():temp.unlink()
def install():
    held=acquire()
    try:
        need(current_state()=='ORIGINAL','PRODUCTION_NOT_ORIGINAL')
        need(not os.path.lexists(str(STAMP)), 'INSTALL_STATE_ALREADY_EXISTS')
        need(not os.path.lexists(str(NEW_MANIFEST)), 'NEW_MANIFEST_ALREADY_EXISTS')
        old_usb=trusted(USB,private=True)
        old_audit=trusted(AUDIT)
        need(sha(old_usb)==OLD_USB_SHA and sha(old_audit)==OLD_AUDIT_SHA,
             'LIVE_SOURCE_CHANGED_BEFORE_INSTALL')
        ts=time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
        bu=LAB/('qdc507-usb-before-retire-coordinator-'+ts+'.sh')
        ba=LAB/('qdc507-audit-before-retire-coordinator-'+ts+'.py')
        write_file(bu,old_usb)
        write_file(ba,old_audit)
        need(h(bu,True)==OLD_USB_SHA and h(ba,True)==OLD_AUDIT_SHA,
             'BACKUP_VERIFICATION_FAILED')
        record={'format':'qdc507-nocoordinator-install-state-v1',
                'backup_usb':bu.name,'backup_audit':ba.name,
                'usb_before':OLD_USB_SHA,'usb_after':NEW_USB_SHA,
                'audit_before':OLD_AUDIT_SHA,'audit_after':NEW_AUDIT_SHA,
                'manifest_after':NEW_MAN_SHA}
        write_file(STAMP,(json.dumps(record,indent=2)+'\n').encode())
        need(current_state()=='ORIGINAL','LIVE_SOURCE_CHANGED_DURING_INSTALL')
        write_file(NEW_MANIFEST,trusted(MAN_CAND),0o600)
        need(h(NEW_MANIFEST,True)==NEW_MAN_SHA,'NEW_MANIFEST_WRITE_FAILED')
        replace(USB,trusted(USB_CAND))
        replace(AUDIT,trusted(AUDIT_CAND))
        need(current_state()=='UPDATED','INSTALL_RESULT_HASH_FAILED')
        print('LEGACY_COORDINATOR_REMOVED_FROM_SIM_SWAP_CALL_CHAIN=1',flush=True)
        print('V221_COLDBOOT_AND_SHARED_LOCK_PRESERVED=1',flush=True)
        print('ONECLICK_GUARD16_AND_V43_SOURCES_UNCHANGED=1',flush=True)
        print('NO_DOCKER_USB_SMS_OR_SCHEDULER_ACTIONS=1',flush=True)
        print('ROLLBACK_STATE='+str(STAMP),flush=True)
    finally:
        for fd in reversed(held):os.close(fd)
def rollback():
    held=acquire()
    try:
        rec=json.loads(trusted(STAMP,True))
        need(rec.get('format')=='qdc507-nocoordinator-install-state-v1' and
             rec.get('usb_before')==OLD_USB_SHA and
             rec.get('usb_after')==NEW_USB_SHA and
             rec.get('audit_before')==OLD_AUDIT_SHA and
             rec.get('audit_after')==NEW_AUDIT_SHA and
             rec.get('manifest_after')==NEW_MAN_SHA,'ROLLBACK_RECORD_CHANGED')
        bu=LAB/rec['backup_usb'];ba=LAB/rec['backup_audit']
        need(bu.parent==LAB and bu.name.startswith('qdc507-usb-before-retire-coordinator-')
             and ba.parent==LAB and ba.name.startswith('qdc507-audit-before-retire-coordinator-'),
             'ROLLBACK_PATH_CHANGED')
        rawusb=trusted(bu,True);rawaudit=trusted(ba,True)
        need(sha(rawusb)==OLD_USB_SHA and sha(rawaudit)==OLD_AUDIT_SHA,
             'ROLLBACK_BACKUP_CHANGED')
        need(h(USB) in (OLD_USB_SHA,NEW_USB_SHA) and
             h(AUDIT) in (OLD_AUDIT_SHA,NEW_AUDIT_SHA),
             'PRODUCTION_CHANGED_REFUSE_ROLLBACK')
        if h(USB)==NEW_USB_SHA:replace(USB,rawusb)
        if h(AUDIT)==NEW_AUDIT_SHA:replace(AUDIT,rawaudit)
        need(current_state()=='ORIGINAL','ROLLBACK_DID_NOT_RESTORE_ORIGINAL')
        print('ORIGINAL_USB_AND_AUDIT_RESTORED=1',flush=True)
        print('RETIRED_COORDINATOR_AVAILABLE_FOR_LEGACY_ROLLBACK=1',flush=True)
        print('NEW_MANIFEST_RETAINED_AS_HISTORY=1',flush=True)
    finally:
        for fd in reversed(held):os.close(fd)
def main():
    need(len(sys.argv)==2 and sys.argv[1] in ('--check','--install','--rollback'),
         'USE_--check_--install_--rollback')
    if sys.argv[1]=='--rollback':rollback();return
    verify()
    state=current_state()
    if sys.argv[1]=='--check':
        print('COORDINATOR_RETIREMENT_STATE='+state,flush=True)
        print('NO_WRITES_READ_ONLY_CHECK=1',flush=True)
    else:
        need(state=='ORIGINAL','INSTALL_REQUIRES_ORIGINAL_STATE')
        install()
if __name__=='__main__':
    try:main()
    except (Stop,OSError,KeyError,ValueError,TypeError,SyntaxError,
            BlockingIOError,subprocess.TimeoutExpired) as exc:
        print('COORDINATOR_RETIRE_STOP='+type(exc).__name__+':'+str(exc)[:380],file=sys.stderr)
        print('NO_AUTOMATIC_RETRY=1',file=sys.stderr)
        sys.exit(2)
