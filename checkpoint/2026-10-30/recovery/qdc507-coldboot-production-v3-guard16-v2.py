#!/usr/bin/python3.8
"""QDC507 production cold-start recovery v3 for DS918+/DSM 7.1 (pending reboot validation).
--check: read only; --boot: one bounded recovery, no auto-retry.
This script runs on NAS HOST; use Synology Task Scheduler, boot-up, root.
Does not recreate the formal container, unload kernel modules, or change baselines.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
import tempfile
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

os.umask(0o077)
os.environ['PATH'] = ('/volume2/@appstore/Docker/usr/bin:/usr/local/bin:'
                      '/usr/local/sbin:/usr/syno/bin:/usr/syno/sbin:'
                      '/usr/sbin:/usr/bin:/sbin:/bin')

PROD = Path('/volume2/docker/asterisk/qdc507-current/recovery')
LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
ONE = LAB / 'qdc507-dynusb-oneclick-v2.py'
IF = LAB / 'qdc507-if-role-manager-v1.py'
NODES = LAB / 'qdc507-device-node-sync-v1.py'
TIMER = 'qdc507-sms-tick.timer'
WORKER = 'qdc507-sms-tick.service'
NAME = 'qdc507-hardware'
EXPECTED_ID = 'cd1f8d1ccc3d3cd465cfefc1c87cc8521cb9979c95333d0f4e60b6f829115fd4'
EXPECTED_OPTION = '395918cff6c00e82cd8fbdfd99b8b2420ac9089605f6df0cc8a80550fd6d98cc'
EXPECTED_ONE = 'd3817c52f19e6f50d03605ee4e478c07e022b442091f0b90d64f52d6a2a4160c'
HOST_BOOT = Path('/usr/local/sbin/qdc507-boot-prep.py')
HOST_BIND = Path('/usr/local/libexec/qdc507/boot/qdc507-bind-if2.sh')
OPTION = Path('/usr/local/libexec/qdc507/boot/option.ko')
STAGE = LAB / 'qdc507-dsm71-option-install-stage-v1/manifest.json'
BASE = LAB / 'qdc507-dynusb-oneclick-baseline-v1.json'
GUARD = LAB / 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
ADB_PREP = Path('/usr/local/sbin/qdc507-boot-adb.py')
ADB = Path('/usr/local/libexec/qdc507/adb')
ADB_PIN = 'a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01'
ADB_HOME = Path('/tmp/qdc507-adb-home')
ADB_VENDOR_ID = '0x2ca3'
ADB_ENV = {
    'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
    'HOME': str(ADB_HOME),
    'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:5037',
}


class Halt(Exception):
    pass

def require(ok, why):
    if not ok:
        raise Halt(why)

def digest(p):
    s = p.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
            not s.st_mode & 0o022, 'UNSAFE_FILE_' + str(p))
    return hashlib.sha256(p.read_bytes()).hexdigest()

def command(args, timeout=35, allow_failure=False):
    a = list(map(str, args))
    try:
        r = subprocess.run(a, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, encoding='utf-8',
                           errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Halt('COMMAND_TIMEOUT_OR_UNAVAILABLE_' + str(a[0]) + '_' + type(e).__name__)
    if r.stdout.strip():
        print(r.stdout.rstrip(), flush=True)
    if not allow_failure:
        require(r.returncode == 0, 'COMMAND_FAILED_' + str(a[:2]) + '_RC' + str(r.returncode))
    return r

def wait_docker():
    print('WAIT_DOCKER_MAX_SECONDS=300', flush=True)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(['docker','info','--format','{{.ServerVersion}}'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                print('DOCKER_READY=1', flush=True)
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(3)
    raise Halt('DOCKER_NOT_READY_AFTER_300S')

def prepare_inputs():
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    require(os.uname().release == '4.4.180+', 'KERNEL_CHANGED')
    require(digest(OPTION) == EXPECTED_OPTION, 'OPTION_DRIVER_HASH_CHANGED')
    require(digest(ONE) == EXPECTED_ONE, 'ONECLICK_40S_VERSION_CHANGED')
    digest(STAGE)
    manifest = json.loads(STAGE.read_text())
    for key, path in [('option.ko',OPTION),
                      ('qdc507-bind-if2.sh',HOST_BIND),
                      ('qdc507-boot-prep.py',HOST_BOOT)]:
        require(digest(path) == manifest['files'][key]['candidate_sha256'],
                'BOOT_FILE_HASH_CHANGED_' + key)
    digest(BASE)
    b = json.loads(BASE.read_text())
    require(b.get('format') == 'qdc507-dynusb-oneclick-v1' and
            b.get('container_id') == EXPECTED_ID and b.get('mode') == '16k',
            'RECOVERY_BASELINE_CHANGED')
    for p in (ONE, IF, NODES, GUARD, ADB_PREP):
        require(digest(p) == b.get('scripts', {}).get(p.name),
                'RECOVERY_SCRIPT_PIN_CHANGED_' + p.name)
    spec = importlib.util.spec_from_file_location('qdc507_coldboot_oneclick', str(ONE))
    require(spec is not None and spec.loader is not None, 'ONECLICK_IMPORT_FAILED')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    require(not m.PENDING.exists() and not m.PENDING.is_symlink(),
            'OLD_RECOVERY_PENDING_REVIEW_REQUIRED')
    for pending in ('qdc507-usb-recovery-v4-pending.json',
                    'qdc507-dynusb-mode-switch-pending.json',
                    'qdc507-dynusb-cutover-pending-v1.json'):
        require(not (LAB/pending).exists(), 'OTHER_RECOVERY_PENDING_'+pending)
    c = m.formal()  # validates exact image, ID, mounts, permissions, mode
    require(c['Id'] == EXPECTED_ID, 'CONTAINER_REPLACED')
    return m, c, b

def _secure_private_dir(path):
    if not path.exists():
        path.mkdir(mode=0o700)
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and
            not (st.st_mode & 0o077), 'UNSAFE_ADB_HOME_' + str(path))


def ensure_vendor_config(create):
    """ADB uses a volatile HOME. Restore one vendor ID before starting/scanning."""
    require(digest(ADB) == ADB_PIN, 'ADB_BINARY_CHANGED')
    require(not ADB_HOME.is_symlink(), 'ADB_HOME_SYMLINK')
    if create:
        _secure_private_dir(ADB_HOME)
    else:
        if not ADB_HOME.is_dir():
            print('ADB_VENDOR_CONFIG_PRESENT=0', flush=True)
            return False
    android = ADB_HOME / '.android'
    if create:
        _secure_private_dir(android)
    else:
        if not android.is_dir():
            print('ADB_VENDOR_CONFIG_PRESENT=0', flush=True)
            return False
        require(not android.is_symlink(), 'ADB_ANDROID_CONFIG_DIR_SYMLINK')
    ini = android / 'adb_usb.ini'
    if ini.exists() or ini.is_symlink():
        st = ini.lstat()
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
                not (st.st_mode & 0o077) and st.st_nlink == 1,
                'ADB_VENDOR_FILE_UNSAFE')
        data = ini.read_text(encoding='ascii')
    else:
        data = ''
    entries = [line.split('#',1)[0].strip().lower() for line in data.splitlines()]
    if ADB_VENDOR_ID in entries:
        print('ADB_VENDOR_ID_CONFIG_READY=1', flush=True)
        return True
    if not create:
        print('ADB_VENDOR_CONFIG_PRESENT=0', flush=True)
        return False
    require(len(data) < 32768, 'ADB_VENDOR_LIST_TOO_LARGE')
    updated = data + ('' if not data or data.endswith('\n') else '\n') + ADB_VENDOR_ID + '\n'
    fd, tmp = tempfile.mkstemp(prefix='.qdc507-adb-vendor-', dir=str(android))
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(ini))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print('ADB_VENDOR_ID_PERSISTED_FOR_THIS_BOOT=1', flush=True)
    return True


def adb_pids():
    """Enumerate only the expected root-owned ADB server, refuse ambiguity."""
    found=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            args=(p/'cmdline').read_bytes().split(b'\0')
            if args[:6] != [str(ADB).encode(), b'-a', b'-P', b'5037',
                            b'nodaemon', b'server']:
                continue
            st=p.stat()
            require(st.st_uid==0, 'UNTRUSTED_ADB_PROCESS_OWNER')
            found.append(int(p.name))
        except (FileNotFoundError,ProcessLookupError):
            continue
    require(len(found)<=1, 'MULTIPLE_ADB_SERVERS')
    return found


def _adb_cmd(args, timeout=12):
    return subprocess.run(list(map(str,args)), env=ADB_ENV,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=timeout)


def ensure_adb_online(m, shot, baseline, running):
    """ADB READY means root shell on this USB serial, not just listening 5037."""
    require(m.generation(shot)[3].lower()==baseline['serial'].lower(),
            'ADB_USB_SERIAL_CHANGED')
    ensure_vendor_config(create=True)
    command(['/usr/bin/python3.8', '-I', '-B', ADB_PREP], 85)
    try:
        serial=m.adb_device(shot)
        print('ADB_ROOT_ONLINE=1', flush=True)
        return serial
    except m.Stop:
        print('ADB_NOT_ONLINE_REFRESH_NEEDED=1', flush=True)

    # After a reboot, the old process can survive the USB re-enumeration
    # while having no QDC transport. Refresh it exactly once when idle.
    if running:
        m.calls_idle()
    require(m.snap()['generation']==shot['generation'],
            'USB_CHANGED_BEFORE_ADB_REFRESH')
    pids=adb_pids()
    require(len(pids)==1, 'ADB_SERVER_NOT_UNIQUELY_IDENTIFIED')
    print('ADB_REFRESH_ONCE=1', flush=True)
    kill=_adb_cmd([ADB, '-H','127.0.0.1','-P','5037','kill-server'], 15)
    require(kill.returncode==0, 'ADB_KILL_SERVER_FAILED')
    deadline=time.monotonic()+12
    while adb_pids() and time.monotonic()<deadline:
        time.sleep(.25)
    require(not adb_pids(), 'OLD_ADB_PROCESS_STILL_ACTIVE')
    require(m.snap()['generation']==shot['generation'],
            'USB_CHANGED_DURING_ADB_REFRESH')
    d=Path('/var/lib/qdc507-boot-adb-server.log')
    with d.open('ab') as f:
        env=dict(ADB_ENV)
        env.pop('ADB_SERVER_SOCKET', None)
        subprocess.Popen([str(ADB),'-a','-P','5037','nodaemon','server'],
                         env=env,stdin=subprocess.DEVNULL,stdout=f,
                         stderr=subprocess.STDOUT,start_new_session=True)
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        require(m.snap()['generation']==shot['generation'],
                'USB_CHANGED_AFTER_ADB_REFRESH')
        try:
            serial=m.adb_device(shot)
            print('ADB_ROOT_ONLINE_AFTER_REFRESH=1', flush=True)
            return serial
        except m.Stop:
            time.sleep(1)
    raise Halt('ADB_NOT_ONLINE_AFTER_ONE_BOUNDED_REFRESH')


def restore_sms_timer_on_failure():
    """Best effort: do not leave the SMS timer off after a failed recovery."""
    try:
        p=command(['systemctl','is-active',TIMER],10,True)
        if p.stdout.strip()!='active':
            command(['systemctl','start',TIMER],35)
            require(command(['systemctl','is-active',TIMER],10).stdout.strip()=='active',
                    'SMS_TIMER_RESTORE_UNCONFIRMED')
        print('SMS_TIMER_POSTFAIL_ACTIVE=1', flush=True)
    except Exception as e:
        print('SMS_TIMER_POSTFAIL_RESTORE_FAILED='+type(e).__name__+':'+str(e)[:180],flush=True)


def worker_idle():
    end = time.monotonic() + 30
    while time.monotonic() < end:
        r = subprocess.run(['systemctl', 'is-active', WORKER], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, timeout=10)
        if r.stdout.strip() == 'inactive':
            print('SMS_WORKER_IDLE=1', flush=True)
            return
        time.sleep(2)
    raise Halt('SMS_WORKER_NOT_IDLE')

def verify_healthy(m, b):
    c = m.ready()
    shot = m.snap()
    m.check_baseline(c, shot)
    require(all(m.remote_flags(m.adb_device(shot)).values()), 'DEVICE_VOICE_FLAGS_INCOMPLETE')
    r = command(['/usr/bin/python3.8','-I','-B', NODES, '--check'], 35)
    require('READ_ONLY_CHECK_PASSED=1' in r.stdout, 'DEVICE_NODES_NOT_VERIFIED')
    t = command(['systemctl','is-active',TIMER], 10)
    require(t.stdout.strip() == 'active', 'SMS_TIMER_INACTIVE')
    return shot

def check(m, c, b):
    require(c['State']['Running'], 'FORMAL_CONTAINER_NOT_RUNNING')
    require(m.usb_enum_snapshot(b['serial']) is not None, 'USB_DEVICE_MISSING')
    ensure_vendor_config(create=False)
    verify_healthy(m,b)
    print('COLD_BOOT_READ_ONLY_PREFLIGHT_OK=1', flush=True)

def boot(m, c, b):
    print('COLD_BOOT_RECOVERY_START=1', flush=True)
    if c['State']['Running']:
        m.calls_idle()
    m.wait_for_usb_enumeration()  # 40s max, stable 3s, same serial + descriptors
    ensure_vendor_config(create=True)  # /tmp is recreated at every NAS reboot
    if c['State']['Running'] and b['host_boot_id'] == m.current_boot():
        try:
            shot = m.snap()
            if m.generation(shot) == b['usb_generation']:
                verify_healthy(m,b)
                print('ALREADY_HEALTHY_NO_ACTION=1', flush=True)
                return
        except (Halt, m.Stop, OSError, subprocess.TimeoutExpired):
            pass
    timer_transition_started=False
    try:
        print('STEP_STOP_SMS_TIMER=1', flush=True)
        timer_transition_started=True
        command(['systemctl','stop',TIMER], 35)
        worker_idle()
        if c['State']['Running']:
            m.calls_idle()
        print('STEP_PREPARE_HOST_USB=1', flush=True)
        h = command(['/usr/bin/python3.8','-I','-B', IF, '--prepare'], 130)
        require('HOST_ALREADY_READY_NO_WRITES=1' in h.stdout or
                'IF0_IF8_HOST_RECOVERY_COMPLETE=1' in h.stdout,
                'USB_PREPARE_NOT_VERIFIED')
        shot = m.snap()
        m.same_serial(shot,b)
        m.check_baseline(c,shot)
        print('STEP_SYNC_DYNAMIC_NODES=1', flush=True)
        n = command(['/usr/bin/python3.8','-I','-B', NODES, '--sync'], 80)
        require('ALREADY_SYNCHRONIZED_NO_WRITES=1' in n.stdout or
                'FUNCTION_NODES_SYNCHRONIZED=1' in n.stdout,
                'NODE_SYNC_NOT_VERIFIED')
        require('READ_ONLY_CHECK_PASSED=1' in command(
            ['/usr/bin/python3.8','-I','-B', NODES, '--check'],35).stdout,
            'DEVICE_NODE_POSTCHECK_FAILED')
        print('STEP_VALIDATE_QDC507_ADB_ONLINE=1', flush=True)
        serial=ensure_adb_online(m,shot,b,c['State']['Running'])
        if not c['State']['Running']:
            print('STEP_RESTORE_DEVICE_SIDE_VOICE=1', flush=True)
            f=m.remote_flags(serial)
            require(f['audio_idle'] and f['qdc507_aprv3']==f['qdc507_voice'],
                    'PARTIAL_OR_BUSY_DEVICE_VOICE_STATE')
            if not f['qdc507_aprv3']:
                m.runtime_sources()
                m.prepare_voice(serial,f)  # only with original container stopped
            else:
                require(f['helper'] and f['calibrated'],
                        'LOADED_VOICE_RUNTIME_INCOMPLETE')
            print('STEP_START_EXISTING_CONTAINER=1', flush=True)
            command(['docker','start',NAME], 100)
            deadline=time.monotonic()+100
            while time.monotonic()<deadline:
                r=command(['docker','exec',NAME,'asterisk','-rx',
                           'core show channels count'],12,True)
                if r.returncode==0 and '0 active channels' in r.stdout:
                    break
                time.sleep(3)
            else:
                raise Halt('ASTERISK_DID_NOT_RESPOND')
        else:
            print('FORMAL_CONTAINER_ALREADY_RUNNING=1', flush=True)
        # The audited oneclick requires timer active on entry and stops it itself.
        print('STEP_RESTART_SMS_TIMER=1', flush=True)
        command(['systemctl','start',TIMER], 35)
        print('STEP_RUN_EXISTING_ONECLICK=1', flush=True)
        r=command(['/usr/bin/python3.8','-I','-B',ONE,'--recover'], 590)
        require('DYNUSB_ONECLICK_RECOVERY_OK=1' in r.stdout or
                'ALREADY_HEALTHY_NO_ACTION=1' in r.stdout,
                'ONECLICK_RECOVERY_RESULT_UNVERIFIED')
        shot=verify_healthy(m,b)
        updated=json.loads(BASE.read_text())
        require(updated.get('host_boot_id') == m.current_boot() and
                updated.get('usb_generation') == m.generation(shot),
                'BASELINE_NOT_UPDATED_AFTER_COLD_BOOT')
        print('COLD_BOOT_RECOVERY_OK=1', flush=True)
        print('SAME_CONTAINER_PRESERVED=1', flush=True)
        print('SMS_TIMER=active', flush=True)
    except BaseException:
        if timer_transition_started:
            restore_sms_timer_on_failure()
        raise

def main():
    require(sys.argv[1:] in (['--check'], ['--boot']), 'USE_--check_OR_--boot')
    require(os.geteuid()==0, 'ROOT_REQUIRED')
    require(PROD.is_dir() and not PROD.is_symlink(), 'PRODUCTION_RECOVERY_DIR_INVALID')
    fd=os.open('/run/qdc507-coldboot-v2.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    require(stat.S_ISREG(st.st_mode) and st.st_uid==0 and
            not st.st_mode & 0o077, 'LOCK_FILE_UNSAFE')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    coord=os.open('/run/qdc507-hotplug-coordinator.lock',
                  os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    cs=os.fstat(coord)
    require(stat.S_ISREG(cs.st_mode) and cs.st_uid==0 and
            not cs.st_mode & 0o077, 'COORDINATOR_LOCK_UNSAFE')
    fcntl.flock(coord,fcntl.LOCK_EX|fcntl.LOCK_NB)
    log=None
    original_stdout=sys.stdout
    try:
        if sys.argv[1]=='--boot':
            logs=PROD/'logs'
            if not logs.exists():
                logs.mkdir(mode=0o700)
            st=logs.lstat()
            require(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and
                    not st.st_mode & 0o077, 'PRODUCTION_LOG_DIR_UNSAFE')
            p=logs/('coldboot-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())+'.log')
            log=p.open('x')
            class Tee:
                def write(self, v):
                    original_stdout.write(v); log.write(v)
                def flush(self):
                    original_stdout.flush(); log.flush()
            sys.stdout=Tee()
            print('LOG='+str(p),flush=True)
            wait_docker()
        m,c,b=prepare_inputs()
        if sys.argv[1]=='--check':
            check(m,c,b)
        else:
            boot(m,c,b)
        return 0
    except Exception as e:
        print('COLD_BOOT_STOPPED='+type(e).__name__+':'+str(e)[:250],flush=True)
        print('NO_AUTOMATIC_RETRY=1',flush=True)
        return 2
    finally:
        if log:
            sys.stdout=original_stdout
            log.close()
        os.close(coord)
        os.close(fd)

# QDC507_GUARD16_V2_AFTER_COLDBOOT_V1_BEGIN
# v2 POST-boot verification. Do not move this inside boot(): locks must be gone.
def _qdc507_guard16v2_after_boot():
    import hashlib as _gv_hash
    import os as _gv_os
    import pathlib as _gv_path
    import stat as _gv_stat
    import subprocess as _gv_sub
    import time as _gv_time

    _helper = _gv_path.Path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-guard16-v2-runtime-restore-v2.py')
    _expected = '077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55'
    _s = _helper.lstat()
    if not (_gv_stat.S_ISREG(_s.st_mode) and _s.st_uid == 0 and
            not (_s.st_mode & 0o022) and 5000 <= _s.st_size <= 1000000):
        raise RuntimeError('V2_HELPER_UNTRUSTED')
    if _gv_hash.sha256(_helper.read_bytes()).hexdigest() != _expected:
        raise RuntimeError('V2_HELPER_SHA256_CHANGED')

    _log_dir = _gv_path.Path('/volume2/docker/asterisk/qdc507-current/recovery/logs')
    _ls = _log_dir.lstat()
    if not (_gv_stat.S_ISDIR(_ls.st_mode) and _ls.st_uid == 0 and
            not (_ls.st_mode & 0o077)):
        raise RuntimeError('V2_POST_BOOT_LOG_DIR_UNTRUSTED')
    _log_path = _log_dir / ('guard16v2-postboot-' +
                             _gv_time.strftime('%Y%m%d-%H%M%S') + '-' +
                             str(_gv_os.getpid()) + '.log')
    _fd = _gv_os.open(str(_log_path), _gv_os.O_WRONLY | _gv_os.O_CREAT |
                      _gv_os.O_EXCL | _gv_os.O_NOFOLLOW, 0o600)
    print('GUARD16_V2_POSTBOOT_LOG=' + str(_log_path), flush=True)
    with _gv_os.fdopen(_fd, 'w', buffering=1) as _log:
        def _run(_mode, _timeout):
            _p = _gv_sub.run(['/usr/bin/python3.8', '-I', '-B', str(_helper), _mode],
                             stdin=_gv_sub.DEVNULL, stdout=_gv_sub.PIPE,
                             stderr=_gv_sub.STDOUT, universal_newlines=True,
                             timeout=_timeout)
            _log.write('COMMAND=' + _mode + ' RC=' + str(_p.returncode) + '\n')
            _log.write(_p.stdout + ('\n' if _p.stdout and not _p.stdout.endswith('\n') else ''))
            _log.flush()
            if _p.returncode:
                raise RuntimeError('V2_' + _mode + '_FAILED_RC' + str(_p.returncode))
            return [s.strip() for s in _p.stdout.splitlines()]

        print('GUARD16_V2_POSTBOOT_ENSURE_START=1', flush=True)
        _result = _run('--ensure-v2', 240)
        if not any(x in _result for x in
                   ('ALREADY_V2_NO_WRITES=1', 'GUARD16_V2_RUNTIME_RESTORED=1')):
            raise RuntimeError('V2_ENSURE_SUCCESS_MARKER_MISSING')
        _check = _run('--check', 100)
        if not all(x in _check for x in
                   ('READ_ONLY_RUNTIME_CHECK_OK=1', 'DEVICE_CURRENT_MODE=v2',
                    'DEVICE_V2_STAGE=present', 'DEVICE_V1_BACKUP=present')):
            raise RuntimeError('V2_POST_BOOT_READBACK_FAILED')
        _log.write('COLD_BOOT_AND_GUARD16_V2_VERIFIED=1\n')
        print('COLD_BOOT_AND_GUARD16_V2_VERIFIED=1', flush=True)


if __name__=='__main__':
    _qdc507_rc = main()  # Original main releases both locks in its finally.
    if _qdc507_rc == 0 and sys.argv[1:] == ['--boot']:
        try:
            _qdc507_guard16v2_after_boot()
        except Exception as _qdc507_v2_error:
            print('GUARD16_V2_POST_BOOT_STOP=' + type(_qdc507_v2_error).__name__ + ':' +
                  str(_qdc507_v2_error)[:260], flush=True)
            print('ORIGINAL_COLDBOOT_COMPLETE_BUT_V2_NOT_VERIFIED=1', flush=True)
            print('NO_AUTOMATIC_RETRY=1', flush=True)
            _qdc507_rc = 2
    sys.exit(_qdc507_rc)
# QDC507_GUARD16_V2_AFTER_COLDBOOT_V1_END
