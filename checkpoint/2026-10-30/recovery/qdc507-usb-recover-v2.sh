#!/bin/sh
# QDC507 USB unplug / SIM swap / replug -- production entrypoint revision 2.
# No modification of the pinned oneclick, hotplug, or Guard16 helpers.
# --check: read-only; --recover (or no argument): exactly one guarded attempt.
set -eu
[ "$#" -le 1 ] || { echo 'USB_SIM_SWAP_INVALID_ARGUMENT_COUNT' >&2; exit 2; }
exec /usr/bin/python3.8 -I -B - "${1:---recover}" <<'PY'
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
HOT = LAB / 'qdc507-hotplug-recovery-v1.py'
ONE = LAB / 'qdc507-dynusb-oneclick-v2.py'
GUARD = LAB / 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
V2 = LAB / 'qdc507-guard16-v2-runtime-restore-v2.py'
BASE = LAB / 'qdc507-dynusb-oneclick-baseline-v1.json'
PREP = Path('/usr/local/sbin/qdc507-boot-adb.py')
ADB = '/usr/local/libexec/qdc507/adb'
CONTAINER = 'qdc507-hardware'
LOCK = '/run/qdc507-usb-sim-swap-v2.lock'
PENDING = (
    'qdc507-dynusb-oneclick-pending-v1.json',
    'qdc507-dynusb-mode-switch-pending.json',
    'qdc507-usb-recovery-v4-pending.json',
    'qdc507-dynusb-cutover-pending-v1.json',
)
PINS = {
    HOT: '4da6b80b65aa9bdb519dcc698be91bf098e23a1ec69e742aa99db4661c5c718c',
    ONE: 'd3817c52f19e6f50d03605ee4e478c07e022b442091f0b90d64f52d6a2a4160c',
    GUARD: '9e9398a77e39d711b0009dbc5938a18c1f9387925f3c6541ae215331d9a9b730',
    V2: '077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55',
}
PREAUDIO_SHA = '1332d6fcf5a4b3f4f7a70180e8632992e30325256b415a4f8fc6417cf7bc0665'
CONTAINER_ID = 'cd1f8d1ccc3d3cd465cfefc1c87cc8521cb9979c95333d0f4e60b6f829115fd4'
IMAGE_ID = 'sha256:1d55612e888a736f372f275a47d6ae3f66f7843fe27ea6673ba68f6a8281e6ca'
PYTHON = '/usr/bin/python3.8'

class Halt(Exception): pass

def need(ok, msg):
    if not ok:
        raise Halt(msg)

def emit(s):
    print(s, flush=True)

def command(argv, timeout=30, allow_fail=False, passthrough=False):
    try:
        kwargs = dict(stdin=subprocess.DEVNULL, timeout=timeout)
        if passthrough:
            p = subprocess.run(list(map(str, argv)), **kwargs)
        else:
            p = subprocess.run(list(map(str, argv)), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, **kwargs)
    except subprocess.TimeoutExpired:
        raise Halt('COMMAND_TIMED_OUT_NO_AUTOMATIC_RETRY_' + str(argv[0]))
    except OSError as e:
        raise Halt('COMMAND_UNAVAILABLE_' + str(argv[0]) + '_' + type(e).__name__)
    if not allow_fail:
        err = '' if passthrough else (p.stderr or p.stdout)[-220:]
        need(p.returncode == 0, 'COMMAND_FAILED_' + str(argv[0]) + '_' + err)
    return p

