#!/usr/bin/env python3
"""V2-identity-pinned single guarded USBDEVFS_RESET for QDC507; no Docker restart.

Production guard.  --check is strictly read-only. --reset is disruptive,
requires QDC507 ADB genuinely unavailable and a stable, idle production setup.
Never perform a second reset inside this program.

--enroll makes an immutable root-owned pin for the NEW running v2 container.
The old USB baseline is used ONLY for USB generation/serial/boot guards,
not as authority for the replaced Docker identity.
This is NOT a replacement for legacy USB SIM-swap or NAS cold-boot recovery.
"""
import argparse
import fcntl
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
BASE = LAB / 'qdc507-dynusb-memoryfix-v2-baseline.json'
PIN = LAB / 'qdc507-memoryfix-v2-runtime-pin.json'
IMAGE = 'qdc507-asterisk:16k-memoryfix-v2-candidate-20261118'
BACKUP = 'qdc507-hardware-v4-pre-memoryfix-20261118'
MODULE_SHA = 'c5b940d5505fc40d9c2815674b1107586b39a0fba5fe0e0dce060c111a680bf0'
OLD_MODULE_SHA = 'ca25e8e04a5ad9792282635a99e1d7152429800d6a51331ba18991e72436fa87'
MOUNT_PINS = {
    '/etc/asterisk':('/volume2/docker/asterisk/qdc507-current/v0', True),
    '/etc/asterisk/modules.conf':('/volume2/docker/asterisk/qdc507-current/modules.hardware.runtime.conf', False),
    '/etc/qdc507-mode':('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-mode-upgrade-c3e-preaudio-v2/mode', False),
    '/var/lib/asterisk/phoneprov':('/volume2/docker/asterisk/qdc507-current/v4', True),
    '/var/lib/asterisk/keys':('/volume2/docker/asterisk/qdc507-current/v3', True),
    '/dev/qdc507':('/dev/qdc507', False),
    '/dev/snd':('/dev/snd', False),
    '/usr/local/bin/adb':('/volume2/docker/asterisk/qdc507-current/helpers/adb', False),
    '/usr/local/sbin/qdc507-call-audio-start.sh':('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-mode-upgrade-c3e-preaudio-v2/dispatch/start.sh', False),
    '/opt/qdc507/runtime':('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-mode-upgrade-c3e-preaudio-v2/profiles', False),
    '/usr/local/sbin/qdc507-call-audio-stop.sh':('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-mode-upgrade-c3e-preaudio-v2/dispatch/stop.sh', False),
    '/var/lib/asterisk/sounds':('/volume2/docker/asterisk/qdc507-current/v5', True),
    '/var/log/asterisk':('/volume2/docker/asterisk/qdc507-current/v1', True),
    '/var/spool/asterisk':('/volume2/docker/asterisk/qdc507-current/v2', True),
}

def mounts_exact(obj):
    ms = obj.get('Mounts', [])
    return len(ms) == len(MOUNT_PINS) and all(
        m.get('Type') == 'bind' and
        MOUNT_PINS.get(m.get('Destination')) == (m.get('Source'),m.get('RW'))
        for m in ms)

LOCK = LAB / 'qdc507-adb-usbreset-v1.lock'
JOURNAL = LAB / 'qdc507-adb-usbreset-v1.json'
USB = Path('/sys/bus/usb/devices')
ADB = '/usr/local/libexec/qdc507/adb'
BOOT = '/usr/local/sbin/qdc507-boot-adb.py'
CONTAINER = 'qdc507-hardware'
GUARD = LAB / 'qdc507-guard16-runtime-memoryfix-v2.py'
RESET_IOCTL = 0x5514  # Linux USBDEVFS_RESET = _IO('U',20)
COOLDOWN_SECONDS = 1800

class Stop(Exception):
    pass

def need(p, msg):
    if not p:
        raise Stop(msg)

def command(args, timeout=15, allow_fail=False):
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Stop('COMMAND_ERROR_' + str(args[0]) + '_' + type(e).__name__)
    if not allow_fail:
        need(p.returncode == 0, 'COMMAND_FAILED_' + str(args[0]) + '_rc' + str(p.returncode))
    return p

def read(p):
    return p.read_text().strip()

