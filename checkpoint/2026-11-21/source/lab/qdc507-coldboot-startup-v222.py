#!/usr/bin/python3.8
"""Staged QDC507 V222: one coldboot -> OneClick + Guard16 V2 business gate.

--selftest: local syntax / interface self-check; never touches NAS services.
--check: read-only production validation (running container required).
--boot: one bounded, guarded recovery; no automatic retries.

This wrapper reuses the SHA-pinned V221 ``main()`` via runpy with a non-main
module name, deliberately bypassing V221's *separate* postboot Guard16 hook.
V221 main includes the original OneClick. Then this wrapper, after V221 has
released its coldboot/hotplug locks, safely synchronizes the Guard16 V2 boot
context, ensures V2, verifies the final state and starts the existing watcher.
The original V221, OneClick and hotplug implementations are NOT modified.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import time

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
CORE = LAB / 'qdc507-coldboot-memoryfix-v221.py'
ONE = LAB / 'qdc507-dynusb-oneclick-memoryfix-v22.py'
GUARD = LAB / 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
V2 = LAB / 'qdc507-guard16-runtime-memoryfix-v2.py'
WATCH = LAB / 'qdc507-adb-watch-alert-memoryfix-v2.py'
AUDIT = LAB / 'qdc507-memoryfix-v22-audit.py'
BASE22 = LAB / 'qdc507-dynusb-memoryfix-v22-baseline.json'
BASE2 = LAB / 'qdc507-dynusb-memoryfix-v2-baseline.json'
PYTHON = '/usr/bin/python3.8'
CONTAINER = 'qdc507-hardware'
OLD_WATCH = LAB / 'qdc507-adb-watch-alert-v2.1.py'
WATCH_MARKER = 'WATCH_V2_3_AUTO_RECOVERY_STARTED'
PINS = {
    CORE: '87f8e033b95a09e13f2a701a18ce6c66931dce342c028b1639da10f20f5afc55',
    ONE: '68ef9ef26f84e91d012a32bbe4a23df3674a121a1e835189c6f145b2a4712185',
    GUARD: '9e9398a77e39d711b0009dbc5938a18c1f9387925f3c6541ae215331d9a9b730',
    V2: 'f7f076f9944c0ef85020785b1b308c98597fd55cb1035ab177447bc239bd6b12',
}
PENDING = (
    'qdc507-dynusb-oneclick-pending-v1.json',
    'qdc507-dynusb-mode-switch-pending.json',
    'qdc507-usb-recovery-v4-pending.json',
    'qdc507-dynusb-cutover-pending-v1.json',
)
SYNC_LOCKS = (
    '/run/qdc507-hotplug-coordinator.lock',
    '/run/qdc507-dynusb-oneclick.lock',
    '/run/qdc507-dynusb-layout.lock',
    '/run/qdc507-if-role-manager.lock',
    '/var/run/qdc-dsm-registry-sync.lock',
    '/run/qdc507-v4-usb.lock',
    '/run/qdc507-v4-boot.lock',
)


class Stop(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Stop(reason)


def trusted_bytes(path, private=False, minimum=1, maximum=1000000):
    s = path.lstat()
    forbid = 0o077 if private else 0o022
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and
         not stat.S_IMODE(s.st_mode) & forbid and minimum <= s.st_size <= maximum,
         'FILE_UNTRUSTED_' + path.name)
    return path.read_bytes()


def pinned_sources():
    need(os.geteuid() == 0 and LAB.is_dir() and not LAB.is_symlink(),
         'ROOT_OR_LAB_INVALID')
    for p, expected in PINS.items():
        need(hashlib.sha256(trusted_bytes(p, minimum=1000)).hexdigest() == expected,
             'SHA_CHANGED_' + p.name)
    # The public checkpoint's watcher file was sanitized, so its public
    # checksum is NOT a production runtime pin. Validate trust and behavior.
    trusted_bytes(WATCH, minimum=1000)
    need(AUDIT.is_file() and not AUDIT.is_symlink(), 'AUDIT_NOT_FOUND')
    # An explicit invariant: V221 has a separate postboot hook which must NOT run.
    source = CORE.read_text()
    need('def _qdc507_guard16v2_after_boot():' in source and
         "if __name__=='__main__':" in source,
         'V221_ENTRY_HOOK_STRUCTURE_CHANGED')


def run(args, timeout=100):
    try:
        p = subprocess.run(list(map(str, args)), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Stop('COMMAND_UNAVAILABLE_OR_TIMEOUT_' + str(args[0]) + '_' +
                   type(exc).__name__)
    if p.stdout:
        print(p.stdout, end='' if p.stdout.endswith('\n') else '\n', flush=True)
    need(p.returncode == 0, 'COMMAND_FAILED_' + str(args[-2:]) +
         '_RC' + str(p.returncode))
    return p.stdout


def read_baseline(path):
    raw = trusted_bytes(path, private=True, minimum=100, maximum=100000)
    d = json.loads(raw.decode('utf-8'))
    need(isinstance(d, dict), 'BAD_BASELINE_' + path.name)
    return raw, d


def validate_pair(old, latest, current_boot, current_generation, live_container):
    """Pure safety gate; returns a new dictionary with only two fields updated."""
    for key in ('format', 'mode', 'serial', 'container_id', 'image_id'):
        need(old.get(key) == latest.get(key), 'CONTEXT_MISMATCH_' + key)
    need(latest.get('format') == 'qdc507-dynusb-oneclick-v1' and
         latest.get('mode') == '16k', 'BAD_BASELINE_MODE')
    need(latest.get('host_boot_id') == current_boot and
         latest.get('usb_generation') == current_generation,
         'V22_CURRENT_BOOT_NOT_SEALED')
    need(live_container.get('Id') == latest.get('container_id') and
         live_container.get('Image') == latest.get('image_id') and
         live_container.get('State', {}).get('Running') is True,
         'LIVE_CONTAINER_CONTEXT_CHANGED')
    for record in (old, latest):
        need(isinstance(record.get('scripts'), dict) and
             record['scripts'].get(GUARD.name) == PINS[GUARD],
             'GUARD_SCRIPT_PIN_MISMATCH')
    need(isinstance(current_generation, list) and len(current_generation) == 4,
         'USB_GENERATION_INVALID')
    result = dict(old)
    result['host_boot_id'] = current_boot
    result['usb_generation'] = current_generation
    return result


def load_oneclick():
    sp = importlib.util.spec_from_file_location('qdc507_v222_oneclick_reader', str(ONE))
    need(sp is not None and sp.loader is not None, 'ONECLICK_IMPORT_FAILED')
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def current_snapshot(m):
    b22raw, latest = read_baseline(BASE22)
    b2raw, old = read_baseline(BASE2)
    c = m.formal()
    shot = m.snap()
    generation = m.generation(shot)
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    need(boot_id and len(boot_id) == 36, 'BOOT_ID_INVALID')
    m.same_serial(shot, latest)
    update = validate_pair(old, latest, boot_id, generation, c)
    return b2raw, old, b22raw, latest, update, generation, boot_id


def check_pending():
    for name in set(PENDING):
        need(not os.path.lexists(str(LAB / name)),
             'PENDING_RECOVERY_REQUIRES_REVIEW_' + name)


def acquire_lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    st = os.fstat(fd)
    need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
         not stat.S_IMODE(st.st_mode) & 0o077, 'LOCK_UNTRUSTED_' + path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise Stop('RECOVERY_LOCK_BUSY_' + path)
    return fd


def store_updated_baseline(old_raw, updated):
    """Back up in LAB, then CAS + atomic replacement. No system-dir writes."""
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
    backup = LAB / ('qdc507-dynusb-memoryfix-v2-before-coldboot-v222-' + stamp + '.json')
    fd = os.open(str(backup), os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(old_raw)
        f.flush()
        os.fsync(f.fileno())
    payload = (json.dumps(updated, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    temp = LAB / ('.qdc507-guard16-v222-' + stamp + '.tmp')
    fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        need(BASE2.read_bytes() == old_raw, 'BASELINE_CHANGED_CONCURRENTLY')
        os.replace(str(temp), str(BASE2))
    finally:
        if temp.exists():
            temp.unlink()
    need(BASE2.read_bytes() == payload, 'BASELINE_SYNC_VERIFY_FAILED')
    print('GUARD16_V2_BASELINE_BACKUP=' + str(backup), flush=True)
    print('GUARD16_V2_COLD_BOOT_CONTEXT_SYNCED=1', flush=True)


def synchronize_guard16_context(m):
    locks = []
    try:
        for path in SYNC_LOCKS:
            locks.append(acquire_lock(path))
        check_pending()
        need(run(['systemctl', 'is-active', 'qdc507-sms-tick.timer'], 15).strip()
             == 'active', 'SMS_TIMER_NOT_ACTIVE')
        # Do not modify device-side state while a phone call is in progress.
        m.calls_idle()
        raw, old, latest_raw, latest, update, gen, boot = current_snapshot(m)
        # Keep filesystem and USB generations stable immediately before commit.
        if old.get('host_boot_id') == boot and old.get('usb_generation') == gen:
            print('GUARD16_V2_COLD_BOOT_CONTEXT_ALREADY_CURRENT=1', flush=True)
            return
        need(BASE22.read_bytes() == latest_raw, 'V22_BASELINE_CHANGED_CONCURRENTLY')
        shot = m.snap()
        need(m.generation(shot) == gen, 'USB_CHANGED_DURING_GUARD_SYNC')
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot,
             'HOST_BOOT_CHANGED_DURING_GUARD_SYNC')
        store_updated_baseline(raw, update)
    finally:
        for fd in reversed(locks):
            os.close(fd)


def verify_guard16_v2():
    lines = run([PYTHON, '-I', '-B', str(V2), '--check'], 110).splitlines()
    required = ('READ_ONLY_RUNTIME_CHECK_OK=1', 'DEVICE_CURRENT_MODE=v2',
                'DEVICE_V2_STAGE=present', 'DEVICE_V1_BACKUP=present')
    need(all(x in lines for x in required), 'GUARD16_V2_FINAL_STATE_UNVERIFIED')


def business_recovery():
    """OneClick already completed inside pinned V221; finish one business gate."""
    print('V222_ONECLICK_GUARD16_BUSINESS_BEGIN=1', flush=True)
    m = load_oneclick()
    synchronize_guard16_context(m)  # locks released before Guard16 helper locks
    print('V222_GUARD16_V2_ENSURE_START=1', flush=True)
    results = run([PYTHON, '-I', '-B', str(V2), '--ensure-v2'], 260).splitlines()
    need(any(x in results for x in ('ALREADY_V2_NO_WRITES=1',
                                    'GUARD16_V2_RUNTIME_RESTORED=1')),
         'GUARD16_ENSURE_MARKER_MISSING')
    verify_guard16_v2()
    out = run([PYTHON, '-I', '-B', str(ONE), '--check'], 100)
    markers = (
        'READ_ONLY_CHECK_COMPLETE=1', 'FUNCTION_NODES_READY=1',
        'DEVICE_QDC507_APRV3=1', 'DEVICE_QDC507_VOICE=1',
        'DEVICE_HELPER=1', 'DEVICE_CALIBRATED=1',
        'DEVICE_GUARD=1', 'DEVICE_PREAUDIO=1',
        'DEVICE_AUDIO_IDLE=1', 'USB_GENERATION_CHANGED=0',
        'FORMAL_RUNNING=1', 'ASTERISK_QUectel_QUERY_OK=1',
    )
    need(all(marker in out.splitlines() for marker in markers),
         'ONECLICK_FINAL_READONLY_CHECK_FAILED')
    _, old, _, latest, update, gen, boot = current_snapshot(m)
    need(old.get('host_boot_id') == boot and old.get('usb_generation') == gen and
         latest.get('host_boot_id') == boot and latest.get('usb_generation') == gen,
         'BASELINE_CHANGED_AFTER_GUARD16')
    need(run(['systemctl','is-active','qdc507-sms-tick.timer'],15).strip()
         == 'active', 'SMS_TIMER_INACTIVE_AFTER_GUARD16')
    print('V222_ONECLICK_AND_GUARD16_V2_VERIFIED=1', flush=True)


def watcher_pids():
    old, new = [], []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            args = (p / 'cmdline').read_bytes().split(b'\0')
        except (OSError, PermissionError):
            continue
        a = [x.decode('utf-8', 'replace') for x in args if x]
        if '--watch' not in a:
            continue
        if str(OLD_WATCH) in a:
            old.append(int(p.name))
        if str(WATCH) in a:
            new.append(int(p.name))
    return old, new


def start_watcher():
    run([PYTHON, '-I', '-B', str(WATCH), '--check-recovery'], 95)
    old, new = watcher_pids()
    need(not old and len(new) <= 1, 'WATCHDOG_COLLISION_AFTER_BOOT')
    if new:
        print('V222_WATCHDOG_ALREADY_RUNNING_PID=' + str(new[0]), flush=True)
        return
    log = LAB / ('qdc507-adb-watch-afterboot-v222-' +
                 time.strftime('%Y%m%d-%H%M%S') + '.log')
    fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as fp:
        p = subprocess.Popen([PYTHON, '-u', '-I', '-B', str(WATCH), '--watch'],
                             stdin=subprocess.DEVNULL, stdout=fp,
                             stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(5)
    need(p.poll() is None, 'WATCHDOG_EXITED_EARLY_' + str(log))
    need(WATCH_MARKER in log.read_text(errors='replace'),
         'WATCHDOG_START_MARKER_MISSING_' + str(log))
    old, new = watcher_pids()
    need(not old and new == [p.pid], 'WATCHDOG_NOT_UNIQUE')
    print('V222_NEW_WATCHDOG_STARTED_PID=' + str(p.pid), flush=True)


def execute_original_v221_core():
    # Caller holds startup lock; V221 itself obtains and releases coldboot locks.
    # Deliberately run with a non-__main__ name to bypass ONLY the independent
    # legacy Guard16 hook. This exact SHA-pinned source still does OneClick.
    namespace = runpy.run_path(str(CORE), run_name='qdc507_v222_reused_v221_core')
    need(callable(namespace.get('main')), 'V221_MAIN_NOT_FOUND')
    rc = namespace['main']()
    need(rc == 0, 'V221_HARDWARE_AND_ONECLICK_FAILED_RC' + str(rc))
    print('V222_V221_CORE_CHECK_PASS=1' if sys.argv[1:] == ['--check'] else
          'V222_HARDWARE_AND_ONECLICK_READY=1', flush=True)


def main():
    need(len(sys.argv) == 2 and sys.argv[1] in ('--selftest', '--check', '--boot'),
         'USE_SELFTEST_CHECK_OR_BOOT')
    mode = sys.argv[1]
    if mode == '--selftest':
        assert validate_pair is not None and business_recovery is not None
        print('V222_OFFLINE_ENTRY_SELFTEST=PASS')
        return
    pinned_sources()
    old, new = watcher_pids()
    need(not old and len(new) <= 1, 'LEGACY_OR_DUPLICATE_WATCHDOG')
    if mode == '--check':
        run([PYTHON, '-I', '-B', str(AUDIT), '--deep'], 240)
        execute_original_v221_core()
        verify_guard16_v2()
        m = load_oneclick()
        _, base2, _, base22, _, gen, boot = current_snapshot(m)
        need(base2.get('host_boot_id') == boot and base2.get('usb_generation') == gen,
             'GUARD16_V2_BASELINE_NOT_CURRENT')
        print('V222_BOOT_ENTRY_READ_ONLY_CHECK=PASS')
        return
    locks = [acquire_lock('/run/qdc507-startup-v221.lock')]
    try:
        locks.append(acquire_lock('/run/qdc507-startup-v222.lock'))
        logs = LAB / ('qdc507-coldboot-v222-' + time.strftime('%Y%m%d-%H%M%S') +
                      '-' + str(os.getpid()) + '.log')
        fd = os.open(str(logs), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW, 0o600)
        original_stdout = sys.stdout
        class Tee:
            def __init__(self, file):
                self.file = file
            def write(self, value):
                original_stdout.write(value)
                self.file.write(value)
            def flush(self):
                original_stdout.flush()
                self.file.flush()
        with os.fdopen(fd, 'w', buffering=1) as log:
            sys.stdout = Tee(log)
            try:
                print('V222_COLDBOOT_BEGIN=1')
                print('V222_LOG=' + str(logs))
                execute_original_v221_core()
                business_recovery()
                print('V222_BUSINESS_RECOVERY_COMPLETE=1', flush=True)
                start_watcher()
                print('V222_STARTUP_COMPLETE=1', flush=True)
            finally:
                sys.stdout = original_stdout
    finally:
        for fd in reversed(locks):
            os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, RuntimeError,
            BlockingIOError, subprocess.TimeoutExpired) as exc:
        print('V222_STARTUP_STOP=' + type(exc).__name__ + ':' + str(exc)[:300],
              file=sys.stderr)
        print('NO_AUTOMATIC_RETRY=1', file=sys.stderr)
        sys.exit(2)
