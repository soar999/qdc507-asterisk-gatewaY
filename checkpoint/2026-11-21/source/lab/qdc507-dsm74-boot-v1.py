#!/usr/bin/python3.8
"""Fail-closed QDC507-only DSM 4.4.302+ boot coordinator.
--check is read-only; --recover is only for a stopped production container.
Never reboot USB, restart Docker, or touch SIM7600CE.
"""
import os
import sys
import time
import stat
import json
import socket
import hashlib
import fcntl
import http.client
import subprocess
import importlib.util
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
KMOD = LAB / 'qdc507-dsm74-kmod-build-v1/output-4.4.302'
ORIGINAL = LAB / 'qdc507-dynusb-oneclick-memoryfix-v22.py'
NODES = LAB / 'qdc507-device-node-sync-memoryfix-v22.py'
ADB = Path('/usr/local/libexec/qdc507/adb')
KEY = LAB / 'qdc507-adbkey'
SER = '<REDACTED_USB_SERIAL>'
NAME = 'qdc507-hardware'
CID = '<REDACTED_CONTAINER_ID>'
IMAGE = 'sha256:<REDACTED_IMAGE_ID>'
TAG = 'qdc507-asterisk:16k-memoryfix-v2-candidate-20261118'
HASHES = {
 'usb_wwan.ko': '64daad5a5cee0af48460ae7b1565d31665f5339cde5125f05479b1796b04698f',
 'option.ko': '4bec9549a3375aeae125121e8084814ce0bb4505ff4e11a63b80e5b455c77560',
}
LOCK = LAB / 'qdc507-dsm74-boot-v1.lock'

class Stop(Exception):
    pass

def need(ok, reason):
    if not ok:
        raise Stop('STOP_' + reason)

def run(args, timeout=25):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL, universal_newlines=True,
                       timeout=timeout)
    need(p.returncode == 0, 'COMMAND_FAILED_' + Path(args[0]).name + '_' + p.stderr[-150:])
    return p.stdout.strip()