def state_file(p):
    if not p.exists():
        return None
    st = p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and
         not (stat.S_IMODE(st.st_mode) & 0o077) and st.st_nlink == 1,
         'UNTRUSTED_STATE_FILE_' + p.name)
    return json.loads(p.read_text())

def atomic_json(p, value):
    tmp = p.with_name('.' + p.name + '.stage-' + str(os.getpid()))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(tmp), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)  # DSM shared-folder ACL may override initial create mode
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(p))
    finally:
        if tmp.exists():
            tmp.unlink()

def baseline():
    b = state_file(BASE)
    need(isinstance(b, dict), 'BASELINE_MISSING')
    need(b.get('format') == 'qdc507-dynusb-oneclick-v1' and b.get('mode') == '16k',
         'BASELINE_FORMAT_OR_MODE_CHANGED')
    need(read(Path('/proc/sys/kernel/random/boot_id')) == b.get('host_boot_id'),
         'NAS_BOOT_CHANGED_NO_RESET')
    gen = b.get('usb_generation')
    need(isinstance(gen, list) and len(gen) == 4 and
         all(isinstance(x, str) and x for x in gen), 'BASELINE_USB_GENERATION_INVALID')
    need(re.fullmatch(r'[A-Za-z0-9._:-]{4,120}', str(b.get('serial', ''))),
         'BASELINE_SERIAL_INVALID')
    need(gen[3].lower() == b['serial'].lower(), 'BASELINE_SERIAL_CONFLICT')
    return b

def usb_snapshot(b):
    found = []
    for d in USB.iterdir():
        try:
            if read(d/'idVendor').lower() == '2ca3' and read(d/'idProduct').lower() == '4006':
                found.append(d)
        except (OSError, ValueError):
            pass
    need(len(found) == 1, 'QDC507_NOT_UNIQUE')
    d = found[0]
    gen = [d.name, read(d/'busnum'), read(d/'devnum'), read(d/'serial')]
    need(gen == b['usb_generation'], 'USB_GENERATION_CHANGED_NO_RESET')
    need(read(d/'authorized') == '1', 'QDC507_NOT_AUTHORIZED')
    need(read(d/'bNumInterfaces') == '9', 'QDC507_INTERFACE_COUNT_CHANGED')
    iface = USB/(d.name + ':1.5')
    need(iface.is_dir(), 'ADB_IF5_MISSING')
    try:
        desc = tuple(int(read(iface/n), 16) for n in
                     ('bInterfaceClass', 'bInterfaceSubClass', 'bInterfaceProtocol'))
    except ValueError:
        raise Stop('ADB_IF5_DESCRIPTOR_UNREADABLE')
    need(desc == (0xff, 0x42, 0x01), 'ADB_IF5_DESCRIPTOR_CHANGED')
    need(read(iface/'authorized') == '1', 'ADB_IF5_NOT_AUTHORIZED')
    driver = iface/'driver'
    need(not driver.is_symlink() or driver.resolve(strict=True).name == 'usbfs',
         'ADB_IF5_BOUND_TO_OTHER_DRIVER')
    bus, dev = int(gen[1]), int(gen[2])
    need(1 <= bus <= 255 and 1 <= dev <= 127, 'USB_BUS_OR_DEVICE_INVALID')
    node = Path('/dev/bus/usb/%03d/%03d' % (bus, dev))
    st = node.stat()
    need(stat.S_ISCHR(st.st_mode) and os.major(st.st_rdev) == 189,
         'USB_DEVICE_NODE_INVALID')
    return gen, node

def _json_docker(args):
    obj = json.loads(command(['docker'] + args, timeout=15).stdout)
    need(isinstance(obj, list) and len(obj) == 1, 'DOCKER_INSPECT_NOT_UNIQUE')
    return obj[0]

def v2_pin():
    j = state_file(PIN)
    need(isinstance(j, dict), 'V2_RUNTIME_PIN_MISSING_RUN_EXPLICIT_--enroll')
    need(j.get('format') == 'qdc507-16k-memoryfix-v2-runtime-pin-v1'
         and j.get('image_tag') == IMAGE
         and j.get('module_sha256') == MODULE_SHA
         and isinstance(j.get('container_id'), str)
         and re.fullmatch('[a-f0-9]{64}', j['container_id']) is not None
         and re.fullmatch('sha256:[a-f0-9]{64}', str(j.get('image_id'))) is not None
         and j.get('backup_container') == BACKUP,  'V2_RUNTIME_PIN_INVALID')
    return j

