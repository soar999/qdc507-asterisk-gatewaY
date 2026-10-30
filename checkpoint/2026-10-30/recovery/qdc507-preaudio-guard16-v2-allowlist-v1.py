#!/usr/bin/python3.8
# coding: utf-8
"""Patch only the stopped production container's PreAudio D5 hash allowlist.

--check: copy/read and validate the in-container script; no production changes.
--install: immutable root-only NAS backup, exact source edit, Python compile,
           docker cp to the SAME stopped container, then independent readback.
--rollback: restore original PreAudio script while container is stopped.

Does not modify NAS USB devices, QDC507 hardware, Asterisk configs, the Docker
image, Guard16 links, SMS services, recovery script pins or oneclick baseline.
The container writable-layer change survives restarting this same container,
but NOT replacing/recreating the container. Not a full coldboot fix.
"""
import fcntl
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile

LAB = pathlib.Path('/volume2/docker/asterisk/qdc507-16k-lab')
NAME = 'qdc507-hardware'
TARGET = '/opt/qdc16k-preaudio-manual/prepare.py'
BACKUP = LAB / 'qdc507-preaudio-before-guard16v2.py.private.bak'
OLD_SHA = '7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71'
V2_SHA = '0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913'
EXPECTED_CONTAINER = 'cd1f8d1ccc3d3cd465cfefc1c87cc8521cb9979c95333d0f4e60b6f829115fd4'
EXPECTED_IMAGE = 'sha256:1d55612e888a736f372f275a47d6ae3f66f7843fe27ea6673ba68f6a8281e6ca'
LOCK_NAMES = ('/run/qdc507-coldboot-v2.lock', '/run/qdc507-hotplug-coordinator.lock',
              '/run/qdc507-dynusb-oneclick.lock', '/run/qdc507-dynusb-mode.lock')
OLD_ASSIGN = "GUARD_HASH='" + OLD_SHA + "'\n"
OLD_CHECK = ''' if active:code+='[ "$(sha256sum "$B/bridge-d5" | cut -d " " -f1)" = "'+GUARD_HASH+'" ]\\n'\n'''
NEW_ASSIGN = OLD_ASSIGN + "GUARD_HASH_V2='" + V2_SHA + "'\n"
NEW_CHECK = ''' if active:
  code+='D5_H=$(sha256sum "$B/bridge-d5" | cut -d " " -f1)\\n'
  code+='[ "$D5_H" = "'+GUARD_HASH+'" ] || [ "$D5_H" = "'+GUARD_HASH_V2+'" ]\\n'
'''

class Halt(Exception):
    pass

def need(condition, reason):
    if not condition:
        raise Halt(reason)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def call(args, timeout=45):
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Halt('COMMAND_UNAVAILABLE_OR_TIMEOUT_' + args[0] + '_' + type(e).__name__)
    need(r.returncode == 0, 'COMMAND_FAILED_' + args[0] + ':' + (r.stderr or r.stdout)[-200:])
    return r.stdout

def acquire_recovery_locks():
    held = []
    try:
        for path in LOCK_NAMES:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            held.append(fd)
            st = os.fstat(fd)
            need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
                 not (st.st_mode & 0o077), 'UNTRUSTED_RECOVERY_LOCK_' + path)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Halt('RECOVERY_OR_MODE_SWITCH_IN_PROGRESS_' + path)
        return held
    except BaseException:
        for fd in reversed(held):
            os.close(fd)
        raise

def inspect_stopped(require_stopped):
    rows = json.loads(call(['docker', 'inspect', NAME], 15))
    need(len(rows) == 1, 'DOCKER_CONTAINER_NOT_UNIQUE')
    row = rows[0]
    need(row.get('Id') == EXPECTED_CONTAINER and row.get('Image') == EXPECTED_IMAGE,
         'PRODUCTION_CONTAINER_ID_OR_IMAGE_DRIFT')
    running = row.get('State', {}).get('Running') is True
    if require_stopped:
        need(not running, 'CONTAINER_RUNNING_REFUSE_EDIT')
    print('PRODUCTION_CONTAINER_RUNNING=' + str(int(running)), flush=True)

def read_container_script():
    with tempfile.TemporaryDirectory(dir=str(LAB), prefix='.qdc507-preaudio-read-') as tmp:
        dest = pathlib.Path(tmp) / 'prepare.py'
        call(['docker', 'cp', NAME + ':' + TARGET, str(dest)], 45)
        s = dest.lstat()
        need(stat.S_ISREG(s.st_mode) and 1000 <= s.st_size <= 25000,
             'CONTAINER_PREAUDIO_INVALID_FILE_TYPE_OR_SIZE')
        return dest.read_bytes()

def trusted_backup():
    st = BACKUP.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
         not (st.st_mode & 0o077) and st.st_nlink == 1 and
         1000 <= st.st_size <= 25000,
         'BACKUP_FILE_NOT_PRIVATE_AND_ROOT_OWNED')
    return BACKUP.read_bytes()

