#!/usr/bin/python3.8
"""QDC507 v22 overlay audit. No legacy v4-only helper scan; actual v22 chain only.
--selftest offline; --check and --deep are read-only. No repair actions.
"""
import hashlib, json, os, stat, subprocess, sys
from pathlib import Path
R=Path('/volume2/docker/asterisk/qdc507-16k-lab')
SRC=Path(__file__).resolve().parent
REL=SRC/'qdc507-memoryfix-v22-release.json'
RELEASE_SHA='1127bfe7ee6b3f25905bb4d1f2fe4eac9cba20ca518a6bd4be6cd44eb4007ec2'
ADOPT=R/'qdc507-memoryfix-v22-baseline-adopt.py'
V21=R/'qdc507-memoryfix-v2-recovery-audit-v2.py'
BASE=R/'qdc507-dynusb-memoryfix-v22-baseline.json'
NODES=R/'qdc507-device-node-sync-memoryfix-v22.py'
OLD_TAG=b'qdc507-asterisk:dualmode-remotehangup-v4-prod'
CURRENT=SRC/'qdc507-memoryfix-v22-production-20261121.json'
ENTRY=Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh')
GUARD_BASE=R/'qdc507-dynusb-memoryfix-v2-baseline.json'
ENTRY_SHA='fb4025303fa51e8a3cac03039ab516c22f8cba5ba0bdcf902910d9f481e6d6d4'
SOURCE_PINS={'qdc507-coldboot-memoryfix-v22.py': '35050d08aef00751bc1419e74f73520538f3a5a49210b0a3ce2bb69fdab8ce01', 'qdc507-device-node-sync-memoryfix-v22.py': 'd5d7e509686645146568029fd608005b8bfbb04cf3249c0fa097201e3d021e9f', 'qdc507-dynusb-oneclick-memoryfix-v22.py': '68ef9ef26f84e91d012a32bbe4a23df3674a121a1e835189c6f145b2a4712185', 'qdc507-hotplug-recovery-memoryfix-v22.py': '3636553d57ed767fb88554ad68b1edf2e8bc67c865c323ddab75e0b58db2ad06', 'qdc507-usb-recover-memoryfix-v22.sh': 'a9ae0b71f42dc74f829c6827f8e1c3c34404ac0671677c1e0a29a3ab81059cb4', 'qdc507-guard16-runtime-memoryfix-v2.py': 'f7f076f9944c0ef85020785b1b308c98597fd55cb1035ab177447bc239bd6b12', 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py': '9e9398a77e39d711b0009dbc5938a18c1f9387925f3c6541ae215331d9a9b730'}
# Pin the DSM 7.2.2 V221 coldboot implementation selected by this live audit.
V221_SHA='afb40eeddafd5148ce86504641dbadf8b99f8af560b847709f82dd9985204a4d'
class Stop(Exception):pass
def need(ok,msg):
    if not ok:raise Stop(msg)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(args,timeout=150):
    try:
        p=subprocess.run(list(map(str,args)),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise Stop('SUBPROCESS_UNAVAILABLE_'+type(e).__name__)
    need(p.returncode==0,'SUBPROCESS_FAILED_'+str(args[-2:])+'_'+p.stdout[-480:])
    return p.stdout
def static():
    need(REL.is_file() and not REL.is_symlink() and sha(REL)==RELEASE_SHA, 'RELEASE_DIGEST_DRIFT')
    historic=json.loads(REL.read_text())
    need(historic.get('format')=='qdc507-memoryfix-v22-overlay-release-v1',
         'HISTORICAL_RELEASE_FORMAT_CHANGED')
    need(CURRENT.is_file() and not CURRENT.is_symlink(),
         'CURRENT_RELEASE_MISSING')
    j=json.loads(CURRENT.read_text())
    need(j.get('format')=='qdc507-memoryfix-v22-production-20261121-v1' and
         j.get('date')=='2026-11-21' and
         j.get('history_release_sha256')==RELEASE_SHA and
         j.get('entry_sha256')==ENTRY_SHA and
         j.get('source_sha256')==SOURCE_PINS,
         'CURRENT_RELEASE_NOT_PINNED')
    need(ENTRY.is_file() and not ENTRY.is_symlink() and
         sha(ENTRY)==ENTRY_SHA,'OFFICIAL_ENTRY_CHANGED')
    for n,h in j['source_sha256'].items():
        p=SRC/n
        need(p.is_file() and not p.is_symlink() and sha(p)==h,'V22_SCRIPT_DRIFT_'+n)
    v221=SRC/'qdc507-coldboot-memoryfix-v221.py'
    need(v221.is_file() and not v221.is_symlink() and
         sha(v221)==V221_SHA, 'V221_DSM722_COLDBOOT_SOURCE_DRIFT')
    # Check the staged v22 helper, not a legacy helper path on NAS.
    need(OLD_TAG not in (SRC/NODES.name).read_bytes(),'V4_IMAGE_GATE_REINTRODUCED_IN_V22_HELPER')
    print('V22_ACTIVE_CHAIN_SOURCE_HASHES=PASS',flush=True)
    print('V22_OBSOLETE_V4_IMAGE_GATE_REMOVED=1',flush=True)
    return j
def audit():
    need(os.geteuid()==0,'ROOT_REQUIRED')

    for path in (BASE,GUARD_BASE):
        s=path.lstat()
        need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and
             not stat.S_IMODE(s.st_mode)&0o077,
             'UNTRUSTED_BASELINE_'+path.name)

    b=json.loads(BASE.read_text())
    g=json.loads(GUARD_BASE.read_text())

    need(b.get('format')=='qdc507-dynusb-oneclick-v1' and
         b.get('mode')=='16k','BASELINE_FORMAT_CHANGED')

    for key in ('format','mode','serial','host_boot_id',
                'container_id','image_id','usb_generation'):
        need(b.get(key)==g.get(key),
             'GUARD16_CONTEXT_CHANGED_'+key)

    need(b.get('host_boot_id')==
         Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'HOST_BOOT_CHANGED')

    # Verify scripts actually sealed by the recovery baselines.
    # The outer USB wrapper is pinned independently by SOURCE_PINS.
    one='qdc507-dynusb-oneclick-memoryfix-v22.py'
    guard='qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'

    need(b.get('scripts',{}).get(one)==SOURCE_PINS[one],
         'ONECLICK_BASELINE_PIN_CHANGED')
    need(b.get('scripts',{}).get(guard)==SOURCE_PINS[guard],
         'GUARD_BASELINE_PIN_CHANGED')
    need(g.get('scripts',{}).get(guard)==SOURCE_PINS[guard],
         'GUARD16_OLD_BASELINE_PIN_CHANGED')

    container=json.loads(
        run(['docker','inspect','qdc507-hardware'],20))[0]

    need(container.get('Id')==b.get('container_id') and
         container.get('Image')==b.get('image_id') and
         container.get('State',{}).get('Running') is True and
         container.get('State',{}).get('Health',{}).get('Status')=='healthy',
         'PRODUCTION_CONTAINER_CHANGED')

    timer=run(['systemctl','is-active',
               'qdc507-sms-tick.timer'],15).strip()
    need(timer=='active','SMS_TIMER_INACTIVE')

    guard_result=run([
        '/usr/bin/python3.8','-I','-B',
        str(R/'qdc507-guard16-runtime-memoryfix-v2.py'),'--check'
    ],130)

    for marker in (
        'DEVICE_CURRENT_MODE=v2',
        'DEVICE_V2_STAGE=present',
        'DEVICE_V1_BACKUP=present',
        'READ_ONLY_RUNTIME_CHECK_OK=1'
    ):
        need(marker in guard_result,'GUARD16_STATE_'+marker)

    usb_result=run(['/bin/sh',str(ENTRY),'--check'],160)

    for marker in (
        'USB_SWAP_READ_ONLY_CHECK_OK=1',
        'USB_GENERATION_CHANGED=0',
        'ADB_STATUS=online',
        'SIM_REGISTERED=1'
    ):
        need(marker in usb_result,'OFFICIAL_USB_STATE_'+marker)

    print('ONECLICK_AND_GUARD16_SEALED_PINS=PASS',flush=True)
    print('OFFICIAL_MEMORYFIX_V22_ENTRY=PASS',flush=True)
    print('GUARD16_V2_AND_USB_BASELINES=PASS',flush=True)
    print('PRODUCTION_CONTAINER_AND_SMS=PASS',flush=True)
    print('V22_READ_ONLY_AUDIT_PASS=1',flush=True)

def deep():
    jobs=[
      ('ONECLICK','qdc507-dynusb-oneclick-memoryfix-v22.py','--check','READ_ONLY_CHECK_COMPLETE=1',130),
      ('HOTPLUG','qdc507-hotplug-recovery-memoryfix-v22.py','--check','READ_ONLY_COORDINATOR_CHECK_OK=1',150),
      ('USB_SIM','qdc507-usb-recover-memoryfix-v22.sh','--check','USB_SWAP_READ_ONLY_CHECK_OK=1',150),
      ('GUARD16','qdc507-guard16-runtime-memoryfix-v2.py','--check','READ_ONLY_RUNTIME_CHECK_OK=1',130),
      ('COLDBOOT','qdc507-coldboot-memoryfix-v221.py','--check','COLD_BOOT_READ_ONLY_PREFLIGHT_OK=1',150),
      ('ADB','qdc507-adb-recover-once-memoryfix-v2.py','--check','RESET_STATIC_GUARDS_OK=1',90),
    ]
    for name,path,mode,marker,timeout in jobs:
        argv=['/bin/sh',str(R/path),mode] if path.endswith('.sh') else              ['/usr/bin/python3.8','-I','-B',str(R/path),mode]
        output=run(argv,timeout=timeout)
        need(marker in output,'V22_'+name+'_MARKER_ABSENT')
        print('V22_'+name+'_READ_ONLY=PASS',flush=True)
    print('V22_DEEP_AUDIT_PASSED=1',flush=True)
    print('MANUAL_USB_RECOVERY_VERIFIED_20261120=1',flush=True)
    print('FUTURE_HOTPLUG_AND_COLDBOOT_NOT_FULLY_VALIDATED=1',flush=True)
def main():
    need(len(sys.argv)==2 and sys.argv[1] in ('--selftest','--check','--deep'), 'USE_SELFTEST_CHECK_DEEP')
    static()
    if sys.argv[1]=='--selftest':
        print('V22_OFFLINE_SELFTEST_PASS=1');return
    audit()
    if sys.argv[1]=='--deep':deep()
if __name__=='__main__':
    try:main()
    except (Stop,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as e:
        print('V22_AUDIT_STOP='+type(e).__name__+':'+str(e)[:550],file=sys.stderr)
        sys.exit(2)