def inspect_v2():
    pin = v2_pin()
    obj = _json_docker(['inspect', CONTAINER])
    need(obj['Id'] == pin['container_id'] and
         obj['Image'] == pin['image_id'] and
         obj['Config']['Image'] == IMAGE and
         obj['State']['Running'], 'V2_CONTAINER_ID_IMAGE_OR_RUNNING_CHANGED')
    img = _json_docker(['image', 'inspect', IMAGE])
    need(img['Id'] == pin['image_id'], 'V2_IMAGE_TAG_REPOINTED')
    old = _json_docker(['inspect', BACKUP])
    need(not old['State']['Running'] and old['Image'] == pin.get('backup_image_id') and
         old['Config']['Image'] == 'qdc507-asterisk:dualmode-remotehangup-v4-prod',
         'ORIGINAL_V4_BACKUP_IDENTITY_CHANGED')
    need(obj['HostConfig']['NetworkMode'] == 'host' and
         mounts_exact(obj) and
         obj['HostConfig']['Privileged'] is False,
         'V2_CONTAINER_NETWORK_MOUNTS_OR_PRIVILEGE_CHANGED')
    module = command(['docker','exec',CONTAINER,'sha256sum',
                      '/usr/lib/asterisk/modules/chan_quectel.so'],timeout=12).stdout.split()[0]
    slot = command(['docker','exec',CONTAINER,'sha256sum',
                    '/opt/qdc507/modes/16k/chan_quectel.so'],timeout=12).stdout.split()[0]
    mode = command(['docker','exec',CONTAINER,'cat','/etc/qdc507-mode/current'],timeout=12).stdout.strip()
    need(module == MODULE_SHA and slot == MODULE_SHA and mode == '16k',
         'V2_DRIVER_SLOT_OR_MODE_CHANGED')
    return obj

def enroll():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(not PIN.exists() and not PIN.is_symlink(), 'V2_PIN_ALREADY_EXISTS_REFUSE_OVERWRITE')
    obj = _json_docker(['inspect', CONTAINER])
    need(obj['Config']['Image'] == IMAGE and obj['State']['Running'],
         'NOT_EXPECTED_LIVE_V2_CONTAINER')
    need(obj['HostConfig']['NetworkMode'] == 'host' and
         mounts_exact(obj) and
         obj['HostConfig']['Privileged'] is False,
         'V2_RUNTIME_SHAPE_CHANGED')
    need(obj['Config'].get('Entrypoint') == ['/usr/local/sbin/qdc507-mode-boot.sh'],
         'V2_STARTUP_ENTRYPOINT_CHANGED')
    img = _json_docker(['image','inspect',IMAGE])
    need(img['Id'] == obj['Image'], 'V2_CANDIDATE_TAG_NOT_MATCHING_LIVE_IMAGE')
    old = _json_docker(['inspect',BACKUP])
    need(not old['State']['Running'] and
         old['Config']['Image'] == 'qdc507-asterisk:dualmode-remotehangup-v4-prod',
         'ORIGINAL_V4_BACKUP_MISSING_OR_RUNNING')
    for path in ('/usr/lib/asterisk/modules/chan_quectel.so',
                 '/opt/qdc507/modes/16k/chan_quectel.so'):
        sha = command(['docker','exec',CONTAINER,'sha256sum',path]).stdout.split()[0]
        need(sha == MODULE_SHA, 'V2_MODULE_SHA_NOT_PINNED_'+path)
    need(command(['docker','exec',CONTAINER,'cat','/etc/qdc507-mode/current']).stdout.strip() == '16k',
         'V2_AUDIO_MODE_NOT_16K')
    boot = command(['docker','exec',CONTAINER,'cat',
                    '/usr/local/sbin/qdc507-mode-boot.sh']).stdout
    need(MODULE_SHA in boot and '8k) SRC=' not in boot,
         'V2_STARTUP_SELECTOR_NOT_16K_ONLY')
    # Check idle before persisting. This enrollment does not restart Asterisk/USB.
    p = command(['docker','exec',CONTAINER,'asterisk','-rx','core show channels count'])
    need(re.search(r'^\s*0 active channels\s*$',p.stdout,re.M) and
         re.search(r'^\s*0 active calls\s*$',p.stdout,re.M),
         'ENROLL_ONLY_WHEN_NO_CALLS')
    need(not PIN.exists() and not PIN.is_symlink(), 'V2_PIN_RACE')
    atomic_json(PIN, {
        'format':'qdc507-16k-memoryfix-v2-runtime-pin-v1',
        'container_id':obj['Id'], 'image_id':obj['Image'],
        'image_tag': IMAGE, 'module_sha256':MODULE_SHA,
        'backup_container':BACKUP, 'backup_image_id':old['Image']
    })
    print('V2_PIN_ENROLLED=1',flush=True)
    print('NO_DOCKER_OR_USB_STATE_CHANGED=1',flush=True)

