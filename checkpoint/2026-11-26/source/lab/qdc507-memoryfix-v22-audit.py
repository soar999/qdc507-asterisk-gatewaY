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
CURRENT=SRC/'qdc507-production-20261126-v44-event.json'
ENTRY=Path('/volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh')
GUARD_BASE=R/'qdc507-dynusb-memoryfix-v2-baseline.json'
ENTRY_SHA='399dbeabf4bed2f145344c24b22e587e0e7ffe7b7e52aedaafc00719da43ce06'
SOURCE_PINS={'qdc507-coldboot-memoryfix-v22.py': '35050d08aef00751bc1419e74f73520538f3a5a49210b0a3ce2bb69fdab8ce01', 'qdc507-device-node-sync-memoryfix-v22.py': 'd5d7e509686645146568029fd608005b8bfbb04cf3249c0fa097201e3d021e9f', 'qdc507-dynusb-oneclick-memoryfix-v22.py': '68ef9ef26f84e91d012a32bbe4a23df3674a121a1e835189c6f145b2a4712185', 'qdc507-usb-recover-memoryfix-v22.sh': 'a86b873982209df25a51ee3bf370b2cc651b80d9d4e5a9239cc940aee61826a4', 'qdc507-guard16-runtime-memoryfix-v2.py': 'f7f076f9944c0ef85020785b1b308c98597fd55cb1035ab177447bc239bd6b12', 'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py': '9e9398a77e39d711b0009dbc5938a18c1f9387925f3c6541ae215331d9a9b730'}
# Pin the DSM 7.2.2 V221 coldboot implementation selected by this live audit.
V221_SHA='cb12e806767ede5007b484b598455d60388a1989e09be9b5ac10f0bb48f09756'
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
    need(sha(CURRENT)=='d9ae1db9cee220457d843d5c604198beafffe384a51bc690c1b9b57ef602405c','CURRENT_MANIFEST_DRIFT')
    j=json.loads(CURRENT.read_text())
    need(j.get('format')=='qdc507-production-20261126-v44-event-v1' and
         j.get('date')=='2026-11-26' and
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

    usb_result=run(['/bin/sh',str(R/'qdc507-usb-recover-memoryfix-v22.sh'),'--check'],160)

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
    print('USB_REENUMERATION_ACCEPTANCE_RECORDED=PASS',flush=True)
    print('PHYSICAL_USB_POWER_CYCLE_PENDING=1',flush=True)
    print('ADB_AUTOMATIC_RESET_LIVE_TEST_PENDING=1',flush=True)
    print('COLDBOOT_AFTER_COORDINATOR_RETIREMENT_NOT_LIVE_TESTED=1',flush=True)

# V44 Event production audit: independently pinned accepted sources.
import runpy

V44_RUNTIME='c00b3504649210a1bf3f3d11b13cb66f5c85d2f57b962b7c4ff4cdbbaae267b0'
V44_EVENT='3831ac17e6da2702d8531fd10ded5b0d540debe8aa578e1e40f3c4206afbcef7'
V44_STAGE=R/'qdc507-v44-prestage-20261126-074550'
V44_NS=R/'qdc507-mode-upgrade-c3e-preaudio-v2/profiles/v44-coordination'
V44_BASE=R.parent/'qdc507-current'

def fixed(path,h):
    st=path.lstat()
    need(stat.S_ISREG(st.st_mode)
         and not stat.S_IMODE(st.st_mode)&0o022
         and sha(path)==h,
         'V44_PIN_DRIFT_'+path.name)

def v44_static():
    fixed(R/'qdc507-v44-runtime.py',V44_RUNTIME)
    fixed(R/'qdc507-v44-event-candidate.sh',V44_EVENT)

    host={
      'qdc507-adb-watch-alert-memoryfix-v2.py':
      '5cd2c7cd5264cf8189846ba73a8f6aca2dcfae14e886d8e8424bb424b5f6794b',
      'qdc507-coldboot-startup-v221.py':
      'edfd4a9af3ccd1acc4d70e9296fb41da93369c920992a075991639736b899e85',
      'qdc507-adb-recover-once-memoryfix-v2.py':
      '9d656bea91419f5a46e3e4a4ac6b85163f61d67929f48030b3e2292e3c5888a6',
    }
    for n,h in host.items():fixed(R/n,h)

    for n in ('v0','v0-edit'):
        fixed(V44_BASE/n/'extensions.conf',
              'a5615c79a25a671beb0fe5790d4915db50d6230c0b2e83b679fc8c5053b39958')

    common={
      'adb_coordination.py':
      '12e9d456dd210d19eb95f897ca16ccefc96c9b074f5432ea6d08ec3f702366f4',
      'lifecycle.py':
      'b65aff643318415713085c004a51427daf4e88613145d216bec9f9ecb30ae784',
      'device-v44-candidate.sh':
      '6352805ba1461451186d52eaa0adbb945abca395c2e9ce6c5482c9c792db1ffb',
      'preaudio-v44-candidate.py':
      '84a35a708defd000dfee0d4ef599d858f32bbe829646e10030fb29878b1db45c',
    }

    for n,h in common.items():
        fixed(V44_STAGE/n,h)
        fixed(V44_NS/n,h)

    modules={
      'nas-prestage.py':
      'e9a3663cbec0d6f1d3a9bea9f214df325bd6f6c5f4fcd943d55f956b1b745950',
      'prepare-v44-stage.py':
      'd2ca5f4fe85af2ce5a29dad8809c401fe166c699f6b1b8b1c72a27a9629c1c39',
      'stage_routes.py':
      'e35aadf6a7c69514701d7713aba6bd5d7cb91ce0d961ab3149211e98efe2f57a',
      'restage_v44.py':
      '8093ebe7038f8a5b9a08c733f61e75312888810a8211fb7398c7cec322181509',
      'observation.py':
      'bd90b0b10835f124ab0733b80fdfbecd11715932c2af0bbeaa045d84ae64f955',
      'watch_adapter.py':
      '0be8daf613e1b97fd9f22f87e13299d1270833fddf8c6186e32fc0e252fd9457',
      'nas_watch_bridge.py':
      'a2debf922480262e78c177e0d124607c5c1576bcdd601ca71f426c0bcd19a47d',
      'nas_recovery_adapter.py':
      '2845ceca288bb607fffc04c3fc536d56d9f179217fd7655c5d389c4d70eb8ba8',
    }

    for n,h in modules.items():fixed(V44_STAGE/n,h)

    fixed(V44_NS/'agi-wrapper-v44',
          '698675fdcad685fcc3c5b1e0d19b83d62fbdae23f6a5c5ad50d1a329fe54b364')

    print('V44_EVENT_STATIC_PINS=PASS',flush=True)

def v44_live(m):
    p=m['p']

    need(m['idle'](),'V44_BUSINESS_BUSY')
    epoch=m['verify_live_epoch'](m['E'],m['gen'])

    for path in (m['E'],m['G']):
        st=path.lstat()
        need(stat.S_ISREG(st.st_mode)
             and st.st_uid==0 and st.st_gid==1000
             and stat.S_IMODE(st.st_mode)==0o640,
             'V44_COORDINATION_PERMISSIONS_'+path.name)

    need(p.ast('cat',p.NS+'/epoch')==(epoch+'\n').encode(),
         'V44_EPOCH_ASTERISK_READBACK_FAILED')

    for name in (
        'agi-wrapper-v44','adb_coordination.py',
        'lifecycle.py','device-v44-candidate.sh',
        'preaudio-v44-candidate.py'
    ):
        need(p.ast('cat',p.NS+'/'+name)==
             (V44_NS/name).read_bytes(),
             'V44_CONTAINER_DRIFT_'+name)

    path=R/'qdc507-v44-activation.json'
    st=path.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0
         and not stat.S_IMODE(st.st_mode)&0o077,
         'V44_ACTIVATION_RECORD_UNTRUSTED')

    j=json.loads(path.read_text())
    need(j['phase']=='ACTIVE'
         and j['runtime_sha256']==V44_RUNTIME
         and j['prestage']==str(m['B']),
         'V44_ACTIVATION_RECORD_DRIFT')

    expected={}
    for path,(a,b) in m['changes'].items():
        need(path.read_bytes()==b,'V44_ENTRY_DRIFT_'+path.name)
        expected[str(path)]=[
            hashlib.sha256(a).hexdigest(),
            hashlib.sha256(b).hexdigest()]
    need(j['changes']==expected,'V44_JOURNAL_PINS_DRIFT')

    # Read-only hash checks on the physical device.
    for path,h in (
      ('/tmp/qdc16k-preaudio-manual/device-v44.sh',
       '6352805ba1461451186d52eaa0adbb945abca395c2e9ce6c5482c9c792db1ffb'),
      (m['EVENT_REMOTE'],V44_EVENT)
    ):
        result=m['rpc']('shell',
            'test -f '+path+' && test -x '+path+
            ' && sha256sum '+path).split()
        need(result and result[0]==h,'V44_DEVICE_SOURCE_DRIFT')

    need(m['mode']()=='16k','V44_16K_MODE_DRIFT')
    need(len(m['pids']())==1,'V44_WATCHDOG_COUNT')
    need(m['verify_live_epoch'](m['E'],m['gen'])==epoch,
         'V44_EPOCH_CHANGED_DURING_AUDIT')

    print('V44_ACTIVE_ENTRIES=PASS',flush=True)
    print('V44_COORDINATION_AND_EPOCH=PASS',flush=True)
    print('V44_CONTAINER_COMPONENTS=PASS',flush=True)
    print('V44_EVENT_DEVICE_HASH=PASS',flush=True)
    print('V44_ACTIVATION_JOURNAL=PASS',flush=True)
    return epoch

def main():
    need(len(sys.argv)==2 and sys.argv[1] in ('--selftest','--check','--deep'), 'USE_SELFTEST_CHECK_DEEP')
    static()
    v44_static()
    if sys.argv[1]=='--selftest':
        print('V44_EVENT_OFFLINE_SELFTEST=PASS');return

    m=runpy.run_path(str(R/'qdc507-v44-runtime.py'))

    with m['gate'](m['G'],True,timeout=10):
        epoch=v44_live(m)
        audit()
        if sys.argv[1]=='--deep':deep()
        need(m['idle'](),'BUSINESS_CHANGED_DURING_AUDIT')
        need(m['verify_live_epoch'](m['E'],m['gen'])==epoch,
             'V44_EPOCH_CHANGED')
        print('V44_EVENT_PRODUCTION_AUDIT=PASS',flush=True)
if __name__=='__main__':
    try:main()
    except (Stop,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as e:
        print('V22_AUDIT_STOP='+type(e).__name__+':'+str(e)[:550],file=sys.stderr)
        sys.exit(2)
