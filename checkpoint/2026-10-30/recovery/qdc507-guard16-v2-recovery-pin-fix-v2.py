#!/usr/bin/python3.8
# coding: utf-8
"""QDC507 Guard16 v2 recovery pin fix, revision 2 (nested oneclick lock fix).

This intentionally does NOT edit oneclick, coldboot, device binaries, the
container or the SMS service. It updates exactly one sealed Guard16 checker
file and exactly one SHA256 entry in the existing recovery baseline.

--check           strict read-only source/pin preflight
--install         backup original checker/baseline, install audited v2 branch
--adopt-generation ONLY after manual post-boot health and telephone testing;
                   reseal current USB generation and boot ID without restart
--rollback        requires device has already been safely restored to Guard16 v1;
                   restore checker and its SHA pin, preserving current generation

Do not publish backups: the Guard16 source and baseline contain a device ID.
NAS: DS918+, DSM 7.1.1 / Python 3.8, exact existing pinned files only.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LAB=Path('/volume2/docker/asterisk/qdc507-16k-lab')
PROD=Path('/volume2/docker/asterisk/qdc507-current/recovery')
GUARD=LAB/'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
BASE=LAB/'qdc507-dynusb-oneclick-baseline-v1.json'
ONE=LAB/'qdc507-dynusb-oneclick-v2.py'
COLD=PROD/'qdc507-coldboot-production-v3.py'
HELPER=LAB/'qdc507-guard16-v2-runtime-restore-v2.py'
PREAUDIO='/opt/qdc16k-preaudio-manual/prepare.py'
PREAUDIO_PATCH_SHA='1332d6fcf5a4b3f4f7a70180e8632992e30325256b415a4f8fc6417cf7bc0665'
ORIG_GUARD_SHA='cf364634b76c0aec0fc8f0c5db5152422cfddca09336c6f1eb8a9be66f9de9df'
ONE_SHA='d3817c52f19e6f50d03605ee4e478c07e022b442091f0b90d64f52d6a2a4160c'
COLD_SHA='a6509d867269e401ec3c17c2fcb609189c7c8f356bdd5259225eb69eac70cbc0'
HELPER_SHA='077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55'
V2_SHA='0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913'
BACKUP_G=LAB/'qdc507-guard16-v1-checker-before-v2.py.private.bak'
BACKUP_B=LAB/'qdc507-dynusb-baseline-before-v2-check.json.private.bak'
BACKUP_ADOPT=LAB/'qdc507-dynusb-baseline-before-generation-adopt.json.private.bak'
PENDING=(
 'qdc507-dynusb-oneclick-pending-v1.json',
 'qdc507-dynusb-mode-switch-pending.json',
 'qdc507-usb-recovery-v4-pending.json',
 'qdc507-dynusb-cutover-pending-v1.json',
)
LOCKS=(
 '/run/qdc507-coldboot-v2.lock',
 '/run/qdc507-hotplug-coordinator.lock',
 '/run/qdc507-dynusb-oneclick.lock',
 '/run/qdc507-dynusb-mode.lock',
 '/run/qdc507-v4-usb.lock',
 '/run/qdc507-v4-boot.lock',
 '/run/qdc507-guard16-v2-runtime-restore.lock',
)
ORIGINAL_ANCHOR=(
 "    elif is_runtime_active(state):\n"
 "        emit('GUARD16_DEVICE_STATE', 'ACCEPTED_RUNTIME_PINNED')\n"
)
# Intentionally extends ONLY the read-only --check branch. The historical
# --activate/--revert actions retain all their original checks and semantics.
EXTRA_CHECK="""    elif (state['CURRENT_HASH'] == CLOCK_SHA and
          state['CURRENT_IS_LINK'] == '1' and state['BACKUP_HASH'] == 'ABSENT'):
        # A fresh device /tmp restored by the PreAudio boot entrypoint creates
        # a *symlink to pinned v1*, without a historical V7 A/B backup.
        # This is distinct from the legacy V7 --activate branch above.
        need(state['CURRENT_TARGET'] == STAGED and
             state['STAGED_HASH'] == CLOCK_SHA and
             state['STAGED_EXEC'] == '1' and state['STAGED_IS_LINK'] == '0' and
             state['OLD_AB_BACKUP'] == 'ABSENT' and
             state['V1_AB_BACKUP'] == 'ABSENT',
             'FRESH_PREAUDIO_V1_LINK_NOT_RECOGNIZED')
        helper_path = LAB / 'qdc507-guard16-v2-runtime-restore-v2.py'
        need(sha256(helper_path) == '077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55',
             'FRESH_PREAUDIO_V1_VERIFIER_HASH_CHANGED')
        code, result, error = command(['/usr/bin/python3.8', '-I', '-B',
                                       str(helper_path), '--check'], 90, strict=False)
        need(code == 0 and all(mark in result.splitlines() for mark in
             ('READ_ONLY_RUNTIME_CHECK_OK=1', 'DEVICE_CURRENT_MODE=v1',
              'DEVICE_V1_BACKUP=absent')),
             'FRESH_PREAUDIO_V1_VERIFIER_FAILED_' + error[-100:])
        emit('GUARD16_DEVICE_STATE', 'FRESH_PREAUDIO_V1_LINK_VERIFIED')
    elif state['CURRENT_HASH'] == '0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913':
        # Guard16 v2 is valid only when the separate, SHA-pinned verifier
        # proves the active v2 link, both binary hashes, and saved v1 rollback.
        need(state['CURRENT_IS_LINK'] == '1', 'GUARD16_V2_ACTIVE_LINK_REQUIRED')
        need(state['STAGED_HASH'] == CLOCK_SHA and state['STAGED_EXEC'] == '1',
             'GUARD16_V1_STAGED_BINARY_NOT_VERIFIED')
        helper_path = LAB / 'qdc507-guard16-v2-runtime-restore-v2.py'
        need(sha256(helper_path) == '077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55',
             'GUARD16_V2_VERIFIER_HASH_CHANGED')
        code, result, error = command(['/usr/bin/python3.8', '-I', '-B',
                                       str(helper_path), '--check'], 90, strict=False)
        need(code == 0 and all(mark in result.splitlines() for mark in
             ('READ_ONLY_RUNTIME_CHECK_OK=1', 'DEVICE_CURRENT_MODE=v2',
              'DEVICE_V1_BACKUP=present', 'DEVICE_V2_STAGE=present')),
             'GUARD16_V2_VERIFIER_FAILED_' + error[-100:])
        emit('GUARD16_DEVICE_STATE', 'PROMOTED_V2_ROLLBACK_READY')