class Docker(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost', timeout=15)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(15)
        self.sock.connect('/var/run/docker.sock')

def api(method, endpoint):
    c = Docker()
    try:
        c.request(method, '/v1.43' + endpoint)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()

def inspect():
    code, data = api('GET', '/containers/' + NAME + '/json')
    need(code == 200, 'DOCKER_INSPECT_' + str(code))
    c = json.loads(data)
    need(c['Id'] == CID and c['Image'] == IMAGE and c['Config']['Image'] == TAG,
         'PRODUCTION_IDENTITY_CHANGED')
    need(c['HostConfig']['RestartPolicy']['Name'] == 'no', 'RESTART_POLICY_CHANGED')
    return c

def device():
    candidates = []
    for d in Path('/sys/bus/usb/devices').iterdir():
        try:
            if (d/'idVendor').read_text().strip().lower() == '2ca3' and \
               (d/'idProduct').read_text().strip().lower() == '4006':
                candidates.append(d)
        except (OSError, ValueError):
            pass
    need(len(candidates) == 1, 'QDC_USB_NOT_UNIQUE')
    d = candidates[0]
    need((d/'serial').read_text().strip().lower() == SER, 'QDC_USB_SERIAL_CHANGED')
    return d

def loaded(name):
    return any(line.startswith(name + ' ') for line in
               Path('/proc/modules').read_text().splitlines())

def load_host_modules():
    need(run(['/bin/uname', '-r']) == '4.4.302+', 'KERNEL_CHANGED')
    for name, digest in HASHES.items():
        p = KMOD / name
        need(p.is_file() and not p.is_symlink(), 'MODULE_NOT_REGULAR_' + name)
        need(hashlib.sha256(p.read_bytes()).hexdigest() == digest,
             'MODULE_HASH_CHANGED_' + name)
    for name in ('bridge', 'br_netfilter', 'usbserial'):
        if not loaded(name):
            run(['/sbin/modprobe', name])
        need(loaded(name), 'MODULE_NOT_LOADED_' + name)
    for name, module in (('usb_wwan','usb_wwan.ko'), ('option','option.ko')):
        if not loaded(name):
            run(['/sbin/insmod', str(KMOD/module)])
        need(loaded(name), 'MODULE_NOT_LOADED_' + name)
    if not loaded('snd_usb_audio'):
        run(['/sbin/modprobe', 'snd-usb-audio'])
    need(loaded('snd_usb_audio'), 'SOUND_MODULE_NOT_LOADED')
    print('HOST_KERNEL_MODULES_READY=1', flush=True)

def module_from(path, name):
    need(path.is_file(), 'SOURCE_NOT_FOUND_' + name)
    spec = importlib.util.spec_from_file_location(name, str(path))
    need(spec is not None and spec.loader is not None, 'SOURCE_IMPORT_FAILED_' + name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def discover():
    mod = module_from(NODES, 'qdc507_node_roles')
    generation, mapping = mod.discover()
    need(generation[3].lower() == SER, 'USB_GENERATION_CHANGED')
    return generation, mapping

def sync_nodes(g, mapping):
    folder = Path('/dev/qdc507')
    if os.path.lexists(str(folder)):
        st = folder.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not folder.is_symlink(),
             'DEVICE_FOLDER_UNTRUSTED')
    else:
        folder.mkdir(mode=0o755)
    os.chmod(str(folder), 0o755)
    for role in ('at','pcm16'):
        source, rdev = mapping[role]
        need(Path(source).is_char_device() and os.major(rdev) == 188,
             'SOURCE_NODE_INVALID_' + role)
        path = folder / role
        if os.path.lexists(str(path)):
            s = path.lstat()
            need(stat.S_ISCHR(s.st_mode) and s.st_uid == 0,
                 'EXISTING_NODE_UNTRUSTED_' + role)
            if s.st_rdev != rdev:
                path.unlink()
        if not os.path.lexists(str(path)):
            os.mknod(str(path), stat.S_IFCHR | 0o660, rdev)
        os.chown(str(path), 0, 1000)
        os.chmod(str(path), 0o660)
        s = path.lstat()
        need(s.st_rdev == rdev and s.st_gid == 1000 and
             stat.S_IMODE(s.st_mode) == 0o660, 'NODE_POSTCHECK_' + role)
        print('NODE_' + role.upper() + '=' + source)
    g2, m2 = discover()
    need(g2 == g and m2 == mapping, 'USB_CHANGED_DURING_NODE_SYNC')
    print('QDC507_NODES_READY=1', flush=True)

def ready_adb():
    need(ADB.is_file() and KEY.is_file(), 'ADB_OR_KEY_MISSING')
    need(KEY.stat().st_uid == 0 and stat.S_IMODE(KEY.stat().st_mode) == 0o600,
         'ADB_KEY_PERMISSIONS_CHANGED')
    cfg = LAB / '.android/adb_usb.ini'
    need(cfg.is_file() and '0x2ca3' in cfg.read_text().lower(),
         'ADB_VENDOR_ID_NOT_CONFIGURED')
    os.environ['HOME'] = str(LAB)
    os.environ['ADB_VENDOR_KEYS'] = str(KEY)
    run([str(ADB), '-H', '127.0.0.1', '-P', '5037', 'start-server'], timeout=20)
    for attempt in range(10):
        output = run([str(ADB), '-H', '127.0.0.1', '-P', '5037', 'devices', '-l'], timeout=15)
        lines = [line.split() for line in output.splitlines()]
        selected = [x for x in lines if x and x[0].lower() == SER]
        if len(selected) == 1 and len(selected[0]) >= 3 and \
           selected[0][1] == 'device' and \
           'usb:' + device().name in selected[0]:
            print('QDC507_ADB_AUTHORIZED=1', flush=True)
            return
        if selected and any(x[1] == 'unauthorized' for x in selected if len(x) > 1):
            raise Stop('STOP_QDC507_ADB_UNAUTHORIZED_NO_SERVER_RESTART')
        time.sleep(2)
    raise Stop('STOP_QDC507_ADB_NOT_READY')

def recover_voice(c):
    need(not c['State']['Running'], 'CONTAINER_ALREADY_RUNNING')
    mod = module_from(ORIGINAL, 'qdc507_existing_voice')
    mod.docker = inspect  # Work around broken docker CLI without modifying original code
    need(mod.remote(SER, 'id -u') == '0', 'QDC_ADB_NOT_ROOT')
    flags = mod.remote_flags(SER)
    if all(flags[x] for x in ('qdc507_aprv3','qdc507_voice','helper','calibrated')):
        print('DEVICE_VOICE_ALREADY_READY=1', flush=True)
        return
    need(not flags['qdc507_aprv3'] and not flags['qdc507_voice'] and
         flags['audio_idle'], 'VOICE_PARTIAL_STATE_MANUAL_REVIEW')
    mod.runtime_sources()
    mod.prepare_voice(SER, flags)
    after = mod.remote_flags(SER)
    need(all(after[x] for x in ('qdc507_aprv3','qdc507_voice','helper','calibrated')),
         'VOICE_RESTORE_POSTCHECK_FAILED')
    print('QDC507_INTERNAL_VOICE_READY=1', flush=True)

def service_active(unit):
    p = subprocess.run(['systemctl','is-active',unit], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    return p.stdout.strip() == 'active'

def check():
    print('KERNEL=' + run(['/bin/uname','-r']))
    print('DRIVERS=' + ','.join(k + ':' + str(int(loaded(k))) for k in
        ('bridge','br_netfilter','usbserial','usb_wwan','option','snd_usb_audio')))
    print('QDC_USB=' + device().name)
    c = inspect()
    print('CONTAINER_STATE=' + c['State']['Status'])
    print('SMS_TIMER_ACTIVE=' + str(int(service_active('qdc507-sms-tick.timer'))))
    try:
        g, mapping = discover()
        print('DYNAMIC_USB=' + g[0])
        for role,(path,rdev) in mapping.items():
            target = Path('/dev/qdc507')/role
            s = target.lstat() if os.path.lexists(str(target)) else None
            good = bool(s and stat.S_ISCHR(s.st_mode) and s.st_rdev == rdev and
                        s.st_uid == 0 and s.st_gid == 1000 and
                        stat.S_IMODE(s.st_mode) == 0o660 and
                        stat.S_IMODE(Path('/dev/qdc507').stat().st_mode) == 0o755)
            print('NODE_' + role.upper() + '_READY=' + str(int(good)))
    except Exception as e:
        print('DYNAMIC_USB_CHECK_UNAVAILABLE=' + repr(e)[:250])
    print('CHECK_IS_READ_ONLY=1')

def recover():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(run(['/bin/uname', '-r']) == '4.4.302+', 'KERNEL_CHANGED')
    need((LAB/'qdc507-mode-upgrade-c3e-preaudio-v2/mode/current').read_text().strip() == '16k',
         'MODE_NOT_16K')
    device()
    # If Asterisk is healthy, do nothing, including no kernel, ADB or timer changes.
    try:
        prior = inspect()
    except (OSError, ConnectionError):
        prior = None
    if prior is not None and prior['State']['Running']:
        print('RUNNING_NO_ACTION=1')
        return
    load_host_modules()
    for attempt in range(12):
        try:
            c = inspect()
            break
        except (OSError, ConnectionError):
            time.sleep(5)
    else:
        raise Stop('STOP_DOCKER_UNAVAILABLE_NO_DAEMON_RESTART')
    need(not c['State']['Running'], 'CONTAINER_BECAME_RUNNING')
    need(c['State']['Status'] == 'exited', 'CONTAINER_NOT_EXITED')
    mounts = {m['Destination']: m for m in c['Mounts']}
    need(mounts.get('/dev/qdc507',{}).get('Source') == '/dev/qdc507',
         'CONTAINER_DEVICE_MOUNT_CHANGED')
    need('c 188:* rwm' in (c['HostConfig'].get('DeviceCgroupRules') or []),
         'CONTAINER_DEVICE_CGROUP_CHANGED')
    need(not service_active('qdc507-sms-tick.service'), 'SMS_WORKER_ACTIVE')
    timer_was_active = service_active('qdc507-sms-tick.timer')
    if timer_was_active:
        run(['systemctl','stop','qdc507-sms-tick.timer'])
        print('SMS_TIMER_TEMPORARILY_PAUSED=1', flush=True)
    try:
        need(not service_active('qdc507-sms-tick.service'), 'SMS_WORKER_ACTIVE')
        g, mapping = discover()
        sync_nodes(g, mapping)
        ready_adb()
        need(device().name == g[0], 'USB_CHANGED_BEFORE_VOICE')
        recover_voice(inspect())
        need(not inspect()['State']['Running'], 'CONTAINER_STARTED_EXTERNALLY')
        code, data = api('POST', '/containers/' + NAME + '/start')
        need(code == 204, 'CONTAINER_START_FAILED_' + str(code) + '_' +
             data[:150].decode(errors='replace'))
        print('PRODUCTION_START_ACCEPTED=1', flush=True)
        time.sleep(8)
        final = inspect()['State']
        need(final['Running'], 'PRODUCTION_EXITED_' + str(final['ExitCode']))
        print('QDC507_ASTERISK_RUNNING=1', flush=True)
    finally:
        if timer_was_active:
            run(['systemctl','start','qdc507-sms-tick.timer'])
            print('SMS_TIMER_RESTORED=1', flush=True)

if __name__ == '__main__':
    try:
        need(sys.argv[1:] in (['--check'],['--recover']), 'USAGE_CHECK_OR_RECOVER')
        if sys.argv[1] == '--check':
            check()
        else:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'r+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                recover()
    except Exception as e:
        print('BOOT_COORDINATOR_ABORT=' + repr(e), flush=True)
        sys.exit(2)
