#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed QDC507 v43 inbound release final, original DS918+ deployment only.

--selftest : fully offline source sanity checks, no NAS access.
--prepare  : read-only production, stage proposed entrypoint + boot hook into LAB.
--apply    : gated, same-container v43 install; no restart, no Guard16/SMS edits.
--status   : read-only installed state checks.
--verify   : read-only full deployed v43 status, device SHA and Guard16 v2.
--rollback : gated v43 -> pinned v42 dialplan, entrypoint and USB wrapper.

This script is NOT portable. Validated only with pinned source files for Nov 15 2026.
Do NOT use with recovery pending journals, active calls, replaced Docker container,
or after unrelated production changes. Do not change USB or Docker image here.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

LAB=Path('/volume2/docker/asterisk/qdc507-16k-lab')
BASE=Path('/volume2/docker/asterisk/qdc507-current')
C='qdc507-hardware'
R='/opt/qdc16k-preaudio-manual'
EP='/usr/local/bin/entrypoint.sh'
USB=BASE/'recovery/qdc507-usb-recover.sh'
OLD_DP=None  # Initialized from the exact hash-pinned v43 LAB preview, never from arbitrary live bytes.
NEW_DP='0f232e3c356922392169a099a966b39b93c659136bdc424c27f963cae1f386e3'
NEW_WRAP='472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98'
HOST_OLD='7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7'
HOST_NEW='13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf'
DEV_OLD='3682a8f0b4160f43778624e8551f39f8390163c92850f6661980ca64038d5f76'
DEV_NEW='ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1'
PREP_HASH='1332d6fcf5a4b3f4f7a70180e8632992e30325256b415a4f8fc6417cf7bc0665'
EP_ANCHOR=b'    /usr/bin/python3 -B /opt/qdc16k-preaudio-manual/prepare.py --boot\n'
EP_LINE=b'    /usr/bin/python3 -B /opt/qdc16k-preaudio-manual/qdc507-v43-device-restore.py --boot\n'
LOCKS=(
 '/run/qdc507-coldboot-v2.lock',
 '/run/qdc507-hotplug-coordinator.lock',
 '/run/qdc507-dynusb-oneclick.lock',
 '/run/qdc507-dynusb-mode.lock',
 '/run/qdc507-guard16-v2-runtime-restore.lock',
 '/run/qdc507-usb-sim-swap-v2.lock',
 '/run/qdc507-v4-usb.lock',
 '/run/qdc507-v4-boot.lock',
 '/run/qdc507-coldboot-v3.lock',
)
PENDING=(
 'qdc507-usb-recovery-v4-pending.json',
 'qdc507-dynusb-mode-switch-pending.json',
 'qdc507-dynusb-cutover-pending-v1.json',
 'qdc507-dynusb-oneclick-pending-v1.json',
)
BACK_EP=LAB/'qdc507-v43-v42-entrypoint.private.bak'
BACK_USB=LAB/'qdc507-v43-v42-usb-recover.private.bak'
BACK_DP=LAB/'qdc507-v43-v42-extensions.private.bak'
STAGE_EP=LAB/'qdc507-v43-entrypoint.staged.sh'
STAGE_USB=LAB/'qdc507-v43-usb-recover.staged.sh'
STAGE_BOOT=LAB/'qdc507-v43-device-restore.final.staged.py'
APPLIED=LAB/'qdc507-v43-release.applied.json'
JOURNAL=LAB/'qdc507-v43-release.pending.json'
V43_REMOTE={
 'preaudio-inbound-v43.py':(LAB/'qdc507-inbound-v43-candidate.py',HOST_NEW,0o644),
 'agi-wrapper-inbound-v43':(LAB/'qdc507-v43-wrapper.staged',NEW_WRAP,0o755),
 'device-v43.sh':(LAB/'qdc507-device-v43-candidate.sh',DEV_NEW,0o600),
}

