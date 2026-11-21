import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

L = Path('/volume2/docker/asterisk/qdc507-16k-lab')
USB = 'qdc507-usb-recover-memoryfix-v22.sh'
CORE = 'qdc507-coldboot-memoryfix-v221.py'
START = 'qdc507-coldboot-startup-v221.py'
AUDIT = 'qdc507-memoryfix-v22-audit.py'
MASTER = 'qdc507-patch-audit-v3.sh'
CURRENT = 'qdc507-memoryfix-v22-production-20261121-nocoordinator.json'
NAMES = [USB, CORE, START, AUDIT, CURRENT, MASTER]
EXPECTED = {
    USB: '2d38f76958cb362aa789dff16e1c820757586bf66b8c3808e0a5356df69d9d47',
    CORE: 'afb40eeddafd5148ce86504641dbadf8b99f8af560b847709f82dd9985204a4d',
    START: '4a9c80b13bb60fa2d8b28caf3a700642dba3509c18babae66c30830969ee06ab',
    AUDIT: 'cbf113687b1a4d2434b8f422c76be78b592fd09afaea30e1d7a58a4a384b5f5d',
    MASTER: 'f210cbdd033cdb5db522e1e82b38ab504aa3cbe6e7e7314dbaa9f140f07af1d1',
}

def need(test, name):
    if not test:
        raise RuntimeError('STOP_' + name)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def repl(content, old, new, label):
    if isinstance(old, str):
        old = old.encode()
    if isinstance(new, str):
        new = new.encode()
    need(content.count(old) == 1, 'ANCHOR_' + label)
    return content.replace(old, new, 1)

def pins_of(content):
    found=[]
    for item in ast.parse(content.decode()).body:
        if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SOURCE_PINS' for t in item.targets):
            found.append(ast.literal_eval(item.value))
    need(len(found) == 1 and isinstance(found[0], dict), 'PINS_PARSE')
    return found[0]

need(os.geteuid() == 0 and L.is_dir() and not L.is_symlink(), 'ROOT_OR_LAB')
files = {n: L / n for n in NAMES}
old, info = {}, {}
for n,p in files.items():
    st = p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1 and
         not (stat.S_IMODE(st.st_mode) & 0o022), 'FILE_UNTRUSTED_' + n)
    info[n], old[n] = st, p.read_bytes()

for n, expected in EXPECTED.items():
    need(sha(old[n]) == expected, 'SOURCE_DRIFT_' + n)

pins = pins_of(old[AUDIT])
j = json.loads(old[CURRENT])
need(j.get('source_sha256') == pins, 'CURRENT_PINS_MISMATCH')
need(pins.get(USB) == sha(old[USB]), 'USB_PIN_MISMATCH')
for n,h in pins.items():
    need((L/n).is_file() and sha((L/n).read_bytes()) == h,
         'PINNED_FILE_DRIFT_' + n)
need(("CURRENT=SRC/'" + CURRENT + "'").encode() in old[AUDIT], 'CURRENT_MANIFEST_PATH')
need(("NEW_SHA='" + EXPECTED[CORE] + "'").encode() in old[START], 'V221_PIN')
need(("EXPECTED=\"" + EXPECTED[AUDIT] + "\"").encode() in old[MASTER], 'MASTER_PIN')
need(old[CORE].count(b'# QDC507_GUARD16_V2_COLD_BOOT_SYNC_V1_BEGIN\n') == 1 and
     old[CORE].count(b'# QDC507_GUARD16_V2_COLD_BOOT_SYNC_V1_END\n') == 1,
     'COLD_SYNC_BLOCK')
need(all(b'QDC507_GUARD16_ACL600_V1' not in old[n] for n in (USB, CORE)), 'ALREADY_PATCHED')

usb = old[USB]
back = ("    fd = os.open(str(backup),\n"
        "                 os.O_WRONLY | os.O_CREAT | os.O_EXCL |\n"
        "                 os.O_NOFOLLOW, 0o600)\n")
tmp = ("    fd = os.open(str(tmp),\n"
       "                 os.O_WRONLY | os.O_CREAT | os.O_EXCL |\n"
       "                 os.O_NOFOLLOW, 0o600)\n")
usb = repl(usb, back,
           back + "    # QDC507_GUARD16_ACL600_V1: DSM ACL inheritance.\n"
           "    os.fchmod(fd, 0o600)\n"
           "    need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600,\n"
           "         'GUARD16_BACKUP_MODE_UNSAFE')\n", 'USB_BACKUP')
usb = repl(usb, tmp,
           tmp + "    os.fchmod(fd, 0o600)\n"
           "    need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o600,\n"
           "         'GUARD16_TMP_MODE_UNSAFE')\n", 'USB_TMP')
usb = repl(usb,
           "        os.replace(str(tmp), str(old_path))\n",
           "        os.replace(str(tmp), str(old_path))\n"
           "        need(stat.S_IMODE(old_path.lstat().st_mode) == 0o600,\n"
           "             'GUARD16_BASELINE_MODE_UNSAFE')\n", 'USB_FINAL')

core=old[CORE]
cback=("        fd = _os.open(str(backup), _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL\n"
       "                      | _os.O_NOFOLLOW, 0o600)\n")
