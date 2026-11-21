#!/usr/bin/python3.8
"""Fail-closed QDC507-only DSM 4.4.302+ boot coordinator (v2 candidate).
--check is read-only; --selftest is fully mocked; --recover ONLY for a stopped
production container. No forced USB rebinding, no Docker daemon restart,
no touch to SIM7600CE. Must pass an actual controlled cold-boot test.
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
LOCK = LAB / 'qdc507-dsm74-boot-v2.lock'
ADB_SHA256 = 'a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01'

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

def api(method, endpoint, payload=None):
    c = Docker()
    try:
        body = json.dumps(payload) if payload is not None else None
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        c.request(method, '/v1.43' + endpoint, body=body, headers=headers)
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

def wait_container(seconds=180):
    """Wait only for daemon/container discovery; never hide an identity mismatch."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return inspect()
        except Stop as exc:
            if str(exc) not in ('STOP_DOCKER_INSPECT_404',
                                'STOP_DOCKER_INSPECT_503'):
                raise
        except (OSError, TimeoutError, http.client.HTTPException):
            pass
        if time.monotonic() >= deadline:
            raise Stop('STOP_DOCKER_NOT_READY_WITHIN_LIMIT')
        time.sleep(5)


def wait_device(seconds=90):
    """Read-only USB enumeration wait; wrong identities abort, not retry."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return device()
        except Stop as exc:
            if str(exc) != 'STOP_QDC_USB_NOT_UNIQUE':
                raise
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise Stop('STOP_QDC_USB_NOT_READY_WITHIN_LIMIT')
        time.sleep(3)


def wait_discover(seconds=90):
    """USB IF interfaces must settle naturally: NEVER rebind live USB."""
    deadline = time.monotonic() + seconds
    last = 'not-yet-enumerated'
    while True:
        need(not inspect()['State']['Running'], 'EXTERNAL_CONTAINER_START_DURING_DISCOVERY')
        try:
            return discover()
        except (Exception,) as exc:
            last = repr(exc)[:200]
        if time.monotonic() >= deadline:
            raise Stop('STOP_QDC_INTERFACES_NOT_READY_' + last)
        time.sleep(3)


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

def ensure_docker_network_modules():
    """Restore only kernel bridge dependencies before waiting for Docker daemon."""
    for name in ('bridge', 'br_netfilter'):
        if not loaded(name):
            run(['/sbin/modprobe', name])
        need(loaded(name), 'DOCKER_NET_MODULE_NOT_LOADED_' + name)
    print('DOCKER_NETWORK_MODULES_READY=1', flush=True)


def initial_inspect_if_ready():
    """Return None only for temporary Docker availability issues."""
    try:
        return inspect()
    except Stop as exc:
        if str(exc) in ('STOP_DOCKER_INSPECT_404',
                        'STOP_DOCKER_INSPECT_503'):
            return None
        raise
    except (OSError, TimeoutError, http.client.HTTPException):
        return None


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
    need(ADB.is_file() and not ADB.is_symlink() and KEY.is_file()
         and not KEY.is_symlink(), 'ADB_OR_KEY_MISSING')
    need(ADB.stat().st_uid == 0 and
         hashlib.sha256(ADB.read_bytes()).hexdigest() == ADB_SHA256,
         'ADB_BINARY_HASH_CHANGED')
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

def exec_asterisk(command):
    """Read-only Asterisk CLI via Docker Engine; never use broken docker CLI."""
    code, data = api('POST', '/containers/' + NAME + '/exec', {
        'AttachStdout': True, 'AttachStderr': True, 'Tty': False,
        'Cmd': ['/usr/sbin/asterisk', '-rx', command]
    })
    need(code == 201, 'ASTERISK_EXEC_CREATE_FAILED_' + str(code))
    eid = json.loads(data)['Id']
    code, data = api('POST', '/exec/' + eid + '/start',
                     {'Detach': False, 'Tty': False})
    need(code == 200, 'ASTERISK_EXEC_START_FAILED_' + str(code))
    chunks = []
    off = 0
    while off + 8 <= len(data):
        n = int.from_bytes(data[off+4:off+8], 'big')
        off += 8
        need(n <= len(data)-off, 'ASTERISK_EXEC_TRUNCATED')
        chunks.append(data[off:off+n].decode('utf-8', 'replace'))
        off += n
    need(off == len(data), 'ASTERISK_EXEC_MALFORMED')
    return ''.join(chunks)


def verify_quectel_after_start(seconds=125):
    """Never restart a running container. Just wait for quectel0 Free."""
    deadline = time.monotonic() + seconds
    last = 'not checked'
    while True:
        need(inspect()['State']['Running'], 'ASTERISK_STOPPED_DURING_READINESS')
        try:
            last = exec_asterisk('quectel show devices')
            entries = [x.split() for x in last.splitlines()]
            q = [x for x in entries if x and x[0] == 'quectel0']
            s = [x for x in entries if x and x[0] == 'sim7600ce']
            if len(s) == 1 and len(s[0]) >= 3:
                need(s[0][2] == 'Stopped', 'SIM7600CE_STATE_CHANGED')
            if len(q) == 1 and len(q[0]) >= 4 and q[0][2] == 'Free':
                print('QDC507_MODEM_FREE=1', flush=True)
                return
        except Stop as exc:
            if str(exc) == 'STOP_SIM7600CE_STATE_CHANGED':
                raise
            last = str(exc)
        if time.monotonic() >= deadline:
            raise Stop('STOP_QDC507_NOT_FREE_POSTSTART_' + repr(last)[-300:])
        time.sleep(5)


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
    # The Docker engine may depend on bridge/br_netfilter on the upgraded DSM.
    # Inspect first: if running, make absolutely no host changes.
    prior = initial_inspect_if_ready()
    if prior is not None and prior['State']['Running']:
        print('RUNNING_NO_ACTION=1')
        return
    # When Docker is still unavailable, only restore its kernel network base.
    # Do not start/restart Docker here; let DSM own its package lifecycle.
    if prior is None:
        ensure_docker_network_modules()
        prior = wait_container()
        if prior['State']['Running']:
            print('RUNNING_NO_ACTION=1')
            return
    need(prior['State']['Status'] == 'exited' and
         prior['State']['ExitCode'] == 0 and
         not prior['State']['OOMKilled'] and
         not prior['State'].get('Error'), 'CONTAINER_EXIT_NOT_CLEAN')
    wait_device()
    # Do not attempt any host changes before verifying the production identity.
    load_host_modules()
    c = inspect()
    need(not c['State']['Running'], 'CONTAINER_BECAME_RUNNING')
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
        g, mapping = wait_discover()
        need(not inspect()['State']['Running'], 'EXTERNAL_CONTAINER_START_BEFORE_NODE_SYNC')
        sync_nodes(g, mapping)
        ready_adb()
        need(device().name == g[0], 'USB_CHANGED_BEFORE_VOICE')
        recover_voice(inspect())
        need(not inspect()['State']['Running'], 'CONTAINER_STARTED_EXTERNALLY')
        code, data = api('POST', '/containers/' + NAME + '/start')
        need(code == 204, 'CONTAINER_START_FAILED_' + str(code) + '_' +
             data[:150].decode(errors='replace'))
        print('PRODUCTION_START_ACCEPTED=1', flush=True)
        verify_quectel_after_start()
        print('QDC507_BOOT_RECOVERED=1', flush=True)
    finally:
        if timer_was_active:
            run(['systemctl','start','qdc507-sms-tick.timer'])
            print('SMS_TIMER_RESTORED=1', flush=True)


def selftest():
    """Non-invasive selftest: no device access, Docker API, or mutations."""
    global run, wait_container, initial_inspect_if_ready, ensure_docker_network_modules
    global load_host_modules, api, wait_device
    global service_active, recover_voice, sync_nodes, ready_adb
    global device
    def fake_run(args, timeout=25):
        need(args == ['/bin/uname', '-r'], 'SELFTEST_UNEXPECTED_SHELL')
        return '4.4.302+'
    def fake_wait(seconds=180):
        return {'State': {'Running': True}}
    def forbidden(*a, **kw):
        raise Stop('STOP_SELFTEST_UNEXPECTED_SIDE_EFFECT')
    run = fake_run
    wait_container = fake_wait
    initial_inspect_if_ready = fake_wait
    ensure_docker_network_modules = forbidden
    load_host_modules = forbidden
    wait_device = forbidden
    api = forbidden
    service_active = forbidden
    recover_voice = forbidden
    sync_nodes = forbidden
    ready_adb = forbidden
    device = forbidden
    recover()
    print('RUNNING_NO_ACTION_SELFTEST_OK=1')


if __name__ == '__main__':
    try:
        need(sys.argv[1:] in (['--check'],['--selftest'],['--recover']), 'USAGE_CHECK_SELFTEST_OR_RECOVER')
        if sys.argv[1] == '--check':
            check()
        elif sys.argv[1] == '--selftest':
            selftest()
        else:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'r+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                recover()
    except Exception as e:
        print('BOOT_COORDINATOR_ABORT=' + repr(e), flush=True)
        sys.exit(2)