def transform(original):
    need(len(original) <= 25000, 'UNEXPECTED_PREAUDIO_SIZE')
    try:
        txt = original.decode('utf-8')
    except UnicodeError:
        raise Halt('PREAUDIO_NOT_UTF8')
    # Only the known 16k PreAudio implementation is in scope.
    need(txt.count(OLD_ASSIGN) == 1, 'EXPECTED_V1_GUARD_ASSIGNMENT_MISSING_OR_DUPLICATE')
    need(txt.count(OLD_CHECK) == 1, 'EXPECTED_V1_ONLY_CHECK_MISSING_OR_DUPLICATE')
    need('GUARD_HASH_V2' not in txt and 'PREAUDIO_PREPARE_UNKNOWN_STATE' in txt and
         'restore_missing_runtime' in txt and 'DEVICE_HASH=' in txt,
         'PREAUDIO_SOURCE_STRUCTURE_UNEXPECTED')
    need(bool(re.search(r"(?m)^SERIAL='[A-Za-z0-9._:-]{4,120}'$", txt)),
         'DEVICE_ID_FORMAT_UNEXPECTED')
    compile(original, TARGET, 'exec')
    patched = txt.replace(OLD_ASSIGN, NEW_ASSIGN, 1).replace(OLD_CHECK, NEW_CHECK, 1)
    compile(patched, TARGET, 'exec')
    return patched.encode('utf-8')

def determine(current):
    # Installed state must have a real original source to check against.
    if BACKUP.exists() or BACKUP.is_symlink():
        original = trusted_backup()
        patched = transform(original)
        if current == original:
            return 'original', original, patched
        if current == patched:
            return 'patched', original, patched
        raise Halt('CURRENT_PREAUDIO_DOES_NOT_MATCH_TRUSTED_BACKUP_OR_PATCH')
    patched = transform(current)
    return 'original', current, patched

def private_backup(data):
    if BACKUP.exists() or BACKUP.is_symlink():
        need(trusted_backup() == data, 'PRIVATE_BACKUP_CONFLICT')
        return
    fd = os.open(str(BACKUP), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        raise
    need(trusted_backup() == data, 'PRIVATE_BACKUP_POSTWRITE_FAILED')
    print('ROOT_ONLY_ORIGINAL_PREAUDIO_BACKUP_SAVED=1', flush=True)

def write_container(data):
    with tempfile.TemporaryDirectory(dir=str(LAB), prefix='.qdc507-preaudio-write-') as tmp:
        path = pathlib.Path(tmp) / 'prepare.py'
        with path.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chown(str(path), 0, 0)
        os.chmod(str(path), 0o644)
        call(['docker', 'cp', str(path), NAME + ':' + TARGET], 65)
    found = read_container_script()
    need(found == data, 'DOCKER_COPY_READBACK_HASH_OR_BYTES_MISMATCH')
    print('CONTAINER_PREAUDIO_READBACK_SHA256=' + sha(found), flush=True)

def main():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(sys.argv[1:] in (['--check'], ['--install'], ['--rollback']),
         'USE_--check_OR_--install_OR_--rollback')
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_PATH_INVALID')
    action = sys.argv[1]
    print('QDC507_PREAUDIO_GUARD16_V2_ALLOWLIST_PATCH_REV=1', flush=True)
    held = acquire_recovery_locks() if action != '--check' else []
    try:
        inspect_stopped(action != '--check')
        current = read_container_script()
        state, original, patched = determine(current)
        print('CONTAINER_PREAUDIO_STATE=' + state, flush=True)
        print('CONTAINER_PREAUDIO_SHA256=' + sha(current), flush=True)
        print('ORIGINAL_PREAUDIO_SHA256=' + sha(original), flush=True)
        print('PATCHED_PREAUDIO_SHA256=' + sha(patched), flush=True)
        if action == '--check':
            print('READ_ONLY_PREAUDIO_PATCH_CHECK_OK=1', flush=True)
        elif action == '--install':
            if state == 'patched':
                print('ALREADY_PATCHED_NO_WRITES=1', flush=True)
                return
            private_backup(original)
            write_container(patched)
            print('PREAUDIO_D5_V1_V2_STRICT_ALLOWLIST_INSTALLED=1', flush=True)
            print('CONTAINER_NOT_STARTED_OR_RECREATED=1', flush=True)
            print('ONECLICK_AND_COLDBOOT_PINNED_SCRIPTS_UNCHANGED=1', flush=True)
        else:
            need(BACKUP.exists(), 'NO_ORIGINAL_BACKUP_FOR_ROLLBACK')
            if state == 'original':
                print('ALREADY_ORIGINAL_NO_WRITES=1', flush=True)
                return
            write_container(original)
            print('PREAUDIO_PATCH_ROLLBACK_OK=1', flush=True)
    finally:
        for fd in reversed(held):
            os.close(fd)

if __name__ == '__main__':
    try:
        main()
    except (Halt, OSError, ValueError, SyntaxError, KeyError) as exc:
        print('PREAUDIO_PATCH_STOP=' + type(exc).__name__ + ':' + str(exc)[:320],
              file=sys.stderr, flush=True)
        sys.exit(2)
