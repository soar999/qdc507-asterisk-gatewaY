#!/usr/bin/python3.8
"""Pinned revision of QDC507 V221's V22 deep audit for DSM 7.2.2.
--check read-only; --install atomic publication of new manifest and audit;
--rollback restore original audit (historical manifest stays preserved).
This script never runs a recovery or restarts any service/container.
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
AUDIT = LAB / 'qdc507-memoryfix-v22-audit.py'
OLD_MANIFEST = LAB / 'qdc507-memoryfix-v22-production-20261120.json'
NEW_MANIFEST = LAB / 'qdc507-memoryfix-v22-production-20261121.json'
CAND_AUDIT = LAB / 'qdc507-memoryfix-v22-audit.dsm722-v221.candidate.py'
CAND_MANIFEST = LAB / 'qdc507-memoryfix-v22-production-20261121.candidate.json'
USB = LAB / 'qdc507-usb-recover-memoryfix-v22.sh'
CORE = LAB / 'qdc507-coldboot-memoryfix-v221.py'
STARTUP = LAB / 'qdc507-coldboot-startup-v221.py'
STAMP = LAB / 'qdc507-v221-dsm722-audit-update-manifest.json'
LOCK = '/run/qdc507-startup-v221.lock'
OLD_AUDIT_SHA = 'fac707fda70b67ec9a769a30edf54836b253f45be7359a5b8ab7f9b9bb9e34fb'
NEW_AUDIT_SHA = 'da805cb402e59c8b7571ebd096f0a6910a02ebd1dca3888ef7910ab366fdbf6b'
OLD_JSON_SHA = '01677c748fef67abf6893bc1851eb1de3fdd995d0a6a24d5928d1b4915733962'
NEW_JSON_SHA = '1cfb2b0b1c817e0de93ecd060bf512633612b30dbc5b0b29b10582db444b5ac6'
USB_SHA = 'a9ae0b71f42dc74f829c6827f8e1c3c34404ac0671677c1e0a29a3ab81059cb4'
USB_BASE_SHA = '51702ae8f9acd338604795dfc93d9dde22fb9636633de275764ad6258067a2d7'
CORE_SHA = 'afb40eeddafd5148ce86504641dbadf8b99f8af560b847709f82dd9985204a4d'
STARTUP_SHA = '4ecb63fcf705358c357c18c16435b4ec1f456cb6861dd9f05f9241d6f3a2f356'
class Stop(Exception):pass
def need(x,why):
    if not x: raise Stop(why)
def sha(b): return hashlib.sha256(b).hexdigest()
def trusted(path, private=False):
    st=path.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1
         and not (stat.S_IMODE(st.st_mode)&(0o077 if private else 0o022))
         and 100<=st.st_size<=250000,'UNTRUSTED_'+path.name)
    return path.read_bytes()
def verify_current_usb_exact_overlay():
    raw=trusted(USB, private=True)
    need(sha(raw)==USB_SHA, 'USB_PRODUCTION_SOURCE_HASH_CHANGED')
    a=raw.decode('utf-8')
    need(a.count('import http.client\n')==1, 'DSM722_HTTP_IMPORT_CHANGED')
    a=a.replace('import http.client\n','',1)
    pat=(r'\n# V22_DOCKER_API_COMPAT_DSM722_V1\ndef docker_api_cli\(argv, timeout\):'
         r'.*?(?=\ndef command\(argv, timeout=30, allow_fail=False, passthrough=False\):)')
    a,n=re.subn(pat,'',a,flags=re.S)
    need(n==1, 'DSM722_DOCKER_API_COMPAT_BLOCK_CHANGED')
    pat=(r'def command\(argv, timeout=30, allow_fail=False, passthrough=False\):\n'
         r'    if argv and argv\[0\] == \'docker\':.*?(?=    try:\n)')
    # Avoid broad matching: its published legacy reference must reproduce byte-for-byte.
    a,n=re.subn(pat, 'def command(argv, timeout=30, allow_fail=False, passthrough=False):\n',a,flags=re.S)
    need(n==1, 'DSM722_DOCKER_EXEC_OVERLAY_CHANGED')
    need(a.count("os.uname().release == '4.4.302+'") == 1,
         'DSM722_KERNEL_PIN_CHANGED')
    a=a.replace("os.uname().release == '4.4.302+'", "os.uname().release == '4.4.180+'",1)
    need(sha(a.encode('utf-8'))==USB_BASE_SHA,
         'USB_DSM722_OVERLAY_NOT_EXACT_HISTORICAL_SOURCE')
    print('USB_DSM722_EXACT_HISTORICAL_PATCH_VERIFIED=1',flush=True)
def read_previous():
    need(sha(trusted(OLD_MANIFEST,private=True))==OLD_JSON_SHA,
         'HISTORICAL_RELEASE_MANIFEST_CHANGED')
    need(sha(trusted(CORE))==CORE_SHA and sha(trusted(STARTUP))==STARTUP_SHA,
         'V221_INSTALLED_SOURCES_CHANGED')
    verify_current_usb_exact_overlay()
def candidate():
    need(sha(trusted(CAND_AUDIT))==NEW_AUDIT_SHA,'AUDIT_CANDIDATE_CHANGED')
    need(sha(trusted(CAND_MANIFEST))==NEW_JSON_SHA,'MANIFEST_CANDIDATE_CHANGED')
    d=json.loads(trusted(CAND_MANIFEST))
    need(d.get('date')=='2026-11-21' and
         d.get('source_sha256',{}).get(USB.name)==USB_SHA,
         'NEW_MANIFEST_USB_PIN_BAD')
    print('AUDIT_CANDIDATES_PINNED=1',flush=True)
def audit_state():
    h=sha(trusted(AUDIT))
    if h==OLD_AUDIT_SHA: return 'ORIGINAL'
    if h==NEW_AUDIT_SHA: return 'UPDATED'
    raise Stop('AUDIT_SOURCE_UNEXPECTED_HASH_'+h)
def acquire():
    fd=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and
         not stat.S_IMODE(st.st_mode)&0o077, 'V221_LOCK_UNTRUSTED')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return fd
def write_new(path, data, mode=0o600):
    fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as f:
        os.fchown(f.fileno(),0,0)
        os.fchmod(f.fileno(),mode)
        f.write(data);f.flush();os.fsync(f.fileno())
def atomic_replace(dest, data):
    temp=LAB/('.qdc507-audit-apply-'+str(os.getpid())+'.tmp')
    try:
        write_new(temp,data,0o700)
        os.replace(str(temp),str(dest))
        d=os.open(str(LAB),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if temp.exists():temp.unlink()
def install():
    held=acquire()
    try:
        need(audit_state()=='ORIGINAL','AUDIT_NOT_ORIGINAL')
        need(not STAMP.exists() and not STAMP.is_symlink(),'AUDIT_UPDATE_PREVIOUS_MANIFEST_EXISTS')
        need(not NEW_MANIFEST.exists() and not NEW_MANIFEST.is_symlink(),
             'NEW_MANIFEST_ALREADY_EXISTS')
        # Back up first. New JSON is published before audit to avoid dangling pointer.
        stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
        backup=LAB/('qdc507-memoryfix-v22-audit.before-dsm722-v221-'+stamp+'.py')
        old_audit=trusted(AUDIT)
        write_new(backup,old_audit)
        need(sha(trusted(backup,True))==OLD_AUDIT_SHA,'AUDIT_BACKUP_FAILED')
        status={'format':'qdc507-v221-audit-install-v1','backup_name':backup.name,
                'audit_before':OLD_AUDIT_SHA,'audit_after':NEW_AUDIT_SHA,
                'manifest_after':NEW_JSON_SHA}
        write_new(STAMP,(json.dumps(status,indent=2)+'\n').encode())
        need(sha(trusted(OLD_MANIFEST,True))==OLD_JSON_SHA and
             audit_state()=='ORIGINAL','SOURCES_CHANGED_BEFORE_INSTALL')
        write_new(NEW_MANIFEST,trusted(CAND_MANIFEST),0o600)
        need(sha(trusted(NEW_MANIFEST,True))==NEW_JSON_SHA,'NEW_MANIFEST_WRITE_FAILED')
        atomic_replace(AUDIT,trusted(CAND_AUDIT))
        need(audit_state()=='UPDATED', 'AUDIT_POST_INSTALL_HASH_MISMATCH')
        print('V221_DSM722_AUDIT_PINS_INSTALLED=1',flush=True)
        print('V22_HISTORICAL_20261120_MANIFEST_UNCHANGED=1',flush=True)
        print('NO_CONTAINER_USB_OR_SMS_CHANGES=1',flush=True)
        print('BACKUP='+str(backup),flush=True)
    finally:os.close(held)
def rollback():
    held=acquire()
    try:
        data=json.loads(trusted(STAMP,True))
        need(data.get('format')=='qdc507-v221-audit-install-v1' and
             data.get('audit_before')==OLD_AUDIT_SHA and
             data.get('audit_after')==NEW_AUDIT_SHA and
             data.get('manifest_after')==NEW_JSON_SHA, 'BACKUP_MANIFEST_CHANGED')
        backup=LAB/data['backup_name']
        need(backup.parent==LAB and backup.name.startswith('qdc507-memoryfix-v22-audit.before-dsm722-v221-'),
             'BACKUP_PATH_CHANGED')
        b=trusted(backup,True)
        need(sha(b)==OLD_AUDIT_SHA,'BACKUP_HASH_CHANGED')
        state=audit_state()
        need(state in ('UPDATED','ORIGINAL'),'AUDIT_STATE_UNSAFE')
        need(sha(trusted(OLD_MANIFEST,True))==OLD_JSON_SHA,'HISTORICAL_MANIFEST_DRIFT')
        if state=='UPDATED':atomic_replace(AUDIT,b)
        print('OLD_V22_AUDIT_RESTORED=1',flush=True)
        print('KEEP_HISTORICAL_AND_NEW_MANIFEST_FOR_AUDIT=1',flush=True)
    finally:os.close(held)
def main():
    need(os.geteuid()==0 and LAB.is_dir() and not LAB.is_symlink(),'ROOT_OR_LAB_INVALID')
    need(len(sys.argv)==2 and sys.argv[1] in ('--check','--install','--rollback'),
         'USE_--check_--install_--rollback')
    mode=sys.argv[1]
    if mode=='--rollback':rollback();return
    read_previous();candidate()
    state=audit_state()
    if mode=='--check':
        if state=='UPDATED':
            need(sha(trusted(NEW_MANIFEST,True))==NEW_JSON_SHA,'CURRENT_MANIFEST_CHANGED')
        print('AUDIT_INSTALL_STATE='+state,flush=True)
        print('NO_WRITES_READ_ONLY_CHECK=1',flush=True)
    else:install()
if __name__=='__main__':
    try:main()
    except (Stop,OSError,ValueError,KeyError,UnicodeError,BlockingIOError) as e:
        print('AUDIT_INSTALL_STOP='+type(e).__name__+':'+str(e)[:300],file=sys.stderr)
        print('NO_AUTOMATIC_RETRY=1',file=sys.stderr)
        sys.exit(2)
