#!/usr/bin/python3.8
"""Safely stage QDC507 coldboot v3 beside production v2; never runs recovery."""
import hashlib, os, stat, sys
from pathlib import Path

SRC=Path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-coldboot-production-v3.py')
DST=Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-coldboot-production-v3.py')
V2=DST.with_name('qdc507-coldboot-production-v2.py')
V2_SHA='f5f885e525f177926208bbbd915d3abe583988d32f9862147a02b7a5729e618d'
V3_SHA='0c1e5e6d317cb33e1308a3fcd92378e2c6e1f22d03a6b2878e9dbe8c647e938d'

def need(value,err):
    if not value: raise RuntimeError(err)

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    need(sys.argv[1:] in (['--check'],['--install']), 'USE_CHECK_OR_INSTALL')
    need(os.uname().release=='4.4.180+','KERNEL_DRIFT')
    for p in (SRC,V2):
        s=p.lstat()
        need(stat.S_ISREG(s.st_mode) and not p.is_symlink(),'INPUT_MISSING_OR_SYMLINK_'+p.name)
    need(sha(SRC)==V3_SHA,'V3_FILE_HASH_MISMATCH')
    need(sha(V2)==V2_SHA,'PRODUCTION_V2_HASH_CHANGED')
    d=DST.parent
    s=d.lstat()
    need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not d.is_symlink(),'PRODUCTION_DIR_UNSAFE')
    compile(SRC.read_text(),str(SRC),'exec')
    if DST.exists() or DST.is_symlink():
        need(DST.is_file() and not DST.is_symlink() and sha(DST)==V3_SHA, 'V3_DESTINATION_COLLISION')
        print('V3_ALREADY_INSTALLED_MATCH=1')
    elif sys.argv[1]=='--install':
        fd=os.open(str(DST),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o700)
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(SRC.read_bytes()); f.flush(); os.fsync(f.fileno())
            os.chown(str(DST),0,0)
            os.chmod(str(DST),0o700)
            need(sha(DST)==V3_SHA,'POST_WRITE_VERIFY_FAILED')
        except BaseException:
            # Leave suspect v3 file for review rather than removing evidence.
            raise
        print('V3_INSTALLED=1')
    else:
        print('V3_INSTALL_READY=1')
    print('V2_UNCHANGED=1')
    print('RUNNING_CONTAINER_UNCHANGED=1')
    print('SCHEDULER_UNCHANGED=1')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('V3_INSTALL_STOPPED='+type(e).__name__+':'+str(e))
        sys.exit(2)
