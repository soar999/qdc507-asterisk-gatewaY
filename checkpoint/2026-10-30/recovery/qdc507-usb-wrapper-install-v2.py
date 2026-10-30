#!/usr/bin/python3.8
# coding: utf-8
"""Safely replace ONLY the existing QDC507 USB/SIM-swap shell entrypoint.

--check: read-only source/target audit.
--install: root-only backup + atomic replacement, only for known old wrappers.
--rollback: atomic restoration from the exact private backup.
No Docker, ADB, SMS, kernel module or USB operations are performed.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
SOURCE = LAB / 'qdc507-usb-recover-v2.sh'
TARGET = Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh')
BACKUP = LAB / 'qdc507-usb-recover-before-v2.sh.private.bak'
NEW_SHA = 'f819ee16242ff58ce8ab975584e184a7f1587237c7a396c9c82b93e9df41d7d5'
OLD_HASHES = (
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    '4d4687a778f6484bad1b76b28554e4dcdd634e9cda10622aef6d821392ae16e0',
)
LOCKS = (
    '/run/qdc507-usb-sim-swap-v2.lock',
    '/run/qdc507-hotplug-coordinator.lock',
    '/run/qdc507-dynusb-oneclick.lock',
    '/run/qdc507-coldboot-v2.lock',
)

class Halt(Exception): pass

def need(ok, why):
    if not ok: raise Halt(why)

def emit(line): print(line, flush=True)

def sha(b): return hashlib.sha256(b).hexdigest()

def trusted_file(p, private=False, minbytes=0):
    s=p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and
         not (stat.S_IMODE(s.st_mode) & (0o077 if private else 0o022)) and
         minbytes <= s.st_size <= 60000,
         'UNTRUSTED_FILE_' + p.name)
    return p.read_bytes()

def locks():
    held=[]
    try:
        for name in LOCKS:
            fd=os.open(name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            held.append(fd)
            st=os.fstat(fd)
            need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
                 not (stat.S_IMODE(st.st_mode) & 0o077),
                 'UNTRUSTED_LOCK_'+name)
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Halt('RECOVERY_IN_PROGRESS_'+name)
        return held
    except BaseException:
        for fd in reversed(held): os.close(fd)
        raise

def snapshot():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_PATH_INVALID')
    need(TARGET.parent.is_dir() and not TARGET.parent.is_symlink(),
         'TARGET_RECOVERY_DIRECTORY_INVALID')
    source=trusted_file(SOURCE,minbytes=1000)
    need(sha(source)==NEW_SHA, 'NEW_WRAPPER_HASH_MISMATCH')
    need(source.startswith(b'#!/bin/sh\n'), 'WRAPPER_HEADER_CHANGED')
    existing=trusted_file(TARGET)
    oldsha=sha(existing)
    need(oldsha in OLD_HASHES or oldsha==NEW_SHA,
         'EXISTING_WRAPPER_NOT_A_KNOWN_VERSION_REFUSE_OVERWRITE')
    if BACKUP.exists() or BACKUP.is_symlink():
        saved=trusted_file(BACKUP,private=True)
        need(sha(saved) in OLD_HASHES, 'PRIVATE_BACKUP_UNEXPECTED_HASH')
    else:
        saved=None
    emit('CURRENT_WRAPPER_SHA256='+oldsha)
    emit('NEW_WRAPPER_SHA256='+NEW_SHA)
    emit('OLD_WRAPPER_BACKUP='+('present' if saved is not None else 'absent'))
    return source,existing,saved

def backup_old(data):
    if BACKUP.exists() or BACKUP.is_symlink():
        need(trusted_file(BACKUP,private=True)==data,
             'EXISTING_PRIVATE_BACKUP_CONFLICT')
        return
    fd=os.open(str(BACKUP),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        os.fchown(f.fileno(),0,0)
        os.fchmod(f.fileno(),0o600)
        f.write(data); f.flush();os.fsync(f.fileno())
    need(trusted_file(BACKUP,private=True)==data,'PRIVATE_BACKUP_POSTWRITE_MISMATCH')
    emit('OLD_WRAPPER_ROOT_ONLY_BACKUP_SAVED=1')

def replace(data):
    fd,name=tempfile.mkstemp(prefix='.qdc507-usb-entry-v2-',dir=str(TARGET.parent))
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchown(f.fileno(),0,0)
            os.fchmod(f.fileno(),0o700)
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,str(TARGET))
    finally:
        if os.path.lexists(name):os.unlink(name)
    need(trusted_file(TARGET)==data,'INSTALLED_WRAPPER_READBACK_FAILED')

def main():
    need(sys.argv[1:] in (['--check'],['--install'],['--rollback']),
         'USE_--check_--install_--rollback')
    op=sys.argv[1]
    held=locks() if op!='--check' else []
    try:
        source,old,saved=snapshot()
        if op=='--check':
            emit('READ_ONLY_USB_WRAPPER_INSTALL_CHECK_OK=1')
        elif op=='--install':
            if old==source:
                emit('WRAPPER_V2_ALREADY_INSTALLED_NO_WRITES=1')
                return
            backup_old(old)
            replace(source)
            emit('ORIGINAL_USB_RECOVER_ENTRYPOINT_UPDATED=1')
            emit('NO_CONTAINER_ADB_OR_USB_OPERATION=1')
        else:
            need(saved is not None,'NO_PRIVATE_BACKUP_FOR_ROLLBACK')
            need(old==source,'ROLLBACK_REQUIRES_INSTALLED_V2_WRAPPER')
            replace(saved)
            emit('ORIGINAL_USB_RECOVER_ENTRYPOINT_ROLLED_BACK=1')
    finally:
        for fd in reversed(held):os.close(fd)

if __name__=='__main__':
    try:main()
    except (Halt,OSError,ValueError,BlockingIOError) as e:
        print('USB_WRAPPER_INSTALL_STOP='+type(e).__name__+':'+str(e)[:280],
              file=sys.stderr,flush=True)
        sys.exit(2)