BOOT_HELPER=r"""#!/usr/bin/env python3
# Stage pinned QDC507 v43 device helper ONLY after original prepare.py --boot.
import hashlib,os,re,subprocess,sys
from pathlib import Path
R=Path('/opt/qdc16k-preaudio-manual')
A='/usr/local/bin/adb'
SHA='ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1'
WRAP_SHA='472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98'
HOST_SHA='13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf'
D='/tmp/qdc16k-preaudio-manual'
class Stop(Exception):pass
def need(ok,msg):
 if not ok:raise Stop(msg)
def run(args,limit=60):
 p=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=limit)
 need(p.returncode==0,'ADB_OR_CMD_FAILED_'+str(args[0]))
 return p.stdout.decode('utf-8','replace').replace('\r','').strip()
def main():
 need(sys.argv[1:]==['--boot'],'ONLY_--boot')
 need(Path('/etc/qdc507-mode/current').read_text().strip()=='16k','NOT_16K')
 source=R/'device-v43.sh'; wrapper=R/'agi-wrapper-inbound-v43'
 need(source.is_file() and not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest()==SHA,'V43_SOURCE_HASH_MISMATCH')
 need(wrapper.is_file() and not wrapper.is_symlink() and hashlib.sha256(wrapper.read_bytes()).hexdigest()==WRAP_SHA,'V43_WRAPPER_HASH_CHANGED')
 host=R/'preaudio-inbound-v43.py'
 need(host.is_file() and not host.is_symlink() and hashlib.sha256(host.read_bytes()).hexdigest()==HOST_SHA,'V43_HOST_HASH_CHANGED')
 serial=re.findall(r'\bQDC_SERIAL=([A-Za-z0-9._:-]+)',wrapper.read_text())
 need(len(serial)==1,'QDC_SERIAL_NOT_UNIQUE')
 base=[A,'-H','127.0.0.1','-P','5037','-s',serial[0]]
 check='''set -eu
D=/tmp/qdc16k-preaudio-manual
[ ! -L "$D" ]; [ ! -L "$D/state" ]; [ ! -L "$D/device-v43.sh" ]; [ ! -L "$D/device-v43.sh.upload" ]
[ -d "$D/state" ]; [ ! -e "$D/state/owner" ]; [ ! -e "$D/state/lock" ]
[ ! -L "$D/state/owner" ]; [ ! -L "$D/state/lock" ]
if [ -e "$D/device-v43.sh" ]; then
 [ -f "$D/device-v43.sh" ]; [ -x "$D/device-v43.sh" ]
 [ "$(sha256sum "$D/device-v43.sh" | cut -d ' ' -f1)" = "'''+SHA+'''" ]
 echo V43_DEVICE_READY
else
 if [ -e "$D/device-v43.sh.upload" ]; then
  [ -f "$D/device-v43.sh.upload" ]
  [ "$(sha256sum "$D/device-v43.sh.upload" | cut -d ' ' -f1)" = "'''+SHA+'''" ]
  echo V43_UPLOAD_VERIFIED
 else
  echo V43_DEVICE_MISSING
 fi
fi'''
 state=run(base+['shell',check],35)
 if state=='V43_DEVICE_READY':
  print('V43_DEVICE_READY=1');print('NO_DEVICE_WRITE=1');return
 need(state in ('V43_DEVICE_MISSING','V43_UPLOAD_VERIFIED'),'UNEXPECTED_DEVICE_STATE_'+state[-90:])
 if state=='V43_DEVICE_MISSING':
  run(base+['push',str(source),D+'/device-v43.sh.upload'],70)
 finish='''set -eu
D=/tmp/qdc16k-preaudio-manual
[ ! -e "$D/state/owner" ]; [ ! -e "$D/state/lock" ]; [ ! -L "$D/state/owner" ]; [ ! -L "$D/state/lock" ]
[ ! -L "$D/device-v43.sh.upload" ]; [ -f "$D/device-v43.sh.upload" ]; [ ! -e "$D/device-v43.sh" ]; [ ! -L "$D/device-v43.sh" ]
[ "$(sha256sum "$D/device-v43.sh.upload" | cut -d ' ' -f1)" = "'''+SHA+'''" ]
chmod 500 "$D/device-v43.sh.upload"
mv "$D/device-v43.sh.upload" "$D/device-v43.sh"
[ -x "$D/device-v43.sh" ]
[ "$(sha256sum "$D/device-v43.sh" | cut -d ' ' -f1)" = "'''+SHA+'''" ]
echo V43_DEVICE_INSTALLED'''
 need(run(base+['shell',finish],35)=='V43_DEVICE_INSTALLED','DEVICE_READBACK_NOT_ACKNOWLEDGED')
 print('V43_DEVICE_READY=1');print('NEW_DEVICE_STAGE_VERIFIED=1')
 if state=='V43_UPLOAD_VERIFIED':print('RESUMED_VERIFIED_UPLOAD=1')
if __name__=='__main__':
 try:main()
 except (Stop,OSError,ValueError,subprocess.TimeoutExpired) as e:
  print('V43_BOOT_STAGE_STOP='+str(e)[:180],file=sys.stderr);sys.exit(2)
"""

class Halt(RuntimeError):pass
def need(ok,msg):
 if not ok:raise Halt(msg)
def sha(data):return hashlib.sha256(data).hexdigest()
def bfile(p,expected=None):
 need(p.is_file() and not p.is_symlink(),'FILE_MISSING_OR_SYMLINK_'+str(p))
 raw=p.read_bytes()
 if expected:need(sha(raw)==expected,'SHA_CHANGED_'+p.name)
 return raw
