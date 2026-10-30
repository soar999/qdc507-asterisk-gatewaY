#!/usr/bin/python3.8
# coding: utf-8
"""Guard16 v2 in-place POST-boot hook for the *pinned* QDC507 coldboot v3.

This patches only the final __main__ entry of the existing coldboot-v3 script.
It never runs --boot during installation; it neither stops nor restarts services.
Usage: sudo python3.8 -I -B this.py --check|--install|--rollback

Original file SHA256 observed in 2026-10-28 recovery audit. Refuse drift.
A verified backup is retained. Re-running --install is an idempotent no-op.
"""
import fcntl
import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-coldboot-production-v3.py')
BACKUP = Path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-coldboot-production-v3-before-guard16-v2.py.bak')
HELPER = Path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-guard16-v2-runtime-restore-v2.py')
ORIGINAL_SHA = '0c1e5e6d317cb33e1308a3fcd92378e2c6e1f22d03a6b2878e9dbe8c647e938d'
HELPER_SHA = '077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55'
LOCKS = ['/run/qdc507-coldboot-v2.lock', '/run/qdc507-hotplug-coordinator.lock']
TAIL = b"if __name__=='__main__':\n    sys.exit(main())\n"
PATCH_MARK = 'QDC507_GUARD16_V2_AFTER_COLDBOOT_V1'

# Following code is appended only to the exact already-audited v3 file. The
# existing main() remains byte-for-byte unchanged, including its finally block,
# and has fully released coldboot/coordination locks before the helper is called.
HOOK = '''# QDC507_GUARD16_V2_AFTER_COLDBOOT_V1_BEGIN
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
            _log.write('COMMAND=' + _mode + ' RC=' + str(_p.returncode) + '\\n')
            _log.write(_p.stdout + ('\\n' if _p.stdout and not _p.stdout.endswith('\\n') else ''))
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
        _log.write('COLD_BOOT_AND_GUARD16_V2_VERIFIED=1\\n')
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
'''


class Stop(Exception):
    pass


def need(ok, why):
    if not ok:
        raise Stop(why)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def trusted(path, min_bytes=100, max_bytes=1000000):
    s = path.lstat()  # rejects symlinks
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
         not (s.st_mode & 0o022) and min_bytes <= s.st_size <= max_bytes,
         'UNTRUSTED_FILE_' + str(path))
    return path.read_bytes(), s


def expected_patch(original):
    need(digest(original) == ORIGINAL_SHA, 'ORIGINAL_COLD_BOOT_V3_SHA_CHANGED')
    need(original.endswith(TAIL) and original.count(TAIL) == 1,
         'ORIGINAL_ENTRYPOINT_UNEXPECTED')
    patch = original[:-len(TAIL)] + HOOK.encode('utf-8')
    compile(patch, str(TARGET), 'exec')  # no .pyc created
    return patch


def check_state():
    helper, _ = trusted(HELPER, 5000)
    need(digest(helper) == HELPER_SHA, 'GUARD16_V2_HELPER_SHA_CHANGED')
    cur, st = trusted(TARGET, 5000)
    if digest(cur) == ORIGINAL_SHA:
        need(not BACKUP.exists() or digest(trusted(BACKUP, 5000)[0]) == ORIGINAL_SHA,
             'BACKUP_PATH_CONFLICT')
        print('COLD_BOOT_V3_ORIGINAL_PIN_OK=1')
        print('GUARD16_V2_HOOK_NOT_INSTALLED=1')
        return 'original', cur, st
    previous, _ = trusted(BACKUP, 5000)
    patch = expected_patch(previous)
    need(cur == patch and PATCH_MARK.encode() in cur, 'CURRENT_BOOT_SCRIPT_NOT_KNOWN_PATCH')
    print('COLD_BOOT_V3_GUARD16_V2_HOOK_VERIFIED=1')
    return 'installed', previous, st


def lock():
    held = []
    for name in LOCKS:
        fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
             not (s.st_mode & 0o077), 'UNTRUSTED_LOCK_' + name)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('COLD_BOOT_OR_HOTPLUG_RECOVERY_RUNNING')
        held.append(fd)
    return held


def atomic_replace(data, original_stat):
    fd, tmp = tempfile.mkstemp(prefix='.guard16v2-coldboot-', dir=str(TARGET.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), stat.S_IMODE(original_stat.st_mode))
            os.fchown(stream.fileno(), original_stat.st_uid, original_stat.st_gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, str(TARGET))
        dfd = os.open(str(TARGET.parent), os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.lexists(tmp): os.unlink(tmp)
    need(TARGET.read_bytes() == data, 'POST_REPLACE_CONTENT_MISMATCH')


def install():
    state, original, st = check_state()
    if state == 'installed':
        print('ALREADY_INSTALLED_NO_WRITES=1')
        return
    patch = expected_patch(original)
    if BACKUP.exists() or BACKUP.is_symlink():
        saved, _ = trusted(BACKUP, 5000)
        need(saved == original, 'ORIGINAL_BACKUP_CONFLICT')
    else:
        fd = os.open(str(BACKUP), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(original)
            f.flush()
            os.fsync(f.fileno())
        print('ORIGINAL_V3_BACKUP_SAVED=' + str(BACKUP))
    atomic_replace(patch, st)
    state, _, _ = check_state()
    need(state == 'installed', 'POST_INSTALL_VERIFICATION_FAILED')
    print('COLDBOOT_V3_INPLACE_GUARD16_V2_INSTALLED=1')
    print('DSM_STARTUP_COMMAND_UNCHANGED=1')
    print('NO_RUNTIME_RESTART_OR_DEVICE_CHANGES=1')
    print('REBOOT_NOT_YET_TESTED=1')


def rollback():
    state, original, st = check_state()
    if state == 'original':
        print('ALREADY_ORIGINAL_NO_WRITES=1')
        return
    need(digest(original) == ORIGINAL_SHA, 'ROLLBACK_SOURCE_SHA_BAD')
    atomic_replace(original, st)
    need(digest(trusted(TARGET, 5000)[0]) == ORIGINAL_SHA, 'ROLLBACK_HASH_BAD')
    print('COLDBOOT_V3_ORIGINAL_RESTORED=1')
    print('DEVICE_V2_NOT_CHANGED_BY_SCRIPT_ROLLBACK=1')


def main():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(sys.argv[1:] in (['--check'], ['--install'], ['--rollback']),
         'USAGE_--check_--install_--rollback')
    print('GUARD16_V2_COLDBOOT_INPLACE_PATCH_REVISION=1')
    if sys.argv[1:] == ['--check']:
        check_state()
        print('READ_ONLY_PATCH_PREFLIGHT_OK=1')
        return
    held = lock()
    try:
        if sys.argv[1:] == ['--install']:
            install()
        else:
            rollback()
    finally:
        for fd in reversed(held): os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, SyntaxError, BlockingIOError) as exc:
        print('GUARD16_V2_INPLACE_PATCH_STOP=' + type(exc).__name__ + ':' + str(exc)[:300],
              file=sys.stderr, flush=True)
        sys.exit(2)
