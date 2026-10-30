#!/usr/bin/python3.8
# coding: utf-8
"""QDC507 Guard16 v2 idempotent runtime restore revision 2 (not yet boot-hooked).

Run on Synology NAS only. Commands:
  --check       : read-only snapshot, verifies pinned NAS backups and live device
  --ensure-v2   : when current bridge is v1 and everything idle, stage + activate
                  verified v2; when already v2, do no writes
  --restore-v1  : idle-only A/B rollback through preserved original v1 symlink

IMPORTANT: This is NOT automatic integration with coldboot/hotplug/mode scripts.
Do NOT enable a new timer or edit existing sealed recovery files to use it
until call points/pins and reset behavior have been audited.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
ADB = '/usr/local/libexec/qdc507/adb'
BASE = LAB / 'qdc507-dynusb-oneclick-baseline-v1.json'
MANIFEST = LAB / 'qdc507-guard16-v2-promotion-manifest.json'
HOST_V1 = LAB / 'qdc507-guard16-v1-clock8-backup.armv7'
HOST_V2 = LAB / 'qdc507-guard16-v2-candidate.armv7'
V1_SHA = '7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71'
V2_SHA = '0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913'
D = '/tmp/qdc507-serial16k-v7'
V1 = D + '/bridge-d5-clock8-guard16'
V2 = '/tmp/qdc507-guard16-v2-lab/bridge-d5-v2'
V2_UPLOAD = V2 + '.upload-production'
LINK = D + '/bridge-d5'
KEEP = D + '/bridge-d5.guard16-v1.keep'
TMP_LINK = D + '/bridge-d5.guard16-switch.new'
CONTAINER = 'qdc507-hardware'
MODE_FILE = LAB / 'qdc507-mode-upgrade-c3e-preaudio-v2/mode/current'
PENDING = ['qdc507-dynusb-oneclick-pending-v1.json',
           'qdc507-dynusb-mode-switch-pending.json',
           'qdc507-usb-recovery-v4-pending.json',
           'qdc507-dynusb-cutover-pending-v1.json']
LOCK_NAMES = ['/run/qdc507-guard16-v2-runtime-restore.lock',
              '/run/qdc507-dynusb-oneclick.lock',
              '/run/qdc507-dynusb-mode.lock',
              '/run/qdc507-v4-usb.lock',
              '/run/qdc507-v4-boot.lock']

class Stop(Exception):
    pass

def need(condition, reason):
    if not condition:
        raise Stop(reason)

def execute(argv, timeout=30):
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Stop('COMMAND_TIMEOUT_STATE_INDETERMINATE_' + argv[0])
    except OSError as exc:
        raise Stop('COMMAND_UNAVAILABLE_' + argv[0] + '_' + type(exc).__name__)

def call(argv, timeout=30):
    p = execute(argv, timeout)
    need(p.returncode == 0, 'COMMAND_FAILED_' + argv[0] + ':' +
         (p.stderr or p.stdout)[-180:])
    return p.stdout.strip()

def trusted(path, low=100, high=10000000):
    s = path.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
         not s.st_mode & 0o022 and low <= s.st_size <= high,
         'UNTRUSTED_OR_UNEXPECTED_FILE_' + str(path))
    return path.read_bytes()

def binary(path, digest):
    b = trusted(path, 1024, 10000000)
    need(b[:4] == b'\x7fELF' and hashlib.sha256(b).hexdigest() == digest,
         'NAS_BINARY_SHA_OR_ELF_MISMATCH_' + path.name)
    return True

def verify_nas():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_DIRECTORY_UNSAFE')
    binary(HOST_V1, V1_SHA)
    binary(HOST_V2, V2_SHA)
    m = json.loads(trusted(MANIFEST, 100, 50000).decode('utf-8'))
    need(m.get('format') == 'qdc507-guard16-v2-promotion-prep-v1' and
         m.get('v1_sha256') == V1_SHA and m.get('v2_sha256') == V2_SHA and
         m.get('v1_file') == str(HOST_V1) and m.get('v2_file') == str(HOST_V2),
         'PROMOTION_MANIFEST_MISMATCH')
    b = json.loads(trusted(BASE, 100, 100000).decode('utf-8'))
    need(b.get('format') == 'qdc507-dynusb-oneclick-v1' and b.get('mode') == '16k',
         'BASELINE_MODE_OR_FORMAT_CHANGED')
    serial = b.get('serial')
    need(isinstance(serial, str) and re.fullmatch(r'[A-Za-z0-9._:-]{4,120}', serial),
         'BASELINE_DEVICE_SERIAL_INVALID')
    # Read-only pin checking. Do not re-seal or change the baseline here.
    guard = LAB / 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
    need(hashlib.sha256(trusted(guard, 1000, 1000000)).hexdigest() ==
         b.get('scripts', {}).get(guard.name), 'SEALED_GUARD_SCRIPT_CHANGED')
    print('NAS_V1_V2_HASH_AND_MANIFEST_OK=1')
    print('BASELINE_16K_AND_GUARD_PIN_OK=1')
    return b

def check_recovery_generation(base):
    need(MODE_FILE.is_file() and MODE_FILE.read_text().strip() == '16k',
         'CURRENT_PRODUCTION_MODE_IS_NOT_16K')
    need(base.get('host_boot_id') ==
         Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'HOST_BOOT_CHANGED_RUN_EXISTING_COLD_BOOT_RECOVERY_FIRST')
    found=[]
    for d in Path('/sys/bus/usb/devices').iterdir():
        try:
            if (d/'idVendor').read_text().strip().lower() == '2ca3' and \
               (d/'idProduct').read_text().strip().lower() == '4006':
                found.append([d.name, (d/'busnum').read_text().strip(),
                              (d/'devnum').read_text().strip(),
                              (d/'serial').read_text().strip()])
        except OSError:
            continue
    need(len(found) == 1 and found[0] == base.get('usb_generation'),
         'USB_GENERATION_CHANGED_RUN_EXISTING_ONECLICK_FIRST')
    c=json.loads(call(['docker','inspect',CONTAINER],15))
    need(len(c)==1 and c[0].get('Id') == base.get('container_id') and
         c[0].get('Image') == base.get('image_id') and
         c[0].get('State',{}).get('Running') is True,
         'PRODUCTION_CONTAINER_ID_IMAGE_OR_STATE_CHANGED')
    print('SEALED_USB_BOOT_AND_16K_PRODUCTION_CONTEXT_OK=1')

def adb(serial, command, timeout=30):
    return call([ADB, '-H', '127.0.0.1', '-P', '5037', '-s', serial,
                 'shell', command], timeout=timeout)

def adb_shell(serial, script, timeout=30):
    # Send a single noninteractive sh -c string, NOT 'sh -s' over a tty.
    # ADB can return rc=0 even when the remote shell fails; require last-line ACK.
    # The script must enable `set -eu`; ACK occurs only after success.
    token='QDC507_V2_REMOTE_SUCCESS_'+str(os.getpid())
    code = script + '\nprintf "%s\\n" ' + shlex.quote(token) + '\n'
    out = adb(serial, 'sh -c ' + shlex.quote(code), timeout)
    lines = [line.strip() for line in out.replace('\r','').splitlines() if line.strip()]
    need(lines and lines[-1] == token and lines.count(token) == 1,
         'ADB_DEVICE_OPERATION_NOT_ACKNOWLEDGED_' + '\n'.join(lines[-3:])[:150])
    return '\n'.join(lines[:-1])

def device_serial(base_serial):
    data=call([ADB,'-H','127.0.0.1','-P','5037','devices','-l'],timeout=15)
    online=[]
    for line in data.splitlines():
        m=re.match(r'^([^\s]+)\s+device(?:\s|$)',line.strip())
        if m: online.append(m.group(1))
    need(len(online)==1 and online[0].lower()==base_serial.lower(),
         'EXPECTED_QDC507_IS_NOT_THE_ONLY_ONLINE_ADB_DEVICE')
    return online[0]

SNAPSHOT = r'''set -eu
D=/tmp/qdc507-serial16k-v7
V1="$D/bridge-d5-clock8-guard16"
V2=/tmp/qdc507-guard16-v2-lab/bridge-d5-v2
L="$D/bridge-d5"
K="$D/bridge-d5.guard16-v1.keep"
test -d "$D" && test -f "$V1" && test -L "$L"
sha() { sha256sum "$1" | cut -d ' ' -f 1; }
printf 'STATE_V1_SHA=%s\n' "$(sha "$V1")"
if test -f "$V2"; then printf 'STATE_V2_SHA=%s\n' "$(sha "$V2")";
else printf 'STATE_V2_SHA=missing\n'; fi
v1=$(readlink -f "$V1")
current=$(readlink -f "$L")
if test "$current" = "$v1"; then mode=v1;
elif test -f "$V2" && test "$current" = "$(readlink -f "$V2")"; then mode=v2;
else mode=unknown; fi
printf 'STATE_MODE=%s\n' "$mode"
printf 'STATE_ACTIVE_HASH=%s\n' "$(sha "$L")"
if test -L "$K"; then
 if test "$(readlink -f "$K")" = "$v1"; then keep=present;
 else keep=unexpected; fi
elif test -e "$K"; then keep=unexpected;
else keep=absent; fi
printf 'STATE_V1_BACKUP=%s\n' "$keep"
if test -r "$D/status"; then
 printf 'STATE_SUPERVISOR=%s\n' "$(cat "$D/status")";
else printf 'STATE_SUPERVISOR=missing\n'; fi
'''

def device_snapshot(serial):
    s=adb_shell(serial, SNAPSHOT, timeout=25)
    d={}
    for line in s.splitlines():
        match=re.fullmatch(r'(STATE_[A-Z0-9_]+)=(.*)',line.strip())
        if match: d[match.group(1)]=match.group(2)
    keys=('STATE_V1_SHA','STATE_V2_SHA','STATE_MODE','STATE_ACTIVE_HASH',
          'STATE_V1_BACKUP','STATE_SUPERVISOR')
    need(all(k in d for k in keys), 'DEVICE_SNAPSHOT_INCOMPLETE')
    need(d['STATE_V1_SHA']==V1_SHA, 'DEVICE_V1_SHA_MISMATCH')
    need(d['STATE_V2_SHA'] in ('missing',V2_SHA), 'DEVICE_V2_SHA_MISMATCH')
    need(d['STATE_MODE'] in ('v1','v2'), 'UNRECOGNIZED_ACTIVE_BRIDGE_D5')
    need(d['STATE_ACTIVE_HASH']==(V1_SHA if d['STATE_MODE']=='v1' else V2_SHA),
         'ACTIVE_BRIDGE_D5_HASH_MISMATCH')
    need(d['STATE_V1_BACKUP'] in ('present','absent'), 'DEVICE_ROLLBACK_LINK_UNEXPECTED')
    print('DEVICE_CURRENT_MODE='+d['STATE_MODE'])
    print('DEVICE_V2_STAGE='+('present' if d['STATE_V2_SHA']==V2_SHA else 'missing'))
    print('DEVICE_V1_BACKUP='+d['STATE_V1_BACKUP'])
    print('DEVICE_SUPERVISOR_STATUS='+d['STATE_SUPERVISOR'])
    return d

def locked():
    held=[]
    for name in LOCK_NAMES:
        fd=os.open(name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        st=os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and
             not stat.S_IMODE(st.st_mode)&0o077,'LOCK_UNTRUSTED_'+name)
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Stop('ANOTHER_RECOVERY_OR_SWITCH_IS_RUNNING_'+name)
        held.append(fd)
    return held

def check_host_idle():
    for p in PENDING:
        need(not os.path.lexists(str(LAB / p)), 'RECOVERY_PENDING_REQUIRES_REVIEW_'+p)
    line=call(['docker','exec',CONTAINER,'asterisk','-rx','core show channels count'],15)
    need(re.search(r'^\s*0 active channels\s*$',line,re.M) and
         re.search(r'^\s*0 active calls\s*$',line,re.M),'ASTERISK_HAS_ACTIVE_CALLS')
    need(call(['systemctl','is-active','qdc507-sms-tick.timer'],12)=='active',
         'SMS_TIMER_NOT_ACTIVE')
    # The SMS timer launches a short-lived service. A single sample can land
    # during legitimate delivery. Never stop or interrupt the worker; wait for
    # a bounded clean idle window before any device-side writes.
    deadline = time.monotonic() + 35
    prior = None
    while True:
        p = execute(['systemctl','is-active','qdc507-sms-tick.service'],timeout=12)
        state = p.stdout.strip()
        if state == 'inactive':
            print('SMS_WORKER_IDLE=1')
            break
        if state not in ('active','activating','deactivating'):
            raise Stop('SMS_WORKER_UNEXPECTED_STATE_' + (state or 'empty') +
                       '_RC' + str(p.returncode))
        if state != prior:
            print('SMS_WORKER_WAIT_STATE=' + state)
            prior = state
        if time.monotonic() >= deadline:
            raise Stop('SMS_WORKER_STILL_BUSY_AFTER_35S_' + state)
        time.sleep(1)
    # Do not stage a variant when modem has not yet reached normal 16k business state.
    s=call(['docker','exec',CONTAINER,'asterisk','-rx',
            'quectel show device state quectel0'],timeout=15)
    for field, value in (('State','Free'),('Voice','Yes'),('SMS','Yes'),
                         ('Calls/Channels','0')):
        need(re.search(r'^\s*'+field+r'\s*:\s*'+value+r'\s*$',s,re.M),
             'MODEM_NOT_READY_'+field)
    print('ASTERISK_MODEM_SMS_IDLE_OK=1')

DEVICE_IDLE=r'''set -eu
D=/tmp/qdc507-serial16k-v7
test "$(cat "$D/status")" = 'DONE d4=0 d5=0'
test "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0
command -v pidof >/dev/null 2>&1
for n in bridge-d4 bridge-d5 bridge-d5-v2 bridge-d5-clock8-guard16; do
 if pidof "$n" >/dev/null 2>&1; then exit 9; fi
done
for n in 4p 4c 5p 6c; do
 test "$(cat /proc/asound/card0/pcm${n}/sub0/hw_params)" = closed
done
'''

def idle_device(serial):
    adb_shell(serial, DEVICE_IDLE,timeout=30)
    print('DEVICE_AUDIO_IDLE_AND_PCM_CLOSED=1')

def stage_v2(serial):
    # Refuse to overwrite a staged unexpected binary or a leftover upload.
    pre=r'''set -eu
DIR=/tmp/qdc507-guard16-v2-lab
V2="$DIR/bridge-d5-v2"
TMP="$V2.upload-production"
if test -e "$V2" || test -L "$V2"; then exit 7; fi
test ! -e "$TMP" && test ! -L "$TMP"
mkdir -p "$DIR"
test -d "$DIR" && test ! -L "$DIR"
'''
    adb_shell(serial,pre)
    p=execute([ADB,'-H','127.0.0.1','-P','5037','-s',serial,
               'push',str(HOST_V2),V2_UPLOAD],timeout=60)
    need(p.returncode==0,'ADB_PUSH_FAILED_'+(p.stderr or p.stdout)[-180:])
    print('DEVICE_V2_PUSH_COMPLETED=1')
    commit=r'''set -eu
V2=/tmp/qdc507-guard16-v2-lab/bridge-d5-v2
TMP="$V2.upload-production"
test -f "$TMP" && test ! -L "$TMP"
test "$(sha256sum "$TMP" | cut -d ' ' -f 1)" = '''+V2_SHA+r'''
test ! -e "$V2" && test ! -L "$V2"
chmod 755 "$TMP"
mv "$TMP" "$V2"
test -x "$V2"
test "$(sha256sum "$V2" | cut -d ' ' -f 1)" = '''+V2_SHA+r'''
echo STAGED_V2_SHA_VERIFIED=1
'''
    out=adb_shell(serial,commit,timeout=40)
    need('STAGED_V2_SHA_VERIFIED=1' in out.splitlines(),'DEVICE_STAGE_NOT_ACKNOWLEDGED')
    print('DEVICE_V2_STAGED_AND_HASH_VERIFIED=1')

SWITCH_V2=r'''set -eu
D=/tmp/qdc507-serial16k-v7
V1="$D/bridge-d5-clock8-guard16"
V2=/tmp/qdc507-guard16-v2-lab/bridge-d5-v2
L="$D/bridge-d5"
K="$D/bridge-d5.guard16-v1.keep"
T="$D/bridge-d5.guard16-switch.new"
test -L "$L" && test ! -e "$K" && test ! -L "$K"
test ! -e "$T" && test ! -L "$T"
test "$(readlink -f "$L")" = "$(readlink -f "$V1")"
test "$(sha256sum "$V1" | cut -d ' ' -f 1)" = '''+V1_SHA+r'''
test -x "$V2" && test "$(sha256sum "$V2" | cut -d ' ' -f 1)" = '''+V2_SHA+r'''
test "$(cat "$D/status")" = 'DONE d4=0 d5=0'
test "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0
for n in 4p 4c 5p 6c; do
 test "$(cat /proc/asound/card0/pcm${n}/sub0/hw_params)" = closed
done
origin=$(readlink "$L")
ln -s "$origin" "$K"
ln -s "$V2" "$T"
mv -f "$T" "$L"
test "$(sha256sum "$L" | cut -d ' ' -f 1)" = '''+V2_SHA+r'''
echo DEVICE_ATOMIC_SWITCH_V2_OK=1
'''

RESTORE_V1=r'''set -eu
D=/tmp/qdc507-serial16k-v7
V1="$D/bridge-d5-clock8-guard16"
V2=/tmp/qdc507-guard16-v2-lab/bridge-d5-v2
L="$D/bridge-d5"
K="$D/bridge-d5.guard16-v1.keep"
T="$D/bridge-d5.guard16-switch.new"
test -L "$L" && test -L "$K"
test ! -e "$T" && test ! -L "$T"
test "$(readlink -f "$L")" = "$(readlink -f "$V2")"
test "$(readlink -f "$K")" = "$(readlink -f "$V1")"
test "$(sha256sum "$V1" | cut -d ' ' -f 1)" = '''+V1_SHA+r'''
test "$(cat "$D/status")" = 'DONE d4=0 d5=0'
test "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0
for n in 4p 4c 5p 6c; do
 test "$(cat /proc/asound/card0/pcm${n}/sub0/hw_params)" = closed
done
origin=$(readlink "$K")
ln -s "$origin" "$T"
mv -f "$T" "$L"
test "$(sha256sum "$L" | cut -d ' ' -f 1)" = '''+V1_SHA+r'''
rm "$K"
echo DEVICE_ATOMIC_RESTORE_V1_OK=1
'''

def main():
    parser=argparse.ArgumentParser(description='Guard16 v2 device-side restore after existing 16k startup')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true')
    group.add_argument('--ensure-v2',action='store_true')
    group.add_argument('--restore-v1',action='store_true')
    a=parser.parse_args()
    print('GUARD16_V2_RUNTIME_HELPER_REVISION=2')
    base=verify_nas()
    if a.check:
        serial=device_serial(base['serial'])
        s=device_snapshot(serial)
        print('READ_ONLY_RUNTIME_CHECK_OK=1')
        return
    held=locked()
    try:
        check_recovery_generation(base)
        serial=device_serial(base['serial'])
        first=device_snapshot(serial)
        # A verified no-op is read-only. It must not be blocked by an unrelated
        # SMS delivery tick and must never start, stop, or restart the worker.
        if a.ensure_v2 and first['STATE_MODE']=='v2':
            need(first['STATE_V1_BACKUP']=='present',
                 'V2_ACTIVE_BUT_ROLLBACK_LINK_MISSING_STOP_FOR_REVIEW')
            print('ALREADY_V2_NO_WRITES=1')
            return
        if a.restore_v1 and first['STATE_MODE']=='v1':
            need(first['STATE_V1_BACKUP']=='absent',
                 'V1_ACTIVE_BUT_OLD_BACKUP_PRESENT')
            print('ALREADY_V1_NO_WRITES=1')
            return
        # Any actual modification requires Asterisk, SMS and the device idle.
        check_host_idle()
        again=device_snapshot(serial)
        need(all(again[k]==first[k] for k in (
             'STATE_V1_SHA','STATE_V2_SHA','STATE_MODE','STATE_ACTIVE_HASH',
             'STATE_V1_BACKUP')), 'DEVICE_CHANGED_WHILE_WAITING_FOR_IDLE')
        first=again
        if a.ensure_v2:
            need(first['STATE_MODE']=='v1' and first['STATE_V1_BACKUP']=='absent',
                 'V1_STATE_HAS_UNEXPECTED_ROLLBACK_LINK')
            idle_device(serial)
            if first['STATE_V2_SHA']=='missing':
                stage_v2(serial)
            # Revalidate host/device idle immediately before atomic change.
            check_host_idle()
            idle_device(serial)
            out=adb_shell(serial,SWITCH_V2,timeout=40)
            need('DEVICE_ATOMIC_SWITCH_V2_OK=1' in out.splitlines(),
                 'SWITCH_V2_ACK_MISSING')
            after=device_snapshot(serial)
            need(after['STATE_MODE']=='v2' and
                 after['STATE_V1_BACKUP']=='present' and
                 after['STATE_V2_SHA']==V2_SHA,'V2_POST_SWITCH_INCONSISTENT')
            print('GUARD16_V2_RUNTIME_RESTORED=1')
            return
        if first['STATE_MODE']=='v1':
            need(first['STATE_V1_BACKUP']=='absent',
                 'V1_ACTIVE_BUT_OLD_BACKUP_PRESENT')
            print('ALREADY_V1_NO_WRITES=1')
            return
        need(first['STATE_MODE']=='v2' and first['STATE_V1_BACKUP']=='present',
             'V2_STATE_NOT_ROLLBACK_READY')
        idle_device(serial)
        check_host_idle()
        out=adb_shell(serial,RESTORE_V1,timeout=40)
        need('DEVICE_ATOMIC_RESTORE_V1_OK=1' in out.splitlines(),
             'RESTORE_V1_ACK_MISSING')
        after=device_snapshot(serial)
        need(after['STATE_MODE']=='v1' and after['STATE_V1_BACKUP']=='absent',
             'V1_POST_RESTORE_INCONSISTENT')
        print('GUARD16_V1_ROLLBACK_VERIFIED=1')
    finally:
        for fd in reversed(held): os.close(fd)

if __name__=='__main__':
    try: main()
    except (Stop, OSError, ValueError, KeyError, BlockingIOError) as exc:
        print('GUARD16_V2_RUNTIME_STOP='+str(exc)[:380], file=sys.stderr)
        print('NO_ASSUMPTION_ABOUT_DEVICE_STATE_AFTER_TIMEOUT=1', file=sys.stderr)
        sys.exit(2)