def call(args,timeout=25,inp=None):
 p=subprocess.run(list(map(str,args)),input=inp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 need(p.returncode==0,'COMMAND_FAILED_'+str(args[:3])+'_'+p.stderr.decode('utf8','replace')[-120:])
 return p.stdout
def dc(path):return call(['docker','exec',C,'cat',path])
def ds(path):return sha(dc(path))
def docker_copy(local,remote):
 call(['docker','cp',str(local),C+':'+str(remote)],35)
def exact_copy(local,remote,expected,mode):
 raw=bfile(local,expected)
 # The destination is a pinned literal path. Distinguish missing from unreadable
 # or other errors; never overwrite an unknown or symlinked existing path.
 test='[ ! -L '+remote+' ] && { if [ -e '+remote+' ]; then echo PRESENT; else echo ABSENT; fi; }'
 state=call(['docker','exec',C,'/bin/sh','-c',test]).decode('utf8','replace').strip()
 need(state in ('PRESENT','ABSENT'),'REMOTE_FILE_STATE_UNKNOWN_'+remote)
 if state=='PRESENT':
  need(dc(remote)==raw,'REMOTE_ALREADY_DIFFERENT_'+remote)
 else:
  docker_copy(local,remote)
 call(['docker','exec','-u','0',C,'chmod',oct(mode)[2:],remote])
 need(ds(remote)==expected,'REMOTE_READBACK_WRONG_'+remote)

def create_once(path,data,mode=0o600):
 need(path.parent==LAB,'OUTSIDE_LAB')
 if path.exists() or path.is_symlink():
  need(path.is_file() and not path.is_symlink() and path.read_bytes()==data,'EXISTING_STAGE_OR_BACKUP_DIFFERS_'+path.name)
  return
 with path.open('xb') as f:
  f.write(data);f.flush();os.fsync(f.fileno())
 os.chmod(str(path),mode)

def check_pending_and_mode():
 need(LAB.is_dir() and not LAB.is_symlink(),'LAB_NOT_DIRECTORY')
 for n in PENDING:need(not (LAB/n).exists() and not (LAB/n).is_symlink(),'RECOVERY_PENDING_'+n)
 need((LAB/'qdc507-mode-upgrade-c3e-preaudio-v2/mode/current').read_text().strip()=='16k','NOT_16K_MODE')
 got=json.loads(call(['docker','inspect',C]))
 need(len(got)==1 and got[0]['State']['Running'],'CONTAINER_NOT_RUNNING')
 base=json.loads(bfile(LAB/'qdc507-dynusb-oneclick-baseline-v1.json'))
 need(got[0]['Id']==base['container_id'] and got[0]['Image']==base['image_id'],'CONTAINER_ID_OR_IMAGE_DRIFT')
 mount={m['Destination']:m['Source'] for m in got[0]['Mounts']}
 need(mount.get('/etc/asterisk')==str(BASE/'v0'),'DIALPLAN_MOUNT_MISMATCH')
 need(ds(R+'/prepare.py')==PREP_HASH,'PREPARE_SHA_CHANGED')
 need(ds(R+'/preaudio-inbound-v42.py')==HOST_OLD,'V42_HOST_SOURCE_CHANGED')
 need(ds(R+'/device.sh')==DEV_OLD,'V42_HOST_DEVICE_SOURCE_CHANGED')
 return got[0]

def idle():
 out=call(['docker','exec',C,'asterisk','-rx','core show channels count']).decode('utf8','replace')
 need(re.search(r'^0 active channels\s*$',out,re.M) and re.search(r'^0 active calls\s*$',out,re.M),'ACTIVE_OR_UNKNOWN_CALLS')
 line=call(['docker','exec',C,'asterisk','-rx','core show channels concise']).decode('utf8','replace')
 for s in line.splitlines():
  if s.strip() and not s.startswith('Message/ast_msg_queue') and not re.match(r'^\s*\d+\s+(?:active\s+channels?|active\s+calls?|calls?\s+processed)',s,re.I):
   raise Halt('UNKNOWN_ACTIVE_CHANNEL_'+s[:75])
 out=call(['docker','exec',C,'asterisk','-rx','quectel show device state quectel0']).decode('utf8','replace')
 need(re.search(r'^\s*State\s*:\s*Free\s*$',out,re.M),'MODEM_NOT_FREE')

def lock_recovery():
 held=[]
 try:
  for name in LOCKS:
   p=Path(name)
   # Locks are created only at /run and never in /root,/usr, or NAS system packages.
   fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
   st=os.fstat(fd)
   need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not stat.S_IMODE(st.st_mode)&0o077,'UNTRUSTED_RECOVERY_LOCK_'+p.name)
   try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise Halt('RECOVERY_IN_PROGRESS_'+p.name)
   held.append(fd)
  return held
 except Exception:
  for fd in held:os.close(fd)
  raise

def release(held):
 for fd in reversed(held):os.close(fd)

def write_atomic(target,data):
 need(target.is_file() and not target.is_symlink(),'TARGET_CHANGED_'+str(target))
 original=target.stat()
 fd,tmp=tempfile.mkstemp(prefix='.qdc507-v43-',dir=str(target.parent))
 try:
  with os.fdopen(fd,'wb') as f:
   f.write(data);f.flush();os.fsync(f.fileno())
  shutil.copystat(str(target),tmp)
  os.chown(tmp,original.st_uid,original.st_gid)
  os.replace(tmp,str(target))
  dr=os.open(str(target.parent),os.O_RDONLY)
  try:os.fsync(dr)
  finally:os.close(dr)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def stage_derived_v42_pin():
 """Recover the exact source v42 dialplan from the pinned, reversible v43 preview.

 This accommodates an existing live dialplan with current cloud-trunk choices,
 without trusting unknown live bytes or disabling exact SHA checking.  The
 preview itself is verified against the v43 pin generated by the earlier plan.
 """
 staged=bfile(LAB/'qdc507-v43-extensions.staged.conf',NEW_DP)
 before=b'agi-wrapper-inbound-v42'
 after=b'agi-wrapper-inbound-v43'
 need(staged.count(after)==6 and staged.count(before)==0,
      'STAGED_V43_WRAPPER_REFERENCES_CHANGED')
 recovered=staged.replace(after,before)
 need(recovered.count(before)==6 and recovered.replace(before,after)==staged,
      'STAGED_DIALPLAN_NOT_REVERSIBLE')
 return sha(recovered)