ctmp=("        fd = _os.open(str(tmp), _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL\n"
      "                      | _os.O_NOFOLLOW, 0o600)\n")
core=repl(core,cback,
          cback + "        # QDC507_GUARD16_ACL600_V1: DSM ACL inheritance.\n"
          "        _os.fchmod(fd, 0o600)\n"
          "        _need(_st.S_IMODE(_os.fstat(fd).st_mode) == 0o600,\n"
          "              'BACKUP_MODE_UNSAFE')\n",'COLD_BACKUP')
core=repl(core,ctmp,
          ctmp + "        _os.fchmod(fd, 0o600)\n"
          "        _need(_st.S_IMODE(_os.fstat(fd).st_mode) == 0o600,\n"
          "              'TMP_MODE_UNSAFE')\n",'COLD_TMP')
core=repl(core,
          "            _os.replace(str(tmp), str(_old_path))\n",
          "            _os.replace(str(tmp), str(_old_path))\n"
          "            _need(_st.S_IMODE(_old_path.lstat().st_mode) == 0o600,\n"
          "                  'BASELINE_MODE_UNSAFE')\n",'COLD_FINAL')

newusb, newcore = sha(usb), sha(core)
start = repl(old[START], EXPECTED[CORE], newcore, 'START_PIN')
audit = repl(old[AUDIT], EXPECTED[USB], newusb, 'AUDIT_PIN')
current = repl(old[CURRENT], EXPECTED[USB], newusb, 'CURRENT_PIN')
master = repl(old[MASTER], EXPECTED[AUDIT], sha(audit), 'MASTER_PIN')
new = {USB:usb, CORE:core, START:start, AUDIT:audit, CURRENT:current, MASTER:master}
need(json.loads(current)['source_sha256'] == pins_of(audit), 'NEW_MANIFEST_PIN')
for n in (CORE, START, AUDIT):
    compile(new[n].decode(), n, 'exec')
for n in (USB, MASTER):
    p = subprocess.run(['/bin/sh','-n'], input=new[n], stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    need(p.returncode == 0, 'SHELL_SYNTAX_'+n)
pieces = usb.split(b"<<'PY'\n",1)
need(len(pieces)==2 and b'\nPY\n' in pieces[1], 'USB_EMBEDDED_PYTHON')
compile(pieces[1].rsplit(b'\nPY\n',1)[0].decode(),USB+':embedded','exec')
need(b'# QDC507_GUARD16_V2_COLD_BOOT_SYNC_V1_BEGIN\n' in core and
     b'# QDC507_GUARD16_V2_COLD_BOOT_SYNC_V1_END\n' in core,'COLD_MARKERS_PRESERVED')
print('ACL600_OFFLINE_PRECHECK=PASS')
print('CURRENT_MANIFEST=' + CURRENT)

counter=[0]
def write_atomic(dest, data, uid, gid, mode):
    counter[0] += 1
    t=L/('.qdc507-acl600-staging-%d-%03d.tmp'%(os.getpid(),counter[0]))
    fd = os.open(str(t), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchown(f.fileno(),uid,gid)
            os.fchmod(f.fileno(),mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        need(sha(t.read_bytes()) == sha(data), 'STAGE_VERIFY_' + dest.name)
        os.replace(str(t),str(dest))
        need(sha(dest.read_bytes()) == sha(data), 'INSTALL_VERIFY_' + dest.name)
    finally:
        if os.path.lexists(str(t)):
            os.unlink(str(t))

stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
for n in NAMES:
    dest=L/(n+'.before-acl600-'+stamp)
    need(not os.path.lexists(str(dest)),'BACKUP_EXISTS_'+n)
    write_atomic(dest,old[n],info[n].st_uid,info[n].st_gid,0o600)
print('ACL600_BACKUPS_VERIFIED=6')
installed=[]
try:
    for n in (CURRENT, AUDIT, USB, CORE, START, MASTER):
        need(sha(files[n].read_bytes()) == sha(old[n]), 'CONCURRENT_CHANGE_'+n)
        installed.append(n)
        write_atomic(files[n],new[n],info[n].st_uid,info[n].st_gid,stat.S_IMODE(info[n].st_mode))
    print('ACL600_PATCH_INSTALLED=1')
    print('USB_SHA256='+newusb)
    print('V221_CORE_SHA256='+newcore)
    for cmd in (
        ['/bin/sh',str(files[MASTER]),'--deep'],
        ['/usr/bin/python3.8','-I','-B',str(files[START]),'--check'],
    ):
        r=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT,universal_newlines=True,timeout=400)
        print(r.stdout,flush=True)
        need(r.returncode==0,'POST_AUDIT_FAILED_'+cmd[-2].split('/')[-1])
    print('ACL600_PATCH_AND_READONLY_AUDITS=PASS')
    print('NO_RECOVER_NO_BOOT_NO_SERVICE_RESTART=1')
except BaseException:
    for n in reversed(installed):
        write_atomic(files[n],old[n],info[n].st_uid,info[n].st_gid,stat.S_IMODE(info[n].st_mode))
    print('ACL600_PATCH_ROLLED_BACK=1')
    raise