"""

class Stop(Exception): pass

def need(value,reason):
    if not value: raise Stop(reason)

def sha(data): return hashlib.sha256(data).hexdigest()

def file_bytes(p, lower=10, upper=1000000):
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and
         not st.st_mode & 0o022 and lower <= st.st_size <= upper,
         'UNTRUSTED_FILE_'+p.name)
    return p.read_bytes()

def private_bytes(p):
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and
         not stat.S_IMODE(st.st_mode)&0o077 and st.st_nlink==1 and
         100 <= st.st_size <= 1000000,
         'BACKUP_PERMISSIONS_UNSAFE_'+p.name)
    return p.read_bytes()

def command(args, seconds=25, acceptable=False):
    try:
        p=subprocess.run([str(x) for x in args],stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         universal_newlines=True,timeout=seconds)
    except subprocess.TimeoutExpired:
        raise Stop('COMMAND_TIMEOUT_INDETERMINATE_'+str(args[0]))
    except OSError as e:
        raise Stop('COMMAND_UNAVAILABLE_'+str(args[0])+'_'+type(e).__name__)
    if not acceptable:
        need(p.returncode==0,'COMMAND_FAILED_'+str(args[0])+'_'+str(p.returncode)+
             '_'+(p.stderr or p.stdout)[-150:])
    return p

def patched_source(original):
    need(sha(original)==ORIG_GUARD_SHA,'GUARD_ORIGINAL_BYTES_NOT_PINNED')
    text=original.decode('utf-8')
    need(text.count(ORIGINAL_ANCHOR)==1,'GUARD_CHECK_ANCHOR_CHANGED')
    need('PROMOTED_V2_ROLLBACK_READY' not in text,'GUARD_ALREADY_PATCHED_IN_SOURCE')
    data=text.replace(ORIGINAL_ANCHOR,ORIGINAL_ANCHOR+EXTRA_CHECK,1).encode('utf-8')
    compile(data,str(GUARD),'exec')
    need(len(data)>len(original),'PATCH_GENERATION_FAILED')
    return data

def load_state():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    need(LAB.is_dir() and not LAB.is_symlink(),'LAB_DIR_UNTRUSTED')
    need(sha(file_bytes(ONE,10000))==ONE_SHA,'ONECLICK_SOURCE_SHA_CHANGED')
    need(sha(file_bytes(COLD,15000))==COLD_SHA,'COLDBOOT_HOOK_SHA_CHANGED')
    need(sha(file_bytes(HELPER,10000))==HELPER_SHA,'V2_HELPER_SHA_CHANGED')
    bbytes=file_bytes(BASE,100,100000)
    b=json.loads(bbytes.decode('utf-8'))
    need(b.get('format')=='qdc507-dynusb-oneclick-v1' and b.get('mode')=='16k',
         'BASELINE_FORMAT_OR_MODE_CHANGED')
    need(b.get('scripts',{}).get(ONE.name)==ONE_SHA,'ONECLICK_BASELINE_SELF_PIN_CHANGED')
    gbytes=file_bytes(GUARD,10000,100000)
    if not BACKUP_G.exists() and not BACKUP_G.is_symlink():
        need(sha(gbytes)==ORIG_GUARD_SHA,'GUARD_UNKNOWN_WITHOUT_ORIGINAL_BACKUP')
        before=gbytes
    else:
        before=private_bytes(BACKUP_G)
        need(sha(before)==ORIG_GUARD_SHA,'ORIGINAL_GUARD_BACKUP_HASH_CHANGED')
    candidate=patched_source(before)
    installed=sha(gbytes)==sha(candidate)
    original=sha(gbytes)==ORIG_GUARD_SHA
    need(installed or original,'GUARD_SCRIPT_NOT_APPROVED_ORIGINAL_OR_PATCH')
    pin=b['scripts'].get(GUARD.name)
    if original:
        need(pin==ORIG_GUARD_SHA,'ORIGINAL_GUARD_PIN_MISMATCH')
    else:
        need(pin==sha(candidate),'PATCHED_GUARD_PIN_MISMATCH')
        need(BACKUP_B.exists() and sha(private_bytes(BACKUP_B))!=sha(bbytes),
             'ORIGINAL_BASELINE_BACKUP_MISSING_OR_UNEXPECTED')
    print('CURRENT_GUARD_CHECKER='+('patched' if installed else 'original'))
    print('CURRENT_GUARD_SHA256='+sha(gbytes))
    print('EXPECTED_PATCHED_GUARD_SHA256='+sha(candidate))
    print('ONECLICK_AND_COLDBOOT_SHA_PINS_OK=1')
    for p in PENDING:
        need(not os.path.lexists(str(LAB/p)),'PENDING_RECOVERY_'+p)
    return b,bbytes,before,candidate,installed

def locks():
    held=[]
    try:
        for path in LOCKS:
            fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            held.append(fd)
            s=os.fstat(fd)
            need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and
                 not stat.S_IMODE(s.st_mode)&0o077,
                 'RECOVERY_LOCK_UNTRUSTED_'+path)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return held
    except BaseException:
        for fd in reversed(held): os.close(fd)
        raise

def private_backup(p,data):
    if p.exists() or p.is_symlink():
        need(private_bytes(p)==data,'BACKUP_EXISTS_WITH_DIFFERENT_CONTENT_'+p.name)
        return
    fd=os.open(str(p),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:
        os.fchmod(out.fileno(),0o600)
        os.fchown(out.fileno(),0,0)
        out.write(data);out.flush();os.fsync(out.fileno())
    need(private_bytes(p)==data,'PRIVATE_BACKUP_WRITE_VERIFICATION_FAILED_'+p.name)
    print('PRIVATE_BACKUP_SAVED='+p.name)

def atomic_replace(p,data):
    orig=p.lstat()
    need(stat.S_ISREG(orig.st_mode) and orig.st_uid==0,'UNTRUSTED_REPLACE_TARGET_'+p.name)
    fd,tmp=tempfile.mkstemp(prefix='.qdc507-guard-v2-atomic-',dir=str(p.parent))
    try:
        with os.fdopen(fd,'wb') as out:
            os.fchmod(out.fileno(),stat.S_IMODE(orig.st_mode))
            os.fchown(out.fileno(),orig.st_uid,orig.st_gid)
            out.write(data);out.flush();os.fsync(out.fileno())
        need(file_bytes(Path(tmp),len(data),len(data))==data,'ATOMIC_STAGE_INVALID')
        os.replace(tmp,str(p))
        dfd=os.open(str(p.parent),os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)
    need(file_bytes(p,len(data),len(data))==data,'POST_REPLACE_MISMATCH_'+p.name)

def encode_base(b):
    return (json.dumps(b,indent=2,sort_keys=True)+'\n').encode('utf-8')

def host_safe():
    need((LAB/'qdc507-mode-upgrade-c3e-preaudio-v2/mode/current').read_text().strip()=='16k',
         'PRODUCTION_MODE_NOT_16K')
    c=json.loads(command(['docker','inspect','qdc507-hardware'],15).stdout)[0]
    need(c.get('State',{}).get('Running') is True,'PRODUCTION_CONTAINER_NOT_RUNNING')
    count=command(['docker','exec','qdc507-hardware','asterisk','-rx',
                   'core show channels count'],15).stdout
    need(re.search(r'^\s*0 active channels\s*$',count,re.M) and
         re.search(r'^\s*0 active calls\s*$',count,re.M),
         'ACTIVE_CALLS_UNSAFE')
    dev=command(['docker','exec','qdc507-hardware','asterisk','-rx',
                 'quectel show device state quectel0'],15).stdout
    for k,v in (('State','Free'),('Voice','Yes'),('SMS','Yes'),('Calls/Channels','0')):
        need(bool(re.search(r'^\s*'+re.escape(k)+r'\s*:\s*'+re.escape(v)+r'\s*$',dev,re.M)),
             'MODEM_NOT_READY_'+k)
    need(command(['systemctl','is-active','qdc507-sms-tick.timer'],12).stdout.strip()=='active',
         'SMS_TIMER_NOT_ACTIVE')
    # The SMS tick is one-shot. A busy worker is not preempted; stop instead.
    p=command(['systemctl','is-active','qdc507-sms-tick.service'],12,acceptable=True)
    need(p.stdout.strip()=='inactive','SMS_WORKER_NOT_IDLE_'+p.stdout.strip())
    need(command(['docker','exec','qdc507-hardware','sha256sum',PREAUDIO],12).stdout.split()[0]==PREAUDIO_PATCH_SHA,
         'PREAUDIO_V1_V2_ALLOWLIST_NOT_INSTALLED')
    # Independent authoritative v1/v2/device validation while baseline is sealed.
    v=command(['/usr/bin/python3.8','-I','-B',str(HELPER),'--check'],70).stdout
    need(all(x in v.splitlines() for x in ('READ_ONLY_RUNTIME_CHECK_OK=1',
         'DEVICE_CURRENT_MODE=v2','DEVICE_V1_BACKUP=present','DEVICE_V2_STAGE=present')),
         'GUARD16_DEVICE_V2_READBACK_NOT_VERIFIED')
    print('PRODUCTION_16K_V2_MODEM_AND_SMS_IDLE_OK=1')

def guard_check():
    c=command(['/usr/bin/python3.8','-I','-B',str(GUARD),'--check'],105).stdout
    need('READ_ONLY_CHECK_PASSED=1' in c.splitlines() and
         'GUARD16_DEVICE_STATE=PROMOTED_V2_ROLLBACK_READY' in c.splitlines(),
         'GUARD16_READ_ONLY_V2_CHECK_NOT_PASSED')
    print('GUARD16_V2_LEGACY_CHECK_OK=1')

def install(b,bbytes,before,candidate):
    host_safe()
    private_backup(BACKUP_G,before)
    private_backup(BACKUP_B,bbytes)
    patched_hash=sha(candidate)
    b2=json.loads(bbytes.decode('utf-8'))
    b2['scripts'][GUARD.name]=patched_hash
    newbase=encode_base(b2)
    original_restored=False
    try:
        atomic_replace(GUARD,candidate)
        atomic_replace(BASE,newbase)
        print('GUARD16_V2_CHECK_AND_BASELINE_PIN_WRITTEN=1')
        # The newly sealed helper and the historical guard must BOTH accept v2.
        guard_check()
        # The installer already holds the same flock locks acquired by the
        # oneclick CLI, so spawning ONE --check would self-deadlock. Invoke
        # the *pinned* oneclick.check() in this process under our existing
        # lock set instead; this is its read-only check() function, not recover().
        import contextlib
        import io
        mod=load_oneclick()
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):
            mod.check()
        readback=capture.getvalue()
        need('READ_ONLY_CHECK_COMPLETE=1' in readback.splitlines(),
             'ONECLICK_POSTPATCH_CHECK_MARKER_MISSING')
        for line in readback.splitlines():
            print(line,flush=True)
        print('ONECLICK_READ_ONLY_POSTPATCH_OK=1')
        print('NO_DOCKER_OR_USB_RESTART=1')
        print('USB_GENERATION_NOT_ADOPTED_YET=1')
    except BaseException:
        # Best effort fail closed; if power is lost, preserve backups for review.
        try:
            if GUARD.read_bytes()==candidate: atomic_replace(GUARD,before)
            if BASE.read_bytes()==newbase: atomic_replace(BASE,bbytes)
            original_restored=(GUARD.read_bytes()==before and BASE.read_bytes()==bbytes)
        except BaseException:pass
        print('INSTALL_INTERRUPTED_ROLLBACK_COMPLETE='+str(int(original_restored)),flush=True)
        raise

def load_oneclick():
    need(sha(file_bytes(ONE,10000))==ONE_SHA,'ONECLICK_SHA_DRIFT')
    spec=importlib.util.spec_from_file_location('qdc507_pinned_oneclick_v2_adopt',str(ONE))
    need(spec is not None and spec.loader is not None,'ONECLICK_IMPORT_FAILED')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def adopt_generation(b,bbytes):
    host_safe()
    guard_check()
    mod=load_oneclick()
    c=mod.ready()                # health, Free, Voice/SMS, no calls, module hash
    shot=mod.snap()              # stable USB IF generation
    mod.check_baseline(c,shot)   # original script and image pins, no writes
    need(all(mod.remote_flags(mod.adb_device(shot)).values()),'RUNTIME_FLAGS_NOT_READY')
    need('READ_ONLY_CHECK_PASSED=1' in command(
         ['/usr/bin/python3.8','-I','-B',str(LAB/'qdc507-device-node-sync-v1.py'),'--check'],45).stdout,
         'DYNAMIC_FUNCTION_NODES_NOT_READY')
    nowboot=mod.current_boot()
    nowgen=mod.generation(shot)
    need(b.get('serial')==nowgen[3], 'USB_SERIAL_CHANGED')
    if b.get('host_boot_id')==nowboot and b.get('usb_generation')==nowgen:
        print('BASELINE_ALREADY_CURRENT_NO_WRITES=1')
        return
    # Stability/identity recheck before a one-field generation adoption.
    time.sleep(3)
    again=mod.snap()
    need(mod.generation(again)==nowgen and mod.current_boot()==nowboot,
         'USB_OR_BOOT_CHANGED_DURING_ADOPTION')
    mod.calls_idle()
    private_backup(BACKUP_ADOPT,bbytes)
    fresh=json.loads(bbytes.decode('utf-8'))
    fresh['host_boot_id']=nowboot
    fresh['usb_generation']=nowgen
    newbase=encode_base(fresh)
    try:
        atomic_replace(BASE,newbase)
        need(mod.check_baseline(c,again).get('host_boot_id')==nowboot,
             'POST_ADOPTION_BASELINE_CHECK_FAILED')
    except BaseException:
        # Do not leave a falsely adopted boot/generation when the final
        # pinned baseline recheck fails. This is NOT a recovery retry.
        if BASE.read_bytes()==newbase:
            atomic_replace(BASE,bbytes)
            print('GENERATION_ADOPTION_ROLLED_BACK=1',flush=True)
        raise
    print('MANUALLY_RECOVERED_HEALTHY_GENERATION_ADOPTED=1')
    print('THIS_IS_NOT_A_PASSING_COLD_BOOT_TEST=1')
    print('NO_USB_OR_CONTAINER_RESTART=1')

def rollback(b,bbytes,before,candidate):
    # Refuse restoring a v1-only checker while v2 remains the live bridge.
    snapshot=command(['/usr/bin/python3.8','-I','-B',str(HELPER),'--check'],65).stdout
    need('DEVICE_CURRENT_MODE=v1' in snapshot.splitlines(),
         'RESTORE_DEVICE_V1_BEFORE_ROLLING_BACK_V2_CHECKER')
    need(sha(file_bytes(GUARD,10000))==sha(candidate),'PATCHED_GUARD_NOT_INSTALLED')
    restored=json.loads(bbytes.decode('utf-8'))
    restored['scripts'][GUARD.name]=ORIG_GUARD_SHA
    atomic_replace(GUARD,before)
    atomic_replace(BASE,encode_base(restored))
    print('GUARD16_V1_CHECKER_AND_PIN_RESTORED=1')
    print('CURRENT_USB_GENERATION_PRESERVED=1')

def main():
    need(len(sys.argv)==2 and sys.argv[1] in
         ('--check','--install','--adopt-generation','--rollback'),
         'USAGE_CHECK_INSTALL_ADOPT-GENERATION_ROLLBACK')
    print('QDC507_GUARD16_V2_RECOVERY_PIN_FIX_REV=2')
    act=sys.argv[1]
    held=locks() if act!='--check' else []
    try:
        b,bbytes,before,candidate,installed=load_state()
        if act=='--check':
            print('READ_ONLY_GUARD_PIN_PRECHECK_OK=1')
        elif act=='--install':
            if installed:
                guard_check()
                print('ALREADY_INSTALLED_NO_WRITES=1')
            else:
                install(b,bbytes,before,candidate)
                print('GUARD16_V2_RECOVERY_PIN_FIX_INSTALLED=1')
        elif act=='--adopt-generation':
            need(installed,'INSTALL_GUARD_V2_COMPAT_FIRST')
            adopt_generation(b,bbytes)
        else:
            need(installed,'NOT_INSTALLED_NO_ROLLBACK_NEEDED')
            rollback(b,bbytes,before,candidate)
    finally:
        for fd in reversed(held):os.close(fd)

if __name__=='__main__':
    try:main()
    except (Stop,OSError,ValueError,KeyError,SyntaxError,BlockingIOError) as e:
        print('GUARD16_V2_RECOVERY_PIN_FIX_STOP='+type(e).__name__+':'+str(e)[:300],
              file=sys.stderr,flush=True)
        sys.exit(2)