def sha_pair(expected):
 live=BASE/'v0/extensions.conf'
 edit=BASE/'v0-edit/extensions.conf'
 livehash=sha(bfile(live))
 edithash=sha(bfile(edit))
 need(livehash==edithash,'DIALPLAN_LIVE_EDIT_DIVERGED')
 need(livehash==expected,'DIALPLAN_NOT_EXACT_PINNED_BASELINE_EXPECTED_'+expected[:16]+'_GOT_'+livehash[:16])
 return live,edit

def expected_boot():
 cur=dc(EP)
 need(cur.count(EP_ANCHOR)==1 and cur.count(EP_LINE)==0,'ENTRYPOINT_ANCHOR_NOT_UNIQUE')
 need(cur.startswith(b'#!/bin/bash'),'ENTRYPOINT_NOT_BASH')
 need(cur.count(b'exec "$@"')==1,'ENTRYPOINT_EXEC_MISMATCH')
 candidate=cur.replace(EP_ANCHOR,EP_ANCHOR+EP_LINE)
 need(candidate.count(EP_LINE)==1,'BOOT_HOOK_NOT_UNIQUE')
 call(['docker','exec','-i',C,'/bin/bash','-n'],timeout=15,inp=candidate)
 return cur,candidate

def usb_derivative(original):
 """Patch only the MANUAL USB/SIM entrypoint, leaving pinned oneclick/hotplug intact.

 The single preexisting success marker is immediately before the return, after
 voice/Guard16/SMS verification. On USB recovery with no container restart,
 install/verify the missing v43 device helper before success is reported.
 """
 need(original.startswith(b'#!/bin/sh\n') and original.count(b"exec /usr/bin/python3.8 -I -B -") == 1,
      'USB_WRAPPER_FORMAT_UNEXPECTED')
 need(original.count(b"PREAUDIO_SHA = '")==1 and
      PREP_HASH.encode() in original and
      b'ONECLICK_RECOVERY_COMPLETE=1' in original and
      b'GUARD16_V2_FINAL_STATE_NOT_VERIFIED' in original,
      'USB_WRAPPER_RECOVERY_LOGIC_CHANGED')
 anchor=b"        emit('USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1')\n"
 need(original.count(anchor)==1,'USB_RECOVERY_SUCCESS_ANCHOR_NOT_UNIQUE')
 insert=b"""        # V43_POST_USB_RESTAGE_V1_BEGIN: oneclick can complete without restarting Docker.
        # Only after full original oneclick/Guard16/voice/SMS verification above.
        emit('V43_POST_USB_RESTAGE_START=1')
        checked = command(['docker','exec','-u','0',CONTAINER,
                           '/usr/bin/python3','-B',
                           '/opt/qdc16k-preaudio-manual/qdc507-v43-device-restore.py',
                           '--boot'], timeout=110)
        need('V43_DEVICE_READY=1' in checked.stdout,
             'V43_USB_DEVICE_RESTAGE_NOT_VERIFIED')
        emit('V43_POST_USB_RESTAGE_OK=1')
        # V43_POST_USB_RESTAGE_V1_END
"""
 proposal=original.replace(anchor,insert+anchor,1)
 need(proposal.replace(insert,b'',1)==original,'USB_STAGE_NOT_REVERSIBLE')
 # sh -n checks all source + heredoc termination, and Python compile checks
 # the modified embedded Python 3.8 logic independently.
 subprocess.run(['/bin/sh','-n'],input=proposal,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 sep=b"<<'PY'\n"
 need(proposal.count(sep)==1,'USB_PY_HEREDOC_MISSING')
 py=proposal.split(sep,1)[1].rsplit(b'\nPY',1)[0]
 compile(py,'qdc507-usb-recover.py','exec')
 return proposal

def check_auto_watch():
 """Fail closed when a running hotplug --watch can bypass the manual USB hook.

 Inspect procfs directly: Synology BusyBox ps options vary by DSM build.
 Missing or unreadable process command lines are skipped, not interpreted as
 affirmative proof that the watch service is absent.
 """
 need(Path('/proc').is_dir(),'PROC_UNAVAILABLE_FOR_WATCH_CHECK')
 for process in Path('/proc').iterdir():
  if not process.name.isdigit():continue
  try:argv=(process/'cmdline').read_bytes().split(b'\0')
  except (OSError,PermissionError):continue
  if (any(b'qdc507-hotplug-recovery-v1.py' in arg for arg in argv) and
      b'--watch' in argv):
   raise Halt('UNHOOKED_HOTPLUG_AUTO_WATCH_RUNNING_STOP_DEPLOYMENT')

def staged():
 need(bfile(LAB/'qdc507-v43-wrapper.staged',NEW_WRAP) is not None,'WRAPPER_NOT_STAGED')
 need(bfile(LAB/'qdc507-v43-extensions.staged.conf',NEW_DP) is not None,'DIALPLAN_NOT_STAGED')
 for _,(p,hash_,__) in V43_REMOTE.items():bfile(p,hash_)
 boot=BOOT_HELPER.encode()
 compile(boot,'qdc507-v43-device-restore.py','exec')
 return boot

def selftest():
 import ast
 ast.parse(BOOT_HELPER)
 compile(BOOT_HELPER,'qdc507-v43-device-restore.py','exec')
 original=b'#!/bin/bash\n'+EP_ANCHOR+b'fi\nexec "$@"\n'
 need(original.count(EP_ANCHOR)==1,'SELFTEST_ENTRYPOINT_ANCHOR')
 proposal=original.replace(EP_ANCHOR,EP_ANCHOR+EP_LINE)
 need(proposal.count(EP_LINE)==1 and proposal.replace(EP_LINE,b'')==original,
      'SELFTEST_ENTRYPOINT_ROUNDTRIP')
 need(len(HOST_NEW)==64 and len(DEV_NEW)==64,'SELFTEST_PINS')
 print('V43_RELEASE_OFFLINE_SELFTEST_OK=1')
 print('NO_NAS_OR_DOCKER_ACCESS=1')

def prepare():
 check_pending_and_mode();need(not JOURNAL.exists(),'EXISTING_RELEASE_PENDING')
 need(not APPLIED.exists(),'ALREADY_APPLIED')
 idle()
 check_auto_watch()
 sha_pair(OLD_DP)
 need(ds(R+'/agi-wrapper-inbound-v42')!=NEW_WRAP,'V42_WRAPPER_UNEXPECTED')
 boot=staged();original,candidate=expected_boot()
 oldusb=bfile(USB)
 newusb=usb_derivative(oldusb)
 create_once(BACK_USB,oldusb)
 create_once(STAGE_USB,newusb)
 create_once(BACK_EP,original)
 create_once(BACK_DP,bfile(BASE/'v0/extensions.conf',OLD_DP))
 create_once(STAGE_EP,candidate)
 create_once(STAGE_BOOT,boot)
 print('V43_PREPARE_OK=1')
 print('V42_ENTRYPOINT_SHA256='+sha(original))
 print('V43_ENTRYPOINT_CANDIDATE_SHA256='+sha(candidate))
 print('V43_DEVICE_RESTORER_SHA256='+sha(boot))
 print('V42_USB_WRAPPER_SHA256='+sha(oldusb))
 print('V43_USB_WRAPPER_CANDIDATE_SHA256='+sha(newusb))
 print('V43_DIALPLAN_CANDIDATE_SHA256='+NEW_DP)
 print('RECOVERY_HOOK_AFTER_ORIGINAL_PREPARE=1')
 print('USB_RECOVERY_RESTAGER_VERIFIED=1')
 print('GUARD16_AND_PREPARE_UNCHANGED=1')
 print('NO_PRODUCTION_WRITES=1')

def read_entrypoint_current():
 return dc(EP)

def status():
 check_pending_and_mode()
 live=sha(bfile(BASE/'v0/extensions.conf'))
 entry=sha(read_entrypoint_current())
 original=bfile(BACK_EP) if BACK_EP.is_file() else b''
 hooked=bfile(STAGE_EP) if STAGE_EP.is_file() else b''
 print('DIALPLAN='+('v43' if live==NEW_DP else 'v42' if live==OLD_DP else 'UNKNOWN'))
 print('ENTRYPOINT='+('v43' if hooked and sha(hooked)==entry else 'v42' if original and sha(original)==entry else 'UNKNOWN'))
 curusb=sha(bfile(USB))
 oldusb=bfile(BACK_USB) if BACK_USB.is_file() else b''
 newusb=bfile(STAGE_USB) if STAGE_USB.is_file() else b''
 print('USB_WRAPPER='+('v43' if newusb and sha(newusb)==curusb else 'v42' if oldusb and sha(oldusb)==curusb else 'UNKNOWN'))
 print('RELEASE_RECEIPT='+('present' if APPLIED.exists() else 'absent'))
 print('RELEASE_JOURNAL='+('present' if JOURNAL.exists() else 'absent'))
 print('NO_PRODUCTION_WRITES=1')

def verify():
 """Read-only production validation after a successful --apply.

 Always require idle for the ADB and Guard16 probes; cannot establish audio
 quality, call-setup time or real USB reset behaviour without actual tests.
 """
 check_pending_and_mode()
 need(APPLIED.is_file() and not APPLIED.is_symlink() and not JOURNAL.exists(),
      'NO_COMPLETE_V43_RELEASE_RECEIPT')
 j=json.loads(bfile(APPLIED))
 need(j.get('phase')=='applied' and j.get('new_dialplan_sha256')==NEW_DP,
      'RECEIPT_NOT_V43')
 held=lock_recovery()
 try:
  idle()
  check_auto_watch()
  sha_pair(NEW_DP)
  need(bfile(USB)==bfile(STAGE_USB,j['new_usb_sha256']),
       'USB_WRAPPER_NOT_PINNED_V43')
  need(dc(EP)==bfile(STAGE_EP,j['new_ep_sha256']),
       'ENTRYPOINT_NOT_PINNED_V43')
  for name,(local,expected,mode) in V43_REMOTE.items():
   need(ds(R+'/'+name)==expected,'V43_CONTAINER_SHA_BAD_'+name)
  need(ds(R+'/qdc507-v43-device-restore.py')==sha(BOOT_HELPER.encode()),
       'V43_RESTAGER_HASH_BAD')
  verify_dialplan('v43')
  wrapper=dc(R+'/agi-wrapper-inbound-v43').decode('utf-8','replace')
  serial=re.findall(r'\bQDC_SERIAL=([A-Za-z0-9._:-]+)',wrapper)
  need(len(serial)==1,'SERIAL_NOT_UNIQUE_IN_PINNED_WRAPPER')
  output=call(['docker','exec',C,'/usr/local/bin/adb','-H','127.0.0.1','-P','5037',
               '-s',serial[0],'shell',
               'set -eu; sha256sum /tmp/qdc16k-preaudio-manual/device-v43.sh; '
               'test ! -e /tmp/qdc16k-preaudio-manual/state/owner; '
               'test ! -e /tmp/qdc16k-preaudio-manual/state/lock'],40)
  need(re.search(DEV_NEW.encode()+rb'\s+/tmp/qdc16k-preaudio-manual/device-v43.sh',output),
       'DEVICE_V43_READBACK_SHA_WRONG')
  print('DEVICE_V43_SHA_AND_OWNER_LOCK_IDLE_OK=1')
  p=call(['/usr/bin/python3.8','-I','-B',
          LAB/'qdc507-guard16-v2-runtime-restore-v2.py','--check'],115)
  need(b'READ_ONLY_RUNTIME_CHECK_OK=1' in p and b'DEVICE_CURRENT_MODE=v2' in p and
       b'DEVICE_V1_BACKUP=present' in p,
       'GUARD16_V2_RECOVERY_CHECK_FAILED')
  print('GUARD16_V2_READ_ONLY_CHECK_OK=1')
  print('V43_PRODUCTION_READ_ONLY_VERIFIED=1')
 finally:release(held)

def verify_dialplan(variant):
 dp=call(['docker','exec',C,'asterisk','-rx','dialplan show incoming-mobile']).decode('utf8','replace')
 marker='agi-wrapper-inbound-v'+('43' if variant=='v43' else '42')
 need(dp.count(marker)==4,'LOADED_INBOUND_WRAPPER_MISMATCH_'+variant)
 for ctx in ('qdc16k-inbound-v42-wait','qdc16k-inbound-v42-cancel'):
  text=call(['docker','exec',C,'asterisk','-rx','dialplan show '+ctx]).decode('utf8','replace')
  need(marker in text,'LOADED_CONTEXT_WRAPPER_MISMATCH_'+ctx)
 print('COMPILED_INBOUND_'+variant.upper()+'_ROUTES_OK=1')

def apply():
 check_pending_and_mode();need(not JOURNAL.exists(),'RELEASE_JOURNAL_EXISTS_STOP')
 need(not APPLIED.exists(),'ALREADY_APPLIED')
 held=lock_recovery()
 try:
  idle()
  check_auto_watch()
  sha_pair(OLD_DP)
  original,candidate=expected_boot()
  boot=staged()
  oldusb=bfile(BACK_USB)
  newusb=bfile(STAGE_USB)
  need(newusb==usb_derivative(oldusb),'USB_PREPARED_PATCH_CHANGED')
  need(bfile(USB)==oldusb,'USB_WRAPPER_CHANGED_SINCE_PREPARE')
  need(bfile(BACK_EP)==original and bfile(BACK_DP,OLD_DP),'BACKUP_MISMATCH')
  need(bfile(STAGE_EP)==candidate,'ENTRYPOINT_STAGE_CHANGED')
  need(bfile(STAGE_BOOT)==boot,'BOOT_HELPER_STAGE_CHANGED')
  verify_dialplan('v42')
  journal={'phase':'started','old_usb_sha256':sha(oldusb),'new_usb_sha256':sha(newusb),
           'old_ep_sha256':sha(original),'new_ep_sha256':sha(candidate),
           'old_dialplan_sha256':OLD_DP,'new_dialplan_sha256':NEW_DP}
  create_once(JOURNAL,(json.dumps(journal,sort_keys=True)+'\n').encode())
  try:
   for name,(local,expected,mode) in V43_REMOTE.items():exact_copy(local,R+'/'+name,expected,mode)
   exact_copy(STAGE_BOOT,R+'/qdc507-v43-device-restore.py',sha(boot),0o644)
   output=call(['docker','exec','-u','0',C,'/usr/bin/python3','-B',R+'/qdc507-v43-device-restore.py','--boot'],95)
   need(b'V43_DEVICE_READY=1' in output,'LIVE_V43_DEVICE_STAGE_NOT_ACKNOWLEDGED')
   print('DEVICE_V43_ACTIVE_CANDIDATE_STAGED=1')
   idle()
   need(ds(EP)==sha(original),'ENTRYPOINT_CHANGED_JUST_BEFORE_WRITE')
   docker_copy(STAGE_EP,EP)
   call(['docker','exec','-u','0',C,'chmod','755',EP])
   need(ds(EP)==sha(candidate),'NEW_ENTRYPOINT_NOT_VERIFIED')
   # USB restage covers the oneclick branch that does not restart this container.
   need(bfile(USB)==oldusb,'USB_WRAPPER_CHANGED_JUST_BEFORE_WRITE')
   write_atomic(USB,newusb)
   need(sha(bfile(USB))==sha(newusb),'USB_WRAPPER_READBACK_WRONG')
   # Dialplan is last. Keep the same dynamic cloud-trunk and original outbound route.
   idle()
   live,edit=sha_pair(OLD_DP)
   new=bfile(LAB/'qdc507-v43-extensions.staged.conf',NEW_DP)
   write_atomic(edit,new)
   write_atomic(live,new)
   need(sha_pair(NEW_DP),'POSTCOPY_DIALPLAN_BAD')
   call(['docker','exec',C,'asterisk','-rx','dialplan reload'],20)
   verify_dialplan('v43')
   receipt={'phase':'applied','old_usb_sha256':sha(oldusb),'new_usb_sha256':sha(newusb),
            'old_ep_sha256':sha(original),'new_ep_sha256':sha(candidate),
            'old_dialplan_sha256':OLD_DP,'new_dialplan_sha256':NEW_DP,
            'boot_helper_sha256':sha(boot),'container':C}
   create_once(APPLIED,(json.dumps(receipt,sort_keys=True)+'\n').encode())
   JOURNAL.unlink()
   print('V43_APPLIED=1')
   print('INBOUND_ONLY_OUTBOUND_V42_UNCHANGED=1')
   print('ORIGINAL_PREPARE_AND_GUARD16_V2_UNCHANGED=1')
   print('USB_MANUAL_RECOVERY_V43_HOOKED=1')
   print('NO_DOCKER_OR_USB_RESTART=1')
  except Exception:
   print('V43_APPLY_INCOMPLETE_STOP_AND_REVIEW_JOURNAL=1',file=sys.stderr)
   print('DO_NOT_REBOOT_UNTIL_RELEASE_JOURNAL_RESOLVED=1',file=sys.stderr)
   print('AFTER_REVIEW_RUN_V43_RELEASE_FINAL_--abort_WHEN_IDLE=1',file=sys.stderr)
   raise
 finally:release(held)

def abort():
 """Resolve an interrupted v43 rollout safely back to the pinned v42 baseline.

 This intentionally does not touch the device; v43 device files are inert when
 the exact v42 dialplan and original entrypoint are restored. Never remove an
 unknown device owner, lock, or pending ADB operation. Only known local hashes
 are accepted. Must be called deliberately while calls and modem are idle.
 """
 check_pending_and_mode()
 need(JOURNAL.exists() or APPLIED.exists(), 'NO_RELEASE_EVIDENCE_TO_RECOVER')
 need(not JOURNAL.is_symlink() and not APPLIED.is_symlink(),'RECOVERY_EVIDENCE_SYMLINK')
 old=bfile(BACK_DP,OLD_DP)
 new=bfile(LAB/'qdc507-v43-extensions.staged.conf',NEW_DP)
 oldusb=bfile(BACK_USB)
 newusb=bfile(STAGE_USB)
 need(newusb==usb_derivative(oldusb),'USB_RECOVERY_STAGED_DIFF_CHANGED')
 original=bfile(BACK_EP)
 candidate=bfile(STAGE_EP)
 need(candidate==original.replace(EP_ANCHOR,EP_ANCHOR+EP_LINE), 'RECOVERY_ENTRYPOINT_NOT_DERIVED')
 if JOURNAL.exists():
  j=json.loads(bfile(JOURNAL))
  need(j.get('old_dialplan_sha256')==OLD_DP and j.get('new_dialplan_sha256')==NEW_DP and
       j.get('old_usb_sha256')==sha(oldusb) and j.get('new_usb_sha256')==sha(newusb) and
       j.get('old_ep_sha256')==sha(original) and j.get('new_ep_sha256')==sha(candidate),
       'UNRECOGNIZED_JOURNAL_STOP')
 if APPLIED.exists():
  r=json.loads(bfile(APPLIED))
  need(r.get('old_dialplan_sha256')==OLD_DP and r.get('new_dialplan_sha256')==NEW_DP and
       r.get('old_usb_sha256')==sha(oldusb) and r.get('new_usb_sha256')==sha(newusb) and
       r.get('old_ep_sha256')==sha(original) and r.get('new_ep_sha256')==sha(candidate),
       'UNRECOGNIZED_RECEIPT_STOP')
 held=lock_recovery()
 try:
  idle()
  live=BASE/'v0/extensions.conf'; edit=BASE/'v0-edit/extensions.conf'
  for p in (live,edit):
   need(sha(bfile(p)) in (OLD_DP,NEW_DP),'DIALPLAN_UNKNOWN_REFUSE_RECOVERY_'+str(p))
  cur=dc(EP)
  need(cur in (original,candidate),'ENTRYPOINT_UNKNOWN_REFUSE_RECOVERY')
  need(bfile(USB) in (oldusb,newusb),'USB_WRAPPER_UNKNOWN_REFUSE_RECOVERY')
  # Revert dialplan before removing v43 boot hook; v42 device remains installed.
  if sha(bfile(edit))!=OLD_DP:write_atomic(edit,old)
  if sha(bfile(live))!=OLD_DP:write_atomic(live,old)
  sha_pair(OLD_DP)
  call(['docker','exec',C,'asterisk','-rx','dialplan reload'],20)
  verify_dialplan('v42')
  if bfile(USB)!=oldusb:write_atomic(USB,oldusb)
  need(sha(bfile(USB))==sha(oldusb),'USB_WRAPPER_RESTORE_READBACK_MISMATCH')
  if cur!=original:
   docker_copy(BACK_EP,EP)
   call(['docker','exec','-u','0',C,'chmod','755',EP])
  need(ds(EP)==sha(original),'ENTRYPOINT_RESTORE_READBACK_MISMATCH')
  # Retain evidence rather than deleting an uncertain release transaction.
  if JOURNAL.exists():
   dst=LAB/'qdc507-v43-release.aborted-pending.json'
   need(not dst.exists(),'ABORT_RECEIPT_ALREADY_EXISTS')
   JOURNAL.rename(dst)
  if APPLIED.exists():
   dst=LAB/'qdc507-v43-release.aborted-applied.json'
   need(not dst.exists(),'ABORT_APPLIED_RECEIPT_ALREADY_EXISTS')
   APPLIED.rename(dst)
  print('V43_ABORT_TO_PINNED_V42_OK=1')
  print('V43_DEVICE_COPY_LEFT_INERT=1')
  print('NO_ADB_DEVICE_OPERATION=1')
  print('USB_RECOVERY_RESTORED_TO_V42=1')
  print('NO_USB_OR_DOCKER_RESTART=1')
 finally:release(held)

def rollback():
 check_pending_and_mode()
 need(APPLIED.is_file() and not APPLIED.is_symlink(),'NO_RELEASE_RECEIPT_FOR_ROLLBACK')
 need(not JOURNAL.exists(),'RELEASE_JOURNAL_PENDING_MANUAL_REVIEW')
 j=json.loads(bfile(APPLIED))
 need(j.get('phase')=='applied' and j.get('new_dialplan_sha256')==NEW_DP and
      j.get('old_dialplan_sha256')==OLD_DP,'RECEIPT_MISMATCH')
 held=lock_recovery()
 try:
  idle()
  sha_pair(NEW_DP)
  oldusb=bfile(BACK_USB,j['old_usb_sha256'])
  newusb=bfile(STAGE_USB,j['new_usb_sha256'])
  need(bfile(USB)==newusb,'USB_WRAPPER_CHANGED_AFTER_APPLY')
  expected=bfile(STAGE_EP)
  need(read_entrypoint_current()==expected,'ENTRYPOINT_CHANGED_AFTER_APPLY')
  original=bfile(BACK_EP,j['old_ep_sha256'])
  old=bfile(BACK_DP,OLD_DP)
  verify_dialplan('v43')
  # Important: revert inbound Asterisk first; v42 device-v2 remains untouched.
  live,edit=sha_pair(NEW_DP)
  write_atomic(edit,old)
  write_atomic(live,old)
  call(['docker','exec',C,'asterisk','-rx','dialplan reload'],20)
  verify_dialplan('v42')
  write_atomic(USB,oldusb)
  need(sha(bfile(USB))==sha(oldusb),'USB_WRAPPER_ROLLBACK_MISMATCH')
  # Then remove only our entrypoint hook (do not remove the pinned v42 prepare).
  with tempfile.NamedTemporaryFile(prefix='qdc-v43-roll-ep-',dir=str(LAB),delete=False) as f:
   tmp=Path(f.name);f.write(original)
  try:
   docker_copy(tmp,EP)
   call(['docker','exec','-u','0',C,'chmod','755',EP])
   need(ds(EP)==sha(original),'ENTRYPOINT_ROLLBACK_HASH_MISMATCH')
  finally:tmp.unlink()
  APPLIED.rename(LAB/'qdc507-v43-release.rolledback.json')
  print('V43_ROLLBACK_TO_V42_OK=1')
  print('EXISTING_V43_ARTIFACTS_LEFT_INERT_FOR_AUDIT=1')
  print('USB_RECOVERY_RESTORED_TO_V42=1')
  print('NO_USB_OR_DOCKER_RESTART=1')
 finally:release(held)

if __name__=='__main__':
 try:
  ap=argparse.ArgumentParser(description=__doc__)
  group=ap.add_mutually_exclusive_group(required=True)
  for a in ('--selftest','--prepare','--apply','--rollback','--abort','--status','--verify'):group.add_argument(a,action='store_true')
  x=ap.parse_args()
  if x.selftest:
   selftest();sys.exit(0)
  need(os.geteuid()==0,'ROOT_REQUIRED')
  # Historical v42 dialplan pin predates later cloud/local routing choices.
  # Reconstruct and pin the exact current v42 baseline from an independently
  # SHA-verified v43 preview; the actual live & edit files must both match.
  OLD_DP=stage_derived_v42_pin()
  print('V43_BASELINE_PIN_FROM_SHA256_PINNED_PREVIEW='+OLD_DP)
  if x.prepare:prepare()
  elif x.apply:apply()
  elif x.rollback:rollback()
  elif x.abort:abort()
  elif x.verify:verify()
  else:status()
 except (Halt,OSError,KeyError,ValueError,IndexError,subprocess.TimeoutExpired) as e:
  print('QDC507_V43_RELEASE_STOP='+str(e)[:300],file=sys.stderr);sys.exit(2)
