#!/usr/bin/python3.8
"""Manual QDC507 same-container recovery for SIM swaps.
--enroll / --check are non-disruptive; --recover is an UNTESTED maintenance path.
No Docker recreate. Device driver writes only via existing audited helpers.
"""
import fcntl, hashlib, importlib.util, json, os, re, shlex, stat, subprocess, sys, tempfile, time
from pathlib import Path

os.environ['PATH']='/volume2/@appstore/Docker/usr/bin:/usr/local/bin:/usr/local/sbin:/usr/syno/bin:/usr/sbin:/usr/bin:/sbin:/bin'
R=Path('/volume2/docker/asterisk/qdc507-16k-lab')
LIVE='qdc507-hardware'
IMAGE='qdc507-asterisk:dualmode-remotehangup-v4-prod'
IID='sha256:1d55612e888a736f372f275a47d6ae3f66f7843fe27ea6673ba68f6a8281e6ca'
MOD='ca25e8e04a5ad9792282635a99e1d7152429800d6a51331ba18991e72436fa87'
C0='cd1f8d1ccc3d3cd465cfefc1c87cc8521cb9979c95333d0f4e60b6f829115fd4'
TIMER='qdc507-sms-tick.timer'
WORKER='qdc507-sms-tick.service'
ADB='/usr/local/libexec/qdc507/adb'
VOICE=Path('/usr/local/libexec/qdc507/runtime')
IF=R/'qdc507-if-role-manager-v1.py'
NODES=R/'qdc507-device-node-sync-v1.py'
PREP=Path('/usr/local/sbin/qdc507-boot-adb.py')
GUARD=R/'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
BASE=R/'qdc507-dynusb-oneclick-baseline-v1.json'
PENDING=R/'qdc507-dynusb-oneclick-pending-v1.json'
LOCK='/run/qdc507-dynusb-oneclick.lock'
H={'qdc507_aprv3.ko':'3d82d3dec4f1e323201bba87156df9d41438e08314097353f2607f9117211d4a',
   'qdc507_voice.ko':'ed3821682d5309969a01c764192c83feff9669c61ef237c69475cd1619cf296c',
   'mavo-pcm-bridge.armv7':'88d47c15e61d1428a59c821fed804c2e6490e82859a085062f21966b58d167fc'}

class Stop(Exception):pass

def need(ok, why):
    if not ok:raise Stop(why)

def run(a,timeout=20,allow=False):
    try:
        p=subprocess.run(list(map(str,a)),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired):
        raise Stop('COMMAND_UNAVAILABLE_OR_TIMEOUT_'+str(a[0]))
    if not allow and p.returncode:
        raise Stop('COMMAND_FAILED_'+str(a[:2])+':'+p.stderr[-120:])
    return p

def output(a,timeout=20):return run(a,timeout).stdout.strip()

def docker():
    p=run(['docker','inspect',LIVE],allow=True)
    need(p.returncode==0,'FORMAL_CONTAINER_NOT_FOUND')
    j=json.loads(p.stdout)
    need(len(j)==1,'FORMAL_CONTAINER_AMBIGUOUS')
    return j[0]

def lfile(p):
    need(p.is_file() and not p.is_symlink(),'SCRIPT_FILE_MISSING_OR_SYMLINK_'+p.name)

def atomic(path,doc):
    need(not path.is_symlink(),'OUTPUT_SYMLINK_'+path.name)
    fd,tmp=tempfile.mkstemp(prefix='.qdc507-oneclick-',dir=str(R))
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600)
            json.dump(doc,f,sort_keys=True,indent=2)
            f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,str(path))
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)

def load(path,name):
    lfile(path)
    sp=importlib.util.spec_from_file_location(name,str(path))
    need(sp and sp.loader,'IMPORT_FAILED_'+name)
    x=importlib.util.module_from_spec(sp);sp.loader.exec_module(x)
    return x