def docker_idle(b):
    obj = inspect_v2()
    health = obj['State'].get('Health')
    if health:
        need(health.get('Status') == 'healthy', 'DOCKER_NOT_HEALTHY')
    p = command(['docker', 'exec', CONTAINER, 'asterisk', '-rx',
                 'core show channels count'])
    need(re.search(r'^\s*0 active channels\s*$', p.stdout, re.M) and
         re.search(r'^\s*0 active calls\s*$', p.stdout, re.M),
         'ACTIVE_OR_UNKNOWN_CALLS')
    out = command(['docker', 'exec', CONTAINER, 'asterisk', '-rx',
                   'quectel show device state quectel0']).stdout
    for name, value in [('State', 'Free'), ('Voice', 'Yes'),
                        ('SMS', 'Yes'), ('Calls/Channels', '0')]:
        need(re.search(r'^\s*' + re.escape(name) + r'\s*:\s*' + value + r'\s*$',
                       out, re.M), 'MODEM_' + name.replace('/', '_') + '_NOT_READY')
    need(re.search(r'^\s*LTE Registration Status\s*:\s*Registered, '
                   r'(home network|roaming)\s*$', out, re.M), 'SIM_NOT_REGISTERED')
    need(command(['systemctl', 'is-active', 'qdc507-sms-tick.timer'],
                 timeout=8).stdout.strip() == 'active', 'SMS_TIMER_NOT_ACTIVE')
    work = command(['systemctl', 'is-active', 'qdc507-sms-tick.service'],
                   timeout=8, allow_fail=True).stdout.strip()
    need(work == 'inactive', 'SMS_WORKER_BUSY_OR_UNKNOWN_' + work)
    print('V2_RUNTIME_PIN_AND_IDLE_GUARDS_OK=1',flush=True)

def devices():
    p = command([ADB, '-H', '127.0.0.1', '-P', '5037', 'devices', '-l'], timeout=12)
    need('List of devices attached' in p.stdout, 'ADB_RESPONSE_UNRECOGNIZED')
    rows = []
    for line in p.stdout.splitlines():
        cols = line.split()
        if len(cols) >= 2 and cols[1] in ('device', 'offline', 'unauthorized'):
            rows.append((cols[0], cols[1]))
    return rows

def adb_is_healthy(serial):
    rows = devices()
    need(all(d.lower() == serial.lower() for d, _ in rows),
         'OTHER_ADB_DEVICE_PRESENT_NO_RESET')
    need(len(rows) <= 1, 'ADB_DEVICE_LIST_AMBIGUOUS')
    if not rows or rows[0][1] != 'device':
        return False
    p = command([ADB, '-H', '127.0.0.1', '-P', '5037', '-s', serial,
                 'shell', 'id -u'], timeout=10, allow_fail=True)
    return p.returncode == 0 and p.stdout.strip().replace('\r', '') == '0'

def preflight(b):
    gen, node = usb_snapshot(b)
    docker_idle(b)
    need(not adb_is_healthy(b['serial']), 'ADB_ALREADY_HEALTHY_NO_RESET')
    return gen, node