def trusted_bytes(path, low=1, high=200000, private=False):
    st = path.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1 and
         not (stat.S_IMODE(st.st_mode) & (0o077 if private else 0o022)) and
         low <= st.st_size <= high, 'UNTRUSTED_FILE_' + path.name)
    return path.read_bytes()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def baseline():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(os.uname().release == '4.4.180+', 'NAS_KERNEL_VERSION_CHANGED')
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_PATH_NOT_TRUSTED')
    for path, expected in PINS.items():
        need(digest(trusted_bytes(path, 1000, 100000)) == expected,
             'RECOVERY_SCRIPT_SHA_CHANGED_' + path.name)
    b = json.loads(trusted_bytes(BASE, 100, 100000, private=True))
    need(b.get('format') == 'qdc507-dynusb-oneclick-v1' and b.get('mode') == '16k' and
         b.get('container_id') == CONTAINER_ID and b.get('image_id') == IMAGE_ID,
         'USB_BASELINE_CONTAINER_OR_MODE_CHANGED')
    need(b.get('scripts',{}).get(ONE.name) == PINS[ONE] and
         b.get('scripts',{}).get(GUARD.name) == PINS[GUARD],
         'RECOVERY_BASELINE_SCRIPT_PINS_CHANGED')
    need(re.fullmatch(r'[A-Za-z0-9._:-]{4,120}', str(b.get('serial',''))),
         'USB_BASELINE_SERIAL_INVALID')
    need(b.get('host_boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'NAS_BOOT_CHANGED_USE_COLDBOOT_NOT_USB_RECOVERY')
    for name in PENDING:
        need(not os.path.lexists(str(LAB / name)), 'EXISTING_RECOVERY_PENDING_REVIEW_' + name)
    return b

def usb_generation(serial):
    found = []
    for p in Path('/sys/bus/usb/devices').iterdir():
        try:
            if (p/'idVendor').read_text().strip().lower() != '2ca3' or \
               (p/'idProduct').read_text().strip().lower() != '4006':
                continue
            found.append([p.name, (p/'busnum').read_text().strip(),
                          (p/'devnum').read_text().strip(), (p/'serial').read_text().strip()])
        except OSError:
            continue
    need(len(found) == 1, 'EXPECTED_EXACTLY_ONE_QDC507_USB_DEVICE')
    need(found[0][3].lower() == serial.lower(), 'QDC507_USB_SERIAL_CHANGED')
    return found[0]

def container():
    data = json.loads(command(['docker', 'inspect', CONTAINER], timeout=20).stdout)
    need(len(data) == 1 and data[0].get('Id') == CONTAINER_ID and
         data[0].get('Image') == IMAGE_ID and
         data[0].get('State', {}).get('Running') is True,
         'PRODUCTION_CONTAINER_ID_IMAGE_OR_RUNNING_STATE_INVALID')
    return data[0]

def calls_idle():
    text = command(['docker','exec',CONTAINER,'asterisk','-rx',
                    'core show channels count'], timeout=15).stdout
    need(re.search(r'^\s*0 active channels\s*$', text, re.M) and
         re.search(r'^\s*0 active calls\s*$', text, re.M),
         'ACTIVE_OR_UNKNOWN_VOICE_CALLS')
    lines = command(['docker','exec',CONTAINER,'asterisk','-rx',
                     'core show channels concise'], timeout=15).stdout
    for line in lines.splitlines():
        line = line.strip()
        if line and line.split('!',1)[0] != 'Message/ast_msg_queue' and not re.fullmatch(
            r'\d+\s+(?:active\s+channels?|active\s+calls?|calls?\s+processed)\.?',
            line, re.I):
            raise Halt('UNEXPECTED_ACTIVE_CHANNEL')

def modem_ready():
    out = command(['docker','exec',CONTAINER,'asterisk','-rx',
                   'quectel show device state quectel0'],timeout=18).stdout
    required = (('State','Free'),('Voice','Yes'),('SMS','Yes'),
                ('Calls/Channels','0'))
    values = all(re.search(r'^\s*' + re.escape(k) + r'\s*:\s*' +
                           re.escape(v) + r'\s*$', out, re.M)
                 for k,v in required)
    # LTE registration can be home or valid roaming, but never unknown/denied.
    registered = re.search(
        r'^\s*LTE Registration Status\s*:\s*Registered, (?:home network|roaming)\s*$',
        out, re.M) is not None
    return values and registered

def adb_server_running():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        return s.connect_ex(('127.0.0.1',5037)) == 0
    finally:
        s.close()

def adb_list():
    if not adb_server_running():
        return None
    p = command([ADB,'-H','127.0.0.1','-P','5037','devices','-l'],
                timeout=17,allow_fail=True)
    need(p.returncode == 0, 'ADB_DEVICE_LIST_COMMAND_FAILED_REFUSE_BLIND_RESTART')
    need('List of devices attached' in p.stdout,
         'ADB_DEVICE_LIST_RESPONSE_UNRECOGNIZED')
    devices = []
    for line in p.stdout.replace('\r','').splitlines():
        words = line.split()
        if len(words) >= 2 and words[1] in ('device','offline','unauthorized'):
            devices.append((words[0],words[1]))
    return devices

def adb_online(serial):
    devices = adb_list()
    if devices is None:
        return False
    # Never reset ADB service if some OTHER device is connected to it.
    need(all(ident.lower() == serial.lower() for ident,_ in devices),
         'OTHER_ADB_DEVICE_PRESENT_REFUSE_RESTART')
    need(len(devices) <= 1, 'ADB_DEVICE_LIST_AMBIGUOUS')
    if devices != [(serial,'device')]:
        return False
    p = command([ADB,'-H','127.0.0.1','-P','5037','-s',serial,'shell','id -u'],
                timeout=17, allow_fail=True)
    return p.returncode == 0 and p.stdout.replace('\r','').strip() == '0'

def sms_idle():
    timer = command(['systemctl','is-active','qdc507-sms-tick.timer'],
                    timeout=12,allow_fail=True).stdout.strip()
    need(timer == 'active', 'SMS_TIMER_NOT_ACTIVE_BEFORE_RECOVERY_' + timer)
    worker = command(['systemctl','is-active','qdc507-sms-tick.service'],
                     timeout=12,allow_fail=True).stdout.strip()
    need(worker == 'inactive', 'SMS_WORKER_ACTIVE_OR_UNKNOWN_' + worker)

def check():
    b = baseline()
    c = container()
    if c['State'].get('Health'):
        need(c['State']['Health'].get('Status') == 'healthy',
             'PRODUCTION_CONTAINER_NOT_HEALTHY')
    calls_idle()
    source = command(['docker','exec',CONTAINER,'sha256sum',
                      '/opt/qdc16k-preaudio-manual/prepare.py'],timeout=15).stdout
    need(source.split()[0] == PREAUDIO_SHA, 'PREAUDIO_V1_V2_ALLOWLIST_MISSING')
    shot = usb_generation(b['serial'])
    emit('USB_GENERATION_CURRENT='+':'.join(shot[:3]))
    emit('USB_GENERATION_CHANGED='+str(int(shot != b['usb_generation'])))
    online = adb_online(b['serial'])
    emit('ADB_STATUS='+('online' if online else 'needs_single_restart'))
    emit('SIM_REGISTERED='+str(int(modem_ready())))
    emit('USB_SWAP_SOURCE_AND_PRODUCTION_PINS_OK=1')
    emit('USB_SWAP_READ_ONLY_CHECK_OK=1')
    return b,shot

def wait_sim_register(shot, serial):
    # Never make a pending journal just to discover an incorrectly inserted SIM.
    end = time.monotonic() + 75
    while time.monotonic() < end:
        need(usb_generation(serial) == shot, 'USB_CHANGED_DURING_SIM_CHECK')
        if modem_ready():
            emit('SIM_READY_BEFORE_TRANSACTION=1')
            return
        time.sleep(3)
    raise Halt('SIM_NOT_REGISTERED_NO_RECOVERY_TRANSACTION_STARTED_CHECK_SIM_SEATING_AND_PIN')

def recover():
    lock_fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(lock_fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and
             not stat.S_IMODE(s.st_mode) & 0o077, 'UNTRUSTED_USB_SWAP_LOCK')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Halt('ANOTHER_USB_SWAP_RECOVERY_ALREADY_RUNNING')
        b,shot = check()
        sms_idle()
        time.sleep(3)
        need(usb_generation(b['serial']) == shot, 'USB_NOT_STABLE_FOR_3_SECONDS')
        if not adb_online(b['serial']):
            emit('ADB_STALE_OR_OFFLINE_SINGLE_RESTART=1')
            if adb_server_running():
                # One bounded reset only. Never kill server with unrelated ADB devices.
                command([ADB,'-H','127.0.0.1','-P','5037','kill-server'],
                        timeout=20,allow_fail=True)
            p = command([PYTHON,'-I','-B',str(PREP)],timeout=38)
            need('BOOT_ADB_EXIT=0' in p.stdout, 'ADB_BOOT_GATE_DID_NOT_SUCCEED')
            end = time.monotonic() + 35
            while time.monotonic() < end:
                if adb_online(b['serial']):
                    break
                time.sleep(1)
            else:
                raise Halt('ADB_NOT_ONLINE_AFTER_SINGLE_RESTART_NO_TRANSACTION_STARTED')
            emit('ADB_REDISCOVERED_AFTER_USB_SWAP=1')
        else:
            emit('ADB_ALREADY_ONLINE_NO_RESTART=1')
        need(usb_generation(b['serial']) == shot, 'USB_CHANGED_AFTER_ADB_REDISCOVERY')
        # The SIM may be inserted incorrectly; stop BEFORE oneclick stops SMS.
        wait_sim_register(shot,b['serial'])
        calls_idle()
        sms_idle()
        # Check the existing coordinator and all its pins without writing to USB.
        p = command([PYTHON,'-I','-B',str(HOT),'--check'],timeout=115)
        need('READ_ONLY_COORDINATOR_CHECK_OK=1' in p.stdout,
             'HOTPLUG_PIN_PREFLIGHT_NOT_SUCCESSFUL')
        emit('HOTPLUG_ONECLICK_RECOVERY_BEGIN=1')
        # The existing hotplug coordinator acquires its own locks, and oneclick
        # seals the new USB generation only after completing full business checks.
        p = command([PYTHON,'-I','-B',str(HOT),'--recover'],
                    timeout=620, allow_fail=True, passthrough=True)
        need(p.returncode == 0,
             'HOTPLUG_OR_ONECLICK_FAILED_PRESERVE_PENDING_NO_AUTOMATIC_RETRY')
        emit('ONECLICK_RECOVERY_COMPLETE=1')
        latest = json.loads(trusted_bytes(BASE,100,100000,private=True))
        need(latest.get('usb_generation') == shot and
             latest.get('host_boot_id') == b['host_boot_id'],
             'ONECLICK_SUCCESS_BUT_BASELINE_NOT_SEALED')
        for name in PENDING:
            need(not os.path.lexists(str(LAB/name)),
                 'PENDING_EXISTS_AFTER_ONECLICK_' + name)
        need(usb_generation(b['serial']) == shot,
             'USB_CHANGED_AFTER_ONECLICK')
        emit('GUARD16_V2_ENSURE_BEGIN=1')
        p = command([PYTHON,'-I','-B',str(V2),'--ensure-v2'],
                    timeout=200,allow_fail=True,passthrough=True)
        need(p.returncode == 0,
             'GUARD16_V2_ENSURE_FAILED_MANUAL_CHECK_NO_AUTOMATIC_RETRY')
        final = command([PYTHON,'-I','-B',str(V2),'--check'],timeout=100).stdout
        need('DEVICE_CURRENT_MODE=v2' in final and
             'READ_ONLY_RUNTIME_CHECK_OK=1' in final and
             'DEVICE_V1_BACKUP=present' in final,
             'GUARD16_V2_FINAL_STATE_NOT_VERIFIED')
        need(modem_ready(), 'MODEM_NOT_READY_AT_FINAL_CHECK')
        sms_idle()
        need(usb_generation(b['serial']) == shot,
             'USB_CHANGED_AT_FINAL_CHECK')
        emit('USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1')
        emit('SAME_PRODUCTION_CONTAINER_AND_USB_BASELINE_PRESERVED=1')
        return
    finally:
        os.close(lock_fd)

def main():
    mode = sys.argv[1:]
    need(len(mode) == 1 and mode[0] in ('--check','--recover'),
         'USE_--check_OR_--recover')
    if mode[0] == '--check':
        check()
    else:
        recover()

if __name__ == '__main__':
    try:
        main()
    except (Halt, OSError, KeyError, ValueError, IndexError, BlockingIOError,
            subprocess.TimeoutExpired) as e:
        print('USB_SIM_SWAP_STOP='+type(e).__name__+':'+str(e)[:350],
              file=sys.stderr,flush=True)
        print('NO_AUTOMATIC_RETRY_KEEP_EXISTING_RECOVERY_JOURNAL=1',
              file=sys.stderr,flush=True)
        sys.exit(2)
PY