def lock(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    need(st.st_uid==0 and not st.st_mode & 0o077,'LOCK_UNTRUSTED')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return fd

def snap(require_ready=True):
    roles=load(IF,'qdc_role_oneclick')
    s=roles.stable()
    if require_ready:need(s['ready'],'IF0_IF8_NOT_READY')
    return s

def generation(s):return list(s['generation'])

# Bound enumeration wait for unplug/replug maintenance. Read-only during wait.
ENUM_WAIT_SECONDS=40
ENUM_STABLE_SECONDS=3

def usb_enum_snapshot(expected_serial):
    root=Path('/sys/bus/usb/devices')
    found=[]
    for d in root.iterdir():
        try:
            if (d/'idVendor').read_text().strip().lower()=='2ca3' and \
               (d/'idProduct').read_text().strip().lower()=='4006':
                found.append(d)
        except OSError:pass
    need(len(found)<=1,'MULTIPLE_QDC507_USB_DEVICES')
    if not found:return None
    d=found[0]
    try:
        serial=(d/'serial').read_text().strip()
        bus=(d/'busnum').read_text().strip()
        dev=(d/'devnum').read_text().strip()
    except OSError:return None
    need(serial.lower()==expected_serial.lower(),'QDC507_SERIAL_CHANGED')
    expected={5:(0xff,0x42,0x01),6:(1,1,0),7:(1,2,0),8:(1,2,0)}
    for n in range(9):
        p=root/(d.name+':1.'+str(n))
        if not p.is_dir():return None
        try:
            number=int((p/'bInterfaceNumber').read_text().strip(),16)
            typ=tuple(int((p/k).read_text().strip(),16) for k in
                      ('bInterfaceClass','bInterfaceSubClass','bInterfaceProtocol'))
        except (OSError,ValueError):return None
        need(number==n,'USB_IF_NUMBER_CHANGED_'+str(n))
        if n<5:
            need(typ[0]==0xff,'USB_IF_VENDOR_TYPE_CHANGED_'+str(n))
            if n==2:need(typ==(0xff,0,0),'USB_IF2_AT_TYPE_CHANGED')
        else:need(typ==expected[n],'USB_IF_TYPE_CHANGED_'+str(n))
    return (d.name,bus,dev,serial)

def wait_for_usb_enumeration():
    need(BASE.is_file() and not BASE.is_symlink(),'ONECLICK_NOT_ENROLLED')
    baseline=json.loads(BASE.read_text())
    need(baseline.get('container_id')==C0 and baseline.get('image_id')==IID,
         'BASELINE_CONTAINER_IMAGE_DRIFT')
    serial=baseline.get('serial')
    need(isinstance(serial,str) and bool(serial),'BASELINE_SERIAL_MISSING')
    print('USB_ENUM_WAIT_MAX_SECONDS='+str(ENUM_WAIT_SECONDS),flush=True)
    deadline=time.monotonic()+ENUM_WAIT_SECONDS
    last=None; since=None
    while time.monotonic()<deadline:
        sample=usb_enum_snapshot(serial)
        now=time.monotonic()
        if sample is None:
            last=None; since=None
        elif sample!=last:
            last=sample; since=now
        elif since is not None and now-since>=ENUM_STABLE_SECONDS:
            print('USB_ENUMERATION_STABLE=1',flush=True)
            return
        time.sleep(min(1.0,max(0.0,deadline-time.monotonic())))
    raise Stop('USB_ENUMERATION_TIMEOUT_40S_NO_BUSINESS_CHANGE')

def current_boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def formal():
    c=docker()
    need(c['Id']==C0 and c['Image']==IID and c['Config']['Image']==IMAGE,'FORMAL_ID_OR_IMAGE_DRIFT')
    m={x['Destination']:x for x in c['Mounts']}
    need(len(m)==14 and all(k in m for k in ('/dev/qdc507','/dev/snd','/etc/asterisk')),
         'DYNAMIC_MOUNTS_CHANGED')
    need(m['/dev/qdc507']['Source']=='/dev/qdc507' and not m['/dev/qdc507']['RW'],
         'FUNCTION_DEVICE_MOUNT_CHANGED')
    h=c['HostConfig']
    need(not h.get('Devices') and set(h.get('DeviceCgroupRules') or [])==
         {'c 188:* rwm','c 116:* rwm'},'DEVICE_RULES_CHANGED')
    need(h['NetworkMode']=='host' and not h['Privileged'] and h['RestartPolicy']['Name']=='no' and
         not c['State'].get('Paused'), 'HOST_POLICY_DRIFT')
    need(m['/dev/snd']['Source']=='/dev/snd' and not m['/dev/snd']['RW'],
         'SOUND_MOUNT_CHANGED')
    q=Path(m['/etc/asterisk']['Source'])/'quectel.conf'
    need(q.is_file() and not q.is_symlink(),'FORMAL_CONFIG_UNSAFE')
    b=q.read_text()
    need(re.search(r'(?m)^\s*data\s*=\s*/dev/qdc507/at\s*$',b) and
         re.search(r'(?m)^\s*audio\s*=\s*/dev/qdc507/pcm16\s*$',b),
         'FUNCTION_PATHS_CHANGED')
    mode=R/'qdc507-mode-upgrade-c3e-preaudio-v2/mode/current'
    need(mode.read_text().strip()=='16k','NOT_IN_16K_MODE')
    for n in ('qdc507-dynusb-mode-switch-pending.json',
              'qdc507-usb-recovery-v4-pending.json'):
        need(not (R/n).exists(),'OTHER_RECOVERY_PENDING_'+n)
    return c

def calls_idle():
    txt=output(['docker','exec',LIVE,'asterisk','-rx','core show channels count'],12)
    need(re.search(r'^\s*0 active channels\s*$',txt,re.M) and
         re.search(r'^\s*0 active calls\s*$',txt,re.M),'VOICE_CALLS_ACTIVE_OR_UNKNOWN')
    lines=output(['docker','exec',LIVE,'asterisk','-rx','core show channels concise'],12)
    for line in lines.splitlines():
        line=line.strip()
        if line and line.split('!',1)[0]!='Message/ast_msg_queue' and not re.fullmatch(
            r'\d+\s+(?:active\s+channels?|active\s+calls?|calls?\s+processed)\.?',line,re.I):
            raise Stop('UNKNOWN_ACTIVE_CHANNEL')

def ready():
    c=formal()
    need(c['State']['Running'] and (c['State'].get('Health') or {}).get('Status')=='healthy',
         'FORMAL_NOT_HEALTHY')
    h=output(['docker','exec',LIVE,'sha256sum','/usr/lib/asterisk/modules/chan_quectel.so'],10)
    need(h.split()[0]==MOD,'V4_DRIVER_DRIFT')
    d=output(['docker','exec',LIVE,'asterisk','-rx','quectel show device state quectel0'],12)
    for k,v in (('State','Free'),('Voice','Yes'),('SMS','Yes'),('Calls/Channels','0')):
        need(re.search(r'^\s*'+re.escape(k)+r'\s*:\s*'+re.escape(v)+r'\s*$',d,re.M),
             'MODEM_NOT_READY_'+k)
    calls_idle()
    return c

def adb_cmd(*tail,timeout=20):
    return output([ADB,'-H','127.0.0.1','-P','5037',*tail],timeout)

def adb_device(shot):
    d=shot['generation']
    serial=d[3]
    need(serial and serial.strip(),'USB_SERIAL_MISSING')
    lines=adb_cmd('devices','-l',timeout=12).splitlines()
    entries=[line.split()[0] for line in lines[1:] if len(line.split())>=2 and line.split()[1]=='device']
    need(len(entries)==1 and entries[0].lower()==serial.lower(),'ADB_NOT_CONNECTED_TO_THIS_QDC507')
    need(remote(serial,'id -u')=='0','ADB_NOT_ROOT')
    return serial

def remote(serial,script,timeout=22):
    marker='QDC_V4_'+str(os.getpid())+'_OK'
    txt=adb_cmd('-s',serial,'shell','('+script+') && echo '+marker,timeout=timeout)
    parts=[line.strip() for line in txt.replace('\r','').splitlines()]
    need(parts and parts[-1]==marker and parts.count(marker)==1,'QDC_DEVICE_SHELL_FAILED')
    return '\n'.join(parts[:-1]).strip()

def remote_flags(serial):
    s=remote(serial,'''set -eu
p=/tmp/djonehub-call/mavo-pcm-bridge.armv7
for k in qdc507_aprv3 qdc507_voice; do grep -q "^$k " /proc/modules && echo "${k}=1" || echo "${k}=0"; done
[ -x "$p" ] && echo helper=1 || echo helper=0
[ -r /run/djonehub-alsaucm.log ] && grep -q "ACDB -> Sent VocProc Cal!" /run/djonehub-alsaucm.log && echo calibrated=1 || echo calibrated=0
[ -x /tmp/qdc507-serial16k-v7/bridge-d5 ] && echo guard=1 || echo guard=0
[ -x /tmp/qdc16k-preaudio-manual/device-v2.sh ] && echo preaudio=1 || echo preaudio=0
[ "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0 ] && echo audio_idle=1 || echo audio_idle=0''')
    out={}
    for line in s.splitlines():
        if '=' in line:
            k,v=line.split('=',1);out[k]=v=='1'
    need(set(out)=={'qdc507_aprv3','qdc507_voice','helper','calibrated','guard','preaudio','audio_idle'},
         'VOICE_STATUS_UNEXPECTED')
    return out

def runtime_sources():
    for name,h in H.items():
        p=VOICE/name
        lfile(p)
        need(hashlib.sha256(p.read_bytes()).hexdigest()==h,'DEVICE_RUNTIME_HASH_DRIFT_'+name)

def prepare_voice(serial,flags):
    # Can run only with Asterisk container completely stopped, at a known idle USB generation.
    c=docker()
    need(not c['State']['Running'],'VOICE_INSTALL_REQUIRES_STOPPED_ASTERISK')
    need(not flags['qdc507_aprv3'] and not flags['qdc507_voice'],
         'PARTIAL_OR_LOADED_VOICE_MODULES_NEED_MANUAL_REVIEW')
    need(flags['audio_idle'],'AUDIO_ENABLE_NOT_IDLE')
    need(remote(serial,'uname -r')=='3.18.44','QDC_KERNEL_DRIFT')
    remote(serial,"! grep -q '^qdc507_afe ' /proc/modules; ! ps | grep -q '[m]avo-pcm-bridge'")
    runtime_sources()
    remote(serial,'umask 077; mkdir -p /tmp/djonehub-call; chmod 700 /tmp/djonehub-call')
    for name,h in H.items():
        dest='/tmp/djonehub-call/'+name
        adb_cmd('-s',serial,'push',str(VOICE/name),dest,timeout=35)
        got=remote(serial,'sha256sum '+shlex.quote(dest)).split()[0]
        need(got==h,'UPLOADED_RUNTIME_HASH_BAD_'+name)
        remote(serial,('chmod 755 ' if name.endswith('.armv7') else 'chmod 644 ')+shlex.quote(dest))
    remote(serial,'''set -eu
insmod /tmp/djonehub-call/qdc507_aprv3.ko
grep -q '^qdc507_aprv3 ' /proc/modules
insmod /tmp/djonehub-call/qdc507_voice.ko
grep -q '^qdc507_voice ' /proc/modules''',timeout=25)
    remote(serial,'''set -eu
n=0; while [ "$n" -lt 100 ]; do
 if test -c /dev/snd/controlC0 && test -c /dev/snd/pcmC0D4p &&
    test -c /dev/snd/pcmC0D4c && test -c /dev/snd/pcmC0D5p && test -c /dev/snd/pcmC0D6c; then break; fi
 sleep 0.2; n=$((n+1)); done
test -c /dev/snd/pcmC0D6c
! ps | grep -q '[a]lsaucm_test'
[ "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0 ]''',timeout=34)
    remote(serial,'''set -eu
rm -f /run/alsaucm_test /run/djonehub-alsaucm.log
nohup /usr/bin/alsaucm_test >/run/djonehub-alsaucm.log 2>&1 &
n=0; while [ "$n" -lt 50 ] && [ ! -p /run/alsaucm_test ]; do sleep 0.1; n=$((n+1)); done
test -p /run/alsaucm_test''',timeout=18)
    for req in ('open snd_soc_msm_9x07_Tomtom_I2S','set _verb VoLTE',
                'set _enadev Auxpcm Rx','set _enadev Auxpcm Tx'):
        remote(serial,"printf '%s\\n' "+shlex.quote(req)+" > /run/alsaucm_test")
    remote(serial,'''n=0
while [ "$n" -lt 100 ]; do
 grep -q 'ACDB -> Sent VocProc Cal!' /run/djonehub-alsaucm.log 2>/dev/null && break
 sleep 0.1; n=$((n+1)); done
grep -q 'ACDB -> Sent VocProc Cal!' /run/djonehub-alsaucm.log
/tmp/djonehub-call/mavo-pcm-bridge.armv7 --check''',timeout=24)
    print('DEVICE_VOICE_MODULES_AND_CALIBRATION_RESTORED=1',flush=True)


def same_serial(shot,base):
    need(shot['generation'][3] == base['serial'],'QDC507_PHYSICAL_IDENTITY_CHANGED')


def check_baseline(c,shot):
    need(BASE.is_file() and not BASE.is_symlink(),'BASELINE_NOT_ENROLLED')
    st=BASE.stat()
    need(st.st_uid==0 and not (st.st_mode & 0o077),'BASELINE_PERMISSIONS_CHANGED')
    d=json.loads(BASE.read_text())
    need(d.get('format')=='qdc507-dynusb-oneclick-v1' and d.get('container_id')==c['Id']
         and d.get('image_id')==IID and d.get('mode')=='16k','RECOVERY_BASELINE_DRIFT')
    same_serial(shot,d)
    for p in (IF,NODES,GUARD,PREP,Path(__file__).resolve()):
        need(hashlib.sha256(p.read_bytes()).hexdigest()==d.get('scripts',{}).get(p.name),
             'SCRIPT_DRIFT_'+p.name)
    return d


def phase(j,name):
    j['phase']=name
    atomic(PENDING,j)
    print('RECOVERY_PHASE='+name,flush=True)


def enroll():
    need(not BASE.exists() and not PENDING.exists(),'BASELINE_OR_PENDING_EXISTS')
    need(not (R/'qdc507-dynusb-cutover-pending-v1.json').exists(), 'CUTOVER_PENDING')
    c=formal();ready()
    shot=snap()
    node=run(['/usr/bin/python3.8','-B',str(NODES),'--check'],timeout=30)
    need(node.returncode==0,'FUNCTION_NODES_NOT_READY')
    serial=adb_device(shot)
    f=remote_flags(serial)
    need(all(f.values()),'VOICE_RUNTIME_NOT_READY')
    runtime_sources()
    need(output(['systemctl','is-active',TIMER])=='active','SMS_TIMER_INACTIVE')
    g=run(['/usr/bin/python3.8','-B',str(GUARD),'--check'],timeout=75)
    need('READ_ONLY_CHECK_PASSED=1' in g.stdout,'GUARD16_BASELINE_NOT_VERIFIED')
    scripts = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
               (IF,NODES,GUARD,PREP,Path(__file__).resolve())}
    atomic(BASE,{'format':'qdc507-dynusb-oneclick-v1','scripts':scripts,'container_id':c['Id'],
                 'image_id':IID,'mode':'16k','serial':serial,
                 'usb_generation':generation(shot),'host_boot_id':current_boot()})
    print('ONECLICK_BASELINE_SEALED=1')
    print('USB_HOTPLUG_RECOVERY_NOT_YET_TESTED=1')
    print('NO_BUSINESS_CHANGE=1')


