#!/usr/bin/python3.8
"""Install/rollback exact V221 Guard16-v2 baseline handshake, no restarts.

--check: read-only; --install: guarded atomic replacement of two LAB scripts;
--rollback: only from an installer-created manifest, exact-hash verified.
Does not start/stop Docker, edit DSM tasks, or alter hot recovery.
"""
import fcntl
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
CORE = LAB / 'qdc507-coldboot-memoryfix-v221.py'
ENTRY = LAB / 'qdc507-coldboot-startup-v221.py'
CAND_CORE = LAB / 'qdc507-coldboot-memoryfix-v221.guard16-sync-20261121.candidate.py'
CAND_ENTRY = LAB / 'qdc507-coldboot-startup-v221.guard16-sync-20261121.candidate.py'
PREVIOUS = {
    CORE.name: '210ec5f510b3f6d3620c778e999f1c86d8969f60ff772561a501b98ccd41e310',
    ENTRY.name: '090dd6ddcda2963ca9f57387bd53abf0335d420551db3bc851da0c0e561cde96',
}
UPDATED = {
    CORE.name: 'afb40eeddafd5148ce86504641dbadf8b99f8af560b847709f82dd9985204a4d',
    ENTRY.name: '4ecb63fcf705358c357c18c16435b4ec1f456cb6861dd9f05f9241d6f3a2f356',
}
MANIFEST = LAB / 'qdc507-v221-guard16-sync-installer-v1-manifest.json'
LOCK = '/run/qdc507-startup-v221.lock'

class Stop(Exception):
    pass

def need(x, why):
    if not x:
        raise Stop(why)

def read(path, private=False):
    st = path.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and not
         (stat.S_IMODE(st.st_mode) & 0o022) and
         (not private or (st.st_uid == 0 and not (stat.S_IMODE(st.st_mode) & 0o077))),
         'UNTRUSTED_FILE_' + path.name)
    data = path.read_bytes()
    need(500 <= len(data) <= 150000, 'FILE_SIZE_' + path.name)
    return data

def sha(data):
    return hashlib.sha256(data).hexdigest()

def state():
    actual = {p.name: sha(read(p)) for p in (CORE, ENTRY)}
    if actual == PREVIOUS:
        return 'ORIGINAL', actual
    if actual == UPDATED:
        return 'UPDATED', actual
    raise Stop('MIXED_OR_UNEXPECTED_PRODUCTION_SOURCES_' + repr(actual))

def check_candidates():
    for candidate, target in ((CAND_CORE, CORE), (CAND_ENTRY, ENTRY)):
        need(sha(read(candidate)) == UPDATED[target.name],
             'CANDIDATE_HASH_' + candidate.name)
    print('V221_CANDIDATES_SHA256_VERIFIED=1')

def write_fsync_new(dest, raw, mode=0o600):
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as fp:
        os.fchown(fp.fileno(), 0, 0)
        os.fchmod(fp.fileno(), mode)
        fp.write(raw)
        fp.flush()
        os.fsync(fp.fileno())

def atomic_replace(dest, raw):
    tmp = LAB / ('.qdc507-v221-replace-' + dest.name + '-' + str(os.getpid()))
    try:
        write_fsync_new(tmp, raw, 0o700)
        os.replace(str(tmp), str(dest))
        dir_fd = os.open(str(LAB), os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
    finally:
        if tmp.exists(): tmp.unlink()

def lock():
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    s = os.fstat(fd)
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
         not (stat.S_IMODE(s.st_mode) & 0o077), 'STARTUP_LOCK_UNTRUSTED')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd

def install():
    check_candidates()
    held = lock()
    try:
        st, _ = state()
        need(st == 'ORIGINAL', 'EXPECTED_ORIGINAL_V221_FOR_INSTALL')
        need(not MANIFEST.exists() and not MANIFEST.is_symlink(),
             'INSTALL_MANIFEST_ALREADY_EXISTS')
        stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
        backup = {}
        for target in (CORE, ENTRY):
            raw = read(target)
            p = LAB / (target.stem + '.before-guard16-sync-' + stamp + '.py')
            write_fsync_new(p, raw)
            need(sha(read(p, True)) == PREVIOUS[target.name], 'BACKUP_FAILED_' + target.name)
            backup[target.name] = p.name
        manifest = {'format':'qdc507-v221-guard16-sync-installer-v1',
                    'backups':backup, 'previous':PREVIOUS, 'updated':UPDATED}
        write_fsync_new(MANIFEST, (json.dumps(manifest,indent=2)+'\n').encode())
        # The original task's lock is held, and both new sources are pinned.
        # Any interruption leaves the original backups and a clear manifest.
        for target, candidate in ((CORE, CAND_CORE), (ENTRY, CAND_ENTRY)):
            need(sha(read(target)) == PREVIOUS[target.name], 'TARGET_CHANGED_DURING_INSTALL')
            atomic_replace(target, read(candidate))
        need(state()[0] == 'UPDATED', 'POST_INSTALL_VERIFICATION_FAILED')
        print('V221_GUARD16_INTEGRATION_INSTALLED=1')
        print('DSM_BOOT_TASK_UNCHANGED=1')
        print('NO_CONTAINER_OR_BUSINESS_RESTART=1')
        print('BACKUP_MANIFEST=' + str(MANIFEST))
    finally:
        os.close(held)

def rollback():
    held = lock()
    try:
        raw = read(MANIFEST, True)
        m = json.loads(raw)
        need(m.get('format') == 'qdc507-v221-guard16-sync-installer-v1'
             and m.get('previous') == PREVIOUS and m.get('updated') == UPDATED,
             'MANIFEST_CHANGED')
        for target in (CORE, ENTRY):
            need(sha(read(target)) in (PREVIOUS[target.name], UPDATED[target.name]),
                 'PRODUCTION_UNEXPECTED_' + target.name)
            backup = LAB / m['backups'][target.name]
            need(backup.parent == LAB and backup.name.startswith(target.stem + '.before-guard16-sync-'),
                 'BACKUP_PATH_UNSAFE_' + target.name)
            need(sha(read(backup, True)) == PREVIOUS[target.name],
                 'BACKUP_HASH_CHANGED_' + target.name)
        for target in (CORE, ENTRY):
            backup = LAB / m['backups'][target.name]
            if sha(read(target)) != PREVIOUS[target.name]:
                atomic_replace(target, read(backup, True))
        need(state()[0] == 'ORIGINAL', 'ROLLBACK_VERIFY_FAILED')
        print('V221_ORIGINAL_FILES_RESTORED=1')
        print('KEEP_BACKUPS_AND_MANIFEST_FOR_AUDIT=1')
    finally:
        os.close(held)

def main():
    need(os.geteuid() == 0 and LAB.is_dir() and not LAB.is_symlink(),
         'ROOT_OR_LAB_INVALID')
    need(len(sys.argv) == 2 and sys.argv[1] in ('--check','--install','--rollback'),
         'USE_--check_--install_--rollback')
    mode = sys.argv[1]
    if mode == '--check':
        s, actual = state()
        print('V221_INSTALL_STATE=' + s)
        check_candidates()
        print('NO_WRITES_READ_ONLY_CHECK=1')
    elif mode == '--install':
        install()
    else:
        rollback()

if __name__ == '__main__':
    try:main()
    except (Stop, OSError, ValueError, KeyError, BlockingIOError) as e:
        print('V221_INSTALL_STOP=' + type(e).__name__ + ':' + str(e)[:280],file=sys.stderr)
        sys.exit(2)