def run_reset():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_DIRECTORY_INVALID')
    fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        st = os.fstat(fd)
        need(st.st_uid == 0 and stat.S_ISREG(st.st_mode) and
             not (stat.S_IMODE(st.st_mode) & 0o077), 'RESET_LOCK_UNTRUSTED')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('ANOTHER_ADB_RESET_IN_PROGRESS')
        b = baseline()
        gen, node = preflight(b)
        print('RESET_GUARD_INITIAL_OK=1 USB=' + ':'.join(gen[:3]), flush=True)
        journal = state_file(JOURNAL)
        if journal and time.time() - float(journal.get('epoch', 0)) < COOLDOWN_SECONDS:
            raise Stop('RESET_COOLDOWN_ACTIVE_NO_RETRY')
        time.sleep(3)
        gen2, node2 = preflight(b)
        need(gen == gen2 and node == node2, 'USB_CHANGED_DURING_PRECHECK')
        print('RESET_GUARD_RECHECK_OK=1', flush=True)
        # The journal is written *before* the disruptive action.  A crash must
        # never cause repeated unattended resets.
        atomic_json(JOURNAL, {'epoch': time.time(), 'usb_generation': gen,
                              'phase': 'reset_attempted'})
        print('RESET_ATTEMPT_RECORDED=1', flush=True)
        command([ADB, '-H', '127.0.0.1', '-P', '5037', 'kill-server'],
                timeout=15, allow_fail=True)
        # The exact USB sysfs generation must still match immediately before reset.
        # Avoid ever applying the ioctl to a replacement USB device node.
        gen3, node3 = usb_snapshot(b)
        need(gen3 == gen and node3 == node, 'USB_CHANGED_BEFORE_RESET')
        print('USB_RESET_TARGET=' + str(node), flush=True)
        usbfd = os.open(str(node), os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
        try:
            usbstat = os.fstat(usbfd)
            need(stat.S_ISCHR(usbstat.st_mode) and os.major(usbstat.st_rdev) == 189,
                 'USB_NODE_CHANGED_BEFORE_IOCTL')
            fcntl.ioctl(usbfd, RESET_IOCTL, 0)
        finally:
            os.close(usbfd)
        print('USB_RESET_IOCTL_OK=1', flush=True)
        time.sleep(5)
        p = command(['/usr/bin/python3.8', '-I', '-B', BOOT], timeout=30)
        need('BOOT_ADB_EXIT=0' in p.stdout, 'ADB_BOOT_FAILED')
        for _ in range(15):
            if adb_is_healthy(b['serial']):
                break
            time.sleep(1)
        else:
            raise Stop('ADB_NOT_ONLINE_AFTER_USB_RESET')
        print('ADB_RECOVERED=1', flush=True)
        new_gen, _ = usb_snapshot(b)
        need(new_gen == gen, 'USB_GENERATION_CHANGED_NEEDS_EXISTING_RECOVERY')
        docker_idle(b)
        p = command(['/usr/bin/python3.8', '-I', '-B', str(GUARD), '--check'],
                    timeout=85)
        need('READ_ONLY_RUNTIME_CHECK_OK=1' in p.stdout and
             'DEVICE_CURRENT_MODE=v2' in p.stdout, 'GUARD16_V2_READ_ONLY_CHECK_FAILED')
        print('GUARD16_V2_OK=1', flush=True)
        print('ADB_USB_RESET_RECOVERY_OK=1', flush=True)
    finally:
        os.close(fd)

def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--reset', action='store_true')
    g.add_argument('--enroll', action='store_true')
    g.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    try:
        if args.enroll:
            enroll()
        elif args.selftest:
            assert len(MODULE_SHA) == 64 and IMAGE.endswith('20261118')
            print('V2_ADB_RECOVERY_OFFLINE_SELFTEST_OK=1')
        elif args.check:
            b = baseline()
            gen, node = usb_snapshot(b)
            docker_idle(b)
            healthy = adb_is_healthy(b['serial'])
            print('USB_GENERATION=' + ':'.join(gen[:3]))
            print('USB_NODE=' + str(node))
            print('ADB_HEALTHY=' + str(int(healthy)))
            print('RESET_STATIC_GUARDS_OK=1')
            print('RESET_WOULD_BE_NEEDED=' + str(int(not healthy)))
        else:
            run_reset()
    except (Stop, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        print('RECOVERY_DEFER=' + str(e), file=sys.stderr, flush=True)
        return 2
    return 0

if __name__ == '__main__':
    sys.exit(main())