def check():
    c=formal()
    shot=snap()
    base=check_baseline(c,shot)
    node=run(['/usr/bin/python3.8','-B',str(NODES),'--check'],timeout=30,allow=True)
    print('FUNCTION_NODES_READY='+str(int(node.returncode==0)))
    serial=adb_device(shot)
    flags=remote_flags(serial)
    for k,v in flags.items():print('DEVICE_'+k.upper()+'='+str(int(v)))
    print('USB_GENERATION_CHANGED='+str(int(base['usb_generation']!=generation(shot) or
                                             base['host_boot_id']!=current_boot())))
    print('FORMAL_RUNNING='+str(int(c['State']['Running'])))
    if c['State']['Running']:
        status=run(['docker','exec',LIVE,'asterisk','-rx',
                    'quectel show device state quectel0'],timeout=10,allow=True)
        print('ASTERISK_QUectel_QUERY_OK='+str(int(status.returncode==0)))
    print('READ_ONLY_CHECK_COMPLETE=1')


def recover():
    need(not PENDING.exists(),'PREVIOUS_RECOVERY_PENDING_REVIEW_REQUIRED')
    c=formal()
    need(c['State']['Running'],'START_FROM_RUNNING_CONTAINER_ONLY')
    wait_for_usb_enumeration()
    calls_idle()
    need(output(['systemctl','is-active',TIMER])=='active', 'SMS_TIMER_NOT_ACTIVE_BEFORE_RECOVERY')
    runtime_sources()
    shot=snap(require_ready=False);base=check_baseline(c,shot)
    changed=base['usb_generation']!=generation(shot) or base['host_boot_id']!=current_boot()
    node=run(['/usr/bin/python3.8','-B',str(NODES),'--check'],timeout=30,allow=True)
    need(node.returncode in (0,2),'NODE_CHECK_UNEXPECTED_EXIT')
    # In the normal healthy state, this action is a strict no-op.
    if not changed and node.returncode==0:
        try:
            ready();serial=adb_device(shot);flags=remote_flags(serial)
            if all(flags.values()) and output(['systemctl','is-active',TIMER])=='active':
                print('ALREADY_HEALTHY_NO_ACTION=1')
                return
        except (Stop,subprocess.TimeoutExpired):pass
    # Record authoritative pre-state before touching SMS or USB. An interrupted
    # recovery is never silently resumed or retried.
    j={'format':'qdc507-dynusb-oneclick-attempt-v1','container_id':c['Id'],
       'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
       'generation_before':base['usb_generation'], 'generation_detected':generation(shot)}
    phase(j,'AUTHORIZED')
    try:
        run(['systemctl','stop',TIMER],timeout=30)
        for _ in range(40):
            if run(['systemctl','is-active',WORKER],allow=True).stdout.strip()=='inactive':break
            time.sleep(.5)
        else:raise Stop('SMS_WORKER_NOT_IDLE')
        calls_idle();phase(j,'SMS_IDLE')
        p=run(['/usr/bin/python3.8','-B',str(IF),'--prepare'],timeout=110)
        need('HOST_ALREADY_READY_NO_WRITES=1' in p.stdout or
             'IF0_IF8_HOST_RECOVERY_COMPLETE=1' in p.stdout,
             'HOST_USB_PREP_NOT_VERIFIED')
        shot=snap();same_serial(shot,base)
        phase(j,'IF0_IF8_READY')
        p=run(['/usr/bin/python3.8','-B',str(NODES),'--sync'],timeout=65)
        need('ALREADY_SYNCHRONIZED_NO_WRITES=1' in p.stdout or
             'FUNCTION_NODES_SYNCHRONIZED=1' in p.stdout,'DEVICE_NODES_NOT_VERIFIED')
        phase(j,'FUNCTION_NODES_READY')
        run(['/usr/bin/python3.8','-B',str(PREP)],timeout=60)
        serial=None
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            try:
                serial=adb_device(shot)
                break
            except Stop:time.sleep(1)
        if serial is None:
            # Reconnect an offline ADB transport once, without killing other services.
            run([ADB,'-H','127.0.0.1','-P','5037','reconnect','offline'],
                timeout=20,allow=True)
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                try:
                    serial=adb_device(shot)
                    break
                except Stop:time.sleep(1)
        need(serial is not None,'ADB_CONNECTION_NOT_READY')
        phase(j,'ADB_READY')
        flags=remote_flags(serial)
        if not all(flags.values()):
            # Non-empty but inconsistent device states are not repaired blindly.
            need(flags['qdc507_aprv3']==flags['qdc507_voice'],
                 'PARTIAL_VOICE_MODULE_STATE_REVIEW_REQUIRED')
            if flags['qdc507_aprv3']:
                need(flags['helper'] and flags['calibrated'] and flags['audio_idle'],
                     'LOADED_MODULES_PARTIAL_RUNTIME_REQUIRES_REVIEW')
            else:
                need(flags['audio_idle'], 'DEVICE_AUDIO_BUSY_BEFORE_VOICE_REPAIR')
            calls_idle()
            run(['docker','stop','-t','30',LIVE],timeout=75)
            phase(j,'CONTAINER_STOPPED_FOR_VOICE_REPAIR')
            if not flags['qdc507_aprv3']:
                prepare_voice(serial,flags)
            need(generation(snap())==generation(shot),'USB_GENERATION_CHANGED_DURING_VOICE_REPAIR')
            phase(j,'VOICE_PREPARED')
            # The image entrypoint restores missing 16k Guard/preaudio /tmp bundle.
            run(['docker','start',LIVE],timeout=75)
            phase(j,'SAME_CONTAINER_STARTED')
        else:
            # No device-side audio files need reinstalling; ask chan_quectel to reopen AT and PCM.
            run(['docker','exec',LIVE,'asterisk','-rx',
                 'quectel restart now quectel0'],timeout=20)
            phase(j,'QUECTEL_RESTART_REQUESTED')
        good=False
        deadline=time.monotonic()+95
        while time.monotonic()<deadline:
            try:
                ready()
                flags=remote_flags(serial)
                need(all(flags.values()),'VOICE_RUNTIME_INCOMPLETE')
                good=True
                break
            except Stop:time.sleep(1)
        if not good and j['phase']=='QUECTEL_RESTART_REQUESTED':
            # Escalation: stop/start the same container ID, never recreate it.
            calls_idle()
            run(['docker','stop','-t','30',LIVE],timeout=75)
            phase(j,'SAME_CONTAINER_STOPPED_FOR_RESTART')
            run(['docker','start',LIVE],timeout=75)
            phase(j,'SAME_CONTAINER_STARTED_FALLBACK')
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                try:
                    ready();flags=remote_flags(serial)
                    need(all(flags.values()),'VOICE_RUNTIME_INCOMPLETE')
                    good=True;break
                except Stop:time.sleep(1)
        need(good,'MODEM_OR_AUDIO_NOT_READY_AFTER_RECOVERY')
        fresh=snap()
        need(fresh['generation']==shot['generation'],'USB_GENERATION_CHANGED_AT_FINISH')
        need(docker()['Id']==C0,'DOCKER_CONTAINER_RECREATED_UNEXPECTEDLY')
        phase(j,'BUSINESS_VERIFIED')
        run(['systemctl','start',TIMER],timeout=30)
        need(output(['systemctl','is-active',TIMER])=='active','SMS_TIMER_FAILED')
        phase(j,'SMS_ACTIVE')
        # Guard checks include SMS timer active and no true voice calls.
        g=run(['/usr/bin/python3.8','-B',str(GUARD),'--check'],timeout=70)
        need('READ_ONLY_CHECK_PASSED=1' in g.stdout,'GUARD_CHECK_FAILED')
        phase(j,'GUARD_VERIFIED')
        atomic(BASE,dict(base,usb_generation=generation(shot),host_boot_id=current_boot()))
        phase(j,'COMPLETE')
        receipt=R/('qdc507-dynusb-oneclick-complete-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
        need(not receipt.exists(),'RECEIPT_COLLISION')
        os.replace(str(PENDING),str(receipt))
        print('DYNUSB_ONECLICK_RECOVERY_OK=1')
        print('CONTAINER_ID_UNCHANGED=1')
        print('SMS_TIMER=active')
        print('RECEIPT='+str(receipt))
    except BaseException:
        print('RECOVERY_INTERRUPTED_JOURNAL_PRESERVED=1',flush=True)
        print('CHECK_FORMAL_CONTAINER_AND_SMS_BEFORE_RETRY=1',flush=True)
        raise


def main():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    need(sys.argv[1:] in (['--enroll'],['--check'],['--recover']),
         'USE --enroll / --check / --recover')
    for p in (IF,NODES,GUARD,PREP):lfile(p)
    locks=[]
    try:
        for path in (LOCK,'/run/qdc507-v4-usb.lock',
                     '/run/qdc507-v4-boot.lock', '/run/qdc507-dynusb-mode.lock'):
            locks.append(lock(path))
        if sys.argv[1]=='--enroll':enroll()
        elif sys.argv[1]=='--check':check()
        else:recover()
    finally:
        for fd in reversed(locks):os.close(fd)

if __name__=='__main__':
    try:main()
    except (Stop,RuntimeError,OSError,ValueError,KeyError,subprocess.TimeoutExpired,
            BlockingIOError) as e:
        print('ONECLICK_STOPPED='+type(e).__name__+':'+str(e)[:230],file=sys.stderr)
        sys.exit(2)
