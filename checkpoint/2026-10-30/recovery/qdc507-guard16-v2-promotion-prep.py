#!/usr/bin/python3.8
# coding: utf-8
"""QDC507 Guard16 v2: persist the verified A/B binaries and audit recovery.

This is stage 1 of promotion, NOT a boot integration installer.
--audit is read-only. --preserve copies verified binaries from the device to
persistent files in the lab root and writes a small manifest. Neither action
changes the active bridge, the recovery scripts, Asterisk, Docker or SMS timer.

NAS directory: /volume2/docker/asterisk/qdc507-16k-lab/
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
ADB = '/usr/local/libexec/qdc507/adb'
PY38 = '/usr/bin/python3.8'
CONTAINER = 'qdc507-hardware'
V1_SHA = '7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71'
V2_SHA = '0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913'
V1_DEVICE = '/tmp/qdc507-serial16k-v7/bridge-d5-clock8-guard16'
V2_DEVICE = '/tmp/qdc507-guard16-v2-lab/bridge-d5-v2'
V1_HOST = LAB / 'qdc507-guard16-v1-clock8-backup.armv7'
V2_HOST = LAB / 'qdc507-guard16-v2-candidate.armv7'
MANIFEST = LAB / 'qdc507-guard16-v2-promotion-manifest.json'
STATUS = LAB / 'qdc507-guard16-v2-test-fixed.py'
LOCKS = ['/run/qdc507-guard16-v2-promotion.lock',
         '/run/qdc507-dynusb-oneclick.lock',
         '/run/qdc507-dynusb-mode.lock',
         '/run/qdc507-coldboot-v2.lock',
         '/run/qdc507-coldboot-v3.lock',
         '/run/qdc507-guard16-v2-ab.lock']
PENDING = ['qdc507-dynusb-oneclick-pending-v1.json',
           'qdc507-dynusb-mode-switch-pending.json',
           'qdc507-usb-recovery-v4-pending.json',
           'qdc507-dynusb-cutover-pending-v1.json']
SCRIPTS = {
    'guard16-v1-stage': LAB / 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py',
    'oneclick': LAB / 'qdc507-dynusb-oneclick-v2.py',
    'hotplug': LAB / 'qdc507-hotplug-recovery-v1.py',
    'coldboot-v2': Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-coldboot-production-v2.py'),
    'coldboot-v3': Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-coldboot-production-v3.py'),
    'coldboot-lab': LAB / 'qdc507-coldboot-production-v3.py',
    'usb-wrapper': Path('/volume2/docker/asterisk/qdc507-current/qdc507-usb-recover.sh'),
    'mode-switch': LAB / 'qdc507-mode.py',
}

class Stop(Exception):
    pass

def need(test, message):
    if not test:
        raise Stop(message)

def sha(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as f:
        while True:
            chunk = f.read(1048576)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)

def valid_file(path, expected):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    need(stat.S_ISREG(st.st_mode) and 1024 < st.st_size < 10000000,
         'UNEXPECTED_FILE_TYPE_OR_SIZE:' + str(path))
    actual = sha(path)
    need(actual == expected, 'SHA_MISMATCH:{}:{}'.format(path, actual))
    with path.open('rb') as f:
        need(f.read(4) == b'\x7fELF', 'NOT_ELF:' + str(path))
    return True

def run(args, timeout=25):
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Stop('COMMAND_FAILED_OR_TIMEOUT:' + str(args[0]) + ':' + str(e)[:100])

def serial():
    r = run([ADB, '-H', '127.0.0.1', '-P', '5037', 'devices'])
    need(r.returncode == 0, 'ADB_DEVICES_FAILED')
    devices = []
    for line in r.stdout.splitlines():
        m = re.fullmatch(r'([^\s]+)\s+device(?:\s+.*)?', line.strip())
        if m:
            devices.append(m.group(1))
    need(len(devices) == 1, 'ADB_NOT_EXACTLY_ONE_DEVICE')
    return devices[0]

def check_idle():
    p = run(['docker','exec', CONTAINER, 'asterisk', '-rx', 'core show channels count'])
    need(p.returncode == 0 and
         re.search(r'^\s*0 active channels\s*$',p.stdout,re.M) and
         re.search(r'^\s*0 active calls\s*$',p.stdout,re.M),
         'ASTERISK_NOT_PROVEN_IDLE')
    print('ASTERISK_IDLE=1')
    timer = run(['systemctl','is-active','qdc507-sms-tick.timer'],timeout=12)
    need(timer.returncode == 0 and timer.stdout.strip() == 'active', 'SMS_TIMER_NOT_ACTIVE')
    print('SMS_TIMER_ACTIVE=1')
    worker = run(['systemctl','is-active','qdc507-sms-tick.service'],timeout=12)
    need(worker.stdout.strip() == 'inactive', 'SMS_WORKER_NOT_IDLE')
    print('SMS_WORKER_IDLE=1')

def device_status():
    need(STATUS.is_file(), 'TESTED_STATUS_SCRIPT_MISSING:' + str(STATUS))
    p = run([PY38, '-I', '-B', str(STATUS), '--status'], timeout=45)
    need(p.returncode == 0, 'DEVICE_STATUS_FAILED:' + (p.stderr or p.stdout)[-160:])
    fields = {}
    for line in p.stdout.splitlines():
        m = re.fullmatch(r'(DEVICE_[A-Z0-9_]+|AB_READ_ONLY_STATUS_OK)=(.*)',line.strip())
        if m:
            fields[m.group(1)] = m.group(2)
    need(fields.get('AB_READ_ONLY_STATUS_OK') == '1', 'DEVICE_STATUS_UNVERIFIED')
    for key in ('DEVICE_CURRENT_MODE','DEVICE_V1_BACKUP','DEVICE_SUPERVISOR_STATUS'):
        need(key in fields, 'DEVICE_STATUS_MISSING_' + key)
    print('DEVICE_CURRENT_MODE=' + fields['DEVICE_CURRENT_MODE'])
    print('DEVICE_V1_BACKUP=' + fields['DEVICE_V1_BACKUP'])
    print('DEVICE_SUPERVISOR_STATUS=' + fields['DEVICE_SUPERVISOR_STATUS'])
    return fields

def lock_all():
    locks=[]
    for name in LOCKS:
        fd = os.open(name, os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        st = os.fstat(fd)
        need(st.st_uid == 0 and stat.S_ISREG(st.st_mode) and
             not (stat.S_IMODE(st.st_mode)&0o077), 'LOCK_UNTRUSTED:' + name)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('RECOVERY_OR_MODE_LOCK_BUSY:' + name)
        locks.append(fd)
    return locks

def print_recovery_audit():
    print('=== RECOVERY_FILE_HASHES_AND_REFERENCES ===')
    b = LAB / 'qdc507-dynusb-oneclick-baseline-v1.json'
    if b.is_file():
        try:
            data = json.loads(b.read_text(encoding='utf-8'))
            pins = data.get('scripts',{})
            print('BASELINE_MODE=' + str(data.get('mode','unknown')))
            print('BASELINE_GUARD16_PIN=' + str(pins.get('qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py','missing')))
            print('BASELINE_ONECLICK_PIN=' + str(pins.get('qdc507-dynusb-oneclick-v2.py','missing')))
        except (ValueError, OSError, TypeError):
            print('BASELINE_PARSE_ERROR=1')
    for name, path in SCRIPTS.items():
        if not path.is_file():
            print('RECOVERY_FILE_{}=missing'.format(name))
            continue
        if path.is_symlink():
            print('RECOVERY_FILE_{}=symlink_do_not_edit'.format(name))
            continue
        print('RECOVERY_FILE_{}={}'.format(name,path))
        print('RECOVERY_SHA256_{}={}'.format(name, sha(path)))
        try:
            lines=path.read_text(encoding='utf-8').splitlines()
        except (OSError,UnicodeDecodeError):
            print('RECOVERY_REFERENCES_{}=non_utf8'.format(name))
            continue
        found=[]
        # Only reveal code lines identifying call paths, never dump full configs.
        pat=re.compile(r'guard16|clock8|coldboot|oneclick|hotplug|recover|stage.*bridge|bridge.*stage|D5_|BRIDGE_D5',re.I)
        for i,line in enumerate(lines):
            if pat.search(line) and len(found)<12:
                compact=line.strip().replace('\t',' ')
                if len(compact)>180:
                    compact=compact[:180]+'...'
                found.append('{}:{}'.format(i+1,compact))
        print('RECOVERY_MATCHES_{}={}'.format(name,len(found)))
        for match in found:
            print('RECOVERY_REF_{}={}'.format(name, match))
    print('RECOVERY_AUDIT_COMPLETE=1')

def preserve_one(remote, target, expected, serial_id):
    if valid_file(target, expected):
        print('NAS_BIN_ALREADY_VERIFIED=' + str(target))
        return
    fd,tmp=tempfile.mkstemp(prefix='.qdc507-guard16-preserve-',dir=str(LAB))
    os.close(fd)
    try:
        # adb pull must write its destination as raw bytes; never use adb shell
        # for a binary payload (shell on this firmware may echo source input).
        cmd = [ADB, '-H', '127.0.0.1', '-P', '5037', '-s', serial_id,
               'pull', remote, tmp]
        r = run(cmd, timeout=60)
        need(r.returncode == 0, 'ADB_PULL_FAILED:' + (r.stderr or r.stdout)[-200:])
        need(valid_file(Path(tmp), expected), 'PULLED_FILE_NOT_VERIFIED')
        os.chmod(tmp, 0o700)
        # Refuse overwrite in case another tool created a file in the meantime.
        need(not target.exists() and not target.is_symlink(),
             'TARGET_APPEARED_DURING_COPY:' + str(target))
        os.replace(tmp, str(target))
        tmp=None
        need(valid_file(target,expected), 'POST_SAVE_SHA_MISMATCH')
        print('NAS_BIN_SAVED=' + str(target))
    finally:
        if tmp is not None and os.path.lexists(tmp):
            os.unlink(tmp)

def write_manifest():
    data={
        'format':'qdc507-guard16-v2-promotion-prep-v1',
        'generated_local_time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'state':'NAS binaries preserved; recovery integration NOT installed',
        'v1_file':str(V1_HOST),'v1_sha256':V1_SHA,
        'v2_file':str(V2_HOST),'v2_sha256':V2_SHA,
        'previous_device_active':'v2',
        'recovery_hook_installed':False,
        'live_restart_or_hotplug_verified':False,
    }
    raw=(json.dumps(data,indent=2,sort_keys=True)+'\n').encode('utf-8')
    if MANIFEST.exists() or MANIFEST.is_symlink():
        need(not MANIFEST.is_symlink() and MANIFEST.is_file(), 'MANIFEST_INVALID_TYPE')
        try:
            old=json.loads(MANIFEST.read_text(encoding='utf-8'))
            need(old.get('format')==data['format'] and
                 old.get('v1_sha256')==V1_SHA and old.get('v2_sha256')==V2_SHA,
                 'MANIFEST_CONFLICT')
        except (OSError,ValueError) as e:
            raise Stop('MANIFEST_UNREADABLE:' + type(e).__name__)
        print('PROMOTION_MANIFEST_ALREADY_PRESENT=1')
        return
    fd,tmp=tempfile.mkstemp(prefix='.qdc507-guard16-manifest-',dir=str(LAB))
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,str(MANIFEST))
    finally:
        if os.path.lexists(tmp): os.unlink(tmp)
    print('PROMOTION_MANIFEST_SAVED=' + str(MANIFEST))

def main():
    parser=argparse.ArgumentParser(description='Guard16 v2 persistent archive and recovery audit (no runtime changes)')
    g=parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--audit',action='store_true',help='read-only recovery inventory and v2 status')
    g.add_argument('--preserve',action='store_true',help='save verified device v1/v2 binaries to NAS')
    args=parser.parse_args()
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_DIRECTORY_MISSING_OR_SYMLINK')
    print('GUARD16_V2_PROMOTION_PREP_V1=1')
    for fn, expected in ((V1_HOST,V1_SHA),(V2_HOST,V2_SHA)):
        if valid_file(fn,expected):
            print('NAS_PERSISTENT_BIN_OK=' + str(fn))
        else:
            print('NAS_PERSISTENT_BIN_MISSING=' + str(fn))
    if args.audit:
        s=device_status()
        print_recovery_audit()
        print('READ_ONLY_AUDIT_COMPLETE=1')
        return
    need(os.geteuid()==0,'ROOT_REQUIRED_FOR_PRESERVE')
    locks=lock_all()
    try:
        for name in PENDING:
            need(not os.path.lexists(str(LAB/name)), 'RECOVERY_PENDING:' + name)
        check_idle()
        s=device_status()
        need(s['DEVICE_CURRENT_MODE']=='v2', 'CURRENT_MODE_NOT_V2')
        need(s['DEVICE_V1_BACKUP']=='present', 'ORIGINAL_V1_BACKUP_MISSING')
        need(s['DEVICE_SUPERVISOR_STATUS']=='DONE d4=0 d5=0', 'DEVICE_SUPERVISOR_BUSY')
        si=serial()
        preserve_one(V2_DEVICE,V2_HOST,V2_SHA,si)
        preserve_one(V1_DEVICE,V1_HOST,V1_SHA,si)
        write_manifest()
        print('GUARD16_V2_NAS_BINARIES_PERSISTED=1')
        print('RUNTIME_UNCHANGED=1')
        print('RECOVERY_INTEGRATION_PENDING=1')
    finally:
        for fd in reversed(locks): os.close(fd)

if __name__=='__main__':
    os.umask(0o077)
    try: main()
    except (Stop, OSError, PermissionError, ValueError) as e:
        print('PROMOTION_PREP_STOP=' + str(e)[:330],file=sys.stderr)
        sys.exit(2)
