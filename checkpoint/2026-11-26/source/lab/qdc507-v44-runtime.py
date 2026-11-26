#!/usr/bin/python3.8
# V44 live activation candidate: preserve sealed cores; audit updated after acceptance.
import os, sys, re, json, time, stat, signal, fcntl, runpy, subprocess, secrets, hashlib, importlib.util
from pathlib import Path
L=Path('/volume2/docker/asterisk/qdc507-16k-lab')
S=L/'qdc507-v44-prestage-20261126-074550'
B=L/'qdc507-v44-private-prestage-REDACTED'
SELF=L/'qdc507-v44-runtime.py'
J=L/'qdc507-v44-activation.json'
PY='/usr/bin/python3.8'
A='/usr/local/libexec/qdc507/adb'
AH='a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01'
def need(x,m):
    if not x: raise RuntimeError(m)
def sha(b):return hashlib.sha256(b).hexdigest()
need(os.geteuid()==0,'ROOT_REQUIRED')
meta=json.loads((B/'manifest.json').read_bytes())
receipt=json.loads((B/'PRESTAGE-ONLY.json').read_bytes())
need(sha((B/'manifest.json').read_bytes())==receipt['manifest_sha256'],'MANIFEST_DRIFT')
need(sha((B/'SHA256SUMS').read_bytes())==receipt['sums_sha256'],'SUMS_DRIFT')
def saved(k):
    b=(B/k).read_bytes();need(sha(b)==meta['files'][k],'BACKUP_DRIFT_'+k);return b
for k in ('adb_coordination.py','lifecycle.py','restage_v44.py','observation.py','watch_adapter.py','nas_watch_bridge.py','nas_recovery_adapter.py'):
    need((S/k).read_bytes()==saved('candidate-'+k),'MODULE_DRIFT_'+k)
need(sha((S/'nas-prestage.py').read_bytes())=='e9a3663cbec0d6f1d3a9bea9f214df325bd6f6c5f4fcd943d55f956b1b745950','SOURCE_DRIFT_nas-prestage.py')
need(sha((S/'prepare-v44-stage.py').read_bytes())=='d2ca5f4fe85af2ce5a29dad8809c401fe166c699f6b1b8b1c72a27a9629c1c39','SOURCE_DRIFT_prepare-v44-stage.py')
need(sha((S/'stage_routes.py').read_bytes())=='e35aadf6a7c69514701d7713aba6bd5d7cb91ce0d961ab3149211e98efe2f57a','SOURCE_DRIFT_stage_routes.py')
sys.path.insert(0,str(S));os.environ['QDC_PROTECTED_GATE']='1'
from adb_coordination import gate, bounded_probe_command, GateBusy
from lifecycle import maintain, sysfs_generation, write_marker, verify_live_epoch
from restage_v44 import restage, PINS
from nas_watch_bridge import WatchBridge
spec=importlib.util.spec_from_file_location('prestage',str(S/'nas-prestage.py'))
n=importlib.util.module_from_spec(spec);spec.loader.exec_module(n);p=n.prep
ns=p.PROFILE/'v44-coordination'; G=ns/'adb.lock'; E=ns/'epoch'; ids=meta['usb_identity']
old=saved('original-extensions-v0.conf');new=saved('extensions-v0.candidate.conf').replace((p.R+'/agi-wrapper-v44').encode(),(p.NS+'/agi-wrapper-v44').encode())
w=saved('agi-wrapper-v44').replace((p.R+'/preaudio-v44-candidate.py').encode(),(p.NS+'/preaudio-v44-candidate.py').encode())
serial=re.findall(rb'\bQDC_SERIAL=([A-Za-z0-9._:-]+)',w);need(len(serial)==1,'SERIAL_INVALID')
# Runtime-only epoch writer: preserve sealed prestage modules.
import lifecycle as _lifecycle

def _write_marker_file(path,value):
    path=Path(path)
    fd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    tmp='.'+path.name+'.'+secrets.token_hex(12)
    try:
        f=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(f,'w') as out:
            os.fchown(out.fileno(),0,1000)
            os.fchmod(out.fileno(),0o640)
            out.write(value+'\n');out.flush();os.fsync(out.fileno())
        os.replace(tmp,path.name,src_dir_fd=fd,dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:os.unlink(tmp,dir_fd=fd)
        except FileNotFoundError:pass
        os.close(fd)

def write_marker(path,value):
    need(Path(path)==E,'UNEXPECTED_EPOCH_PATH')
    _write_marker_file(path,value)
    if value.startswith('READY:'):
        try:
            need(p.ast('cat',p.NS+'/epoch')==(value+'\n').encode(), 'ASTERISK_EPOCH_READBACK_FAILED')
        except BaseException:
            _write_marker_file(path,'BLOCKED:epoch-readback-failed')
            raise

_lifecycle.write_marker=write_marker
def gen():return sysfs_generation(p.USB,p.BOOT,ids['idVendor'],ids['idProduct'],ids['serial'])
def baseadb():
    need(sha(Path(A).read_bytes())==AH,'ADB_HASH_DRIFT');return [A,'-H','127.0.0.1','-P','5037','-s',serial[0].decode()]
def rpc(*a):return subprocess.check_output(baseadb()+list(a),stderr=subprocess.PIPE,timeout=75).decode().replace('\r','')
def idle():
    try:return n.idle()
    except Exception:return False
def mode():return p.ast('cat','/etc/qdc507-mode/current').decode().strip()
def run(argv,timeout):return subprocess.run(list(map(str,argv)),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
def checked(argv,t,markers):
    r=run(argv,t);print(r.stdout,flush=True);need(r.returncode==0 and set(markers)<=set(r.stdout.splitlines()),'RECOVERY_RECEIPT_MISSING');return True
def original(k):
    v=saved('original-host-'+k);need(sha(v)==p.PINS[k],'CORE_PIN_DRIFT_'+k);return B/('original-host-'+k)
def pin(k):need(p.HOST[k].read_bytes()==saved('original-host-'+k),'CORE_CHANGED_'+k)
EVENT_SOURCE=L/'qdc507-v44-event-candidate.sh'
EVENT_REMOTE='/tmp/qdc16k-preaudio-manual/device-v44-event.sh'
EVENT_SHA='3831ac17e6da2702d8531fd10ded5b0d540debe8aa578e1e40f3c4206afbcef7'
w0=w
w=w0.replace(b'QDC_DEVICE_SCRIPT=/tmp/qdc16k-preaudio-manual/device-v44.sh',('QDC_DEVICE_SCRIPT='+EVENT_REMOTE).encode())
need(w!=w0,'EVENT_WRAPPER_ANCHOR_MISSING')

def event_stage(g):
    st=EVENT_SOURCE.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022 and sha(EVENT_SOURCE.read_bytes())==EVENT_SHA,'EVENT_SOURCE_DRIFT')
    def guard():
        need(E.read_text().startswith('BLOCKED:') and idle() and gen()==g and mode()=='16k','EVENT_STAGE_NOT_QUIESCENT')
    def call(*args):
        guard();result=rpc(*args).strip();guard();return result
    common=f'''set -eu
F={EVENT_REMOTE}; U="$F.upload"
[ ! -L "$F" ]; [ ! -L "$U" ]
'''
    check=common+f'''if [ -e "$F" ]; then
 [ ! -e "$U" ]; [ -f "$F" ]; [ -x "$F" ]
 [ "$(sha256sum "$F" | cut -d ' ' -f1)" = {EVENT_SHA} ]
 echo READY
elif [ -e "$U" ]; then
 [ -f "$U" ]; [ "$(sha256sum "$U" | cut -d ' ' -f1)" = {EVENT_SHA} ]
 echo UPLOAD_VERIFIED
else echo MISSING; fi
'''
    state=call('shell',check)
    if state!='READY':
        need(state in ('MISSING','UPLOAD_VERIFIED'),'EVENT_STAGE_UNKNOWN')
        if state=='MISSING':call('push',str(EVENT_SOURCE),EVENT_REMOTE+'.upload')
        need(call('shell',common+f'''[ ! -e "$F" ]; [ -f "$U" ]
[ "$(sha256sum "$U" | cut -d ' ' -f1)" = {EVENT_SHA} ]
chmod 500 "$U"
mv "$U" "$F"
echo INSTALLED
''')=='INSTALLED','EVENT_INSTALL_UNCONFIRMED')
        need(call('shell',check)=='READY','EVENT_READBACK_FAILED')
    print('V44_EVENT_RESTAGE=PASS',flush=True)
    return True

def stage():
    g=gen();result=restage(S,rpc,authorized=lambda:E.read_text().startswith('BLOCKED:'),generation=gen,expected_generation=g,mode=mode);print('V44_RESTAGE='+result,flush=True);return event_stage(g)
def post():
    need((ns/'agi-wrapper-v44').read_bytes()==w and p.ast('cat',p.NS+'/agi-wrapper-v44')==w,'EVENT_WRAPPER_DRIFT')
    need(idle() and mode()=='16k','POST_NOT_IDLE_OR_16K')
    need(p.run('systemctl','is-active','qdc507-sms-tick.timer').strip()==b'active','SMS_TIMER_NOT_ACTIVE')
    h=L/'qdc507-guard16-runtime-memoryfix-v2.py';need(sha(h.read_bytes())=='f7f076f9944c0ef85020785b1b308c98597fd55cb1035ab177447bc239bd6b12','GUARD_PIN_DRIFT')
    return checked([PY,'-I','-B',h,'--check'],130,['DEVICE_CURRENT_MODE=v2','READ_ONLY_RUNTIME_CHECK_OK=1'])
def restage_after():
    # Original V43 is kept available and required by the verified V44 restager.
    need(sha(p.ce('cat',p.CONTAINER['restager']))==p.PINS['restager'],'V43_RESTAGER_DRIFT')
    checked(['docker','exec','-u','0',p.C,'/usr/bin/python3','-B',p.CONTAINER['restager'],'--boot'],110,['V43_DEVICE_READY=1'])
    return stage()
class Recovery:
    def execute(self,kind,*,expected_epoch=None,reset_authorize=None):
        if kind=='usb':
            k='usb_inner';argv=['/bin/sh',p.HOST[k],'--recover'];limit=1100;marks=['USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1','V43_POST_USB_RESTAGE_OK=1','SAME_PRODUCTION_CONTAINER_AND_USB_BASELINE_PRESERVED=1']
        elif kind=='coldboot':
            k='cold_inner';argv=[PY,'-I','-B',original('startup'),'--boot'];limit=1250;marks=['V221_COLDBOOT_COMPLETE=1']
        elif kind=='adb-reset':
            need(expected_epoch is not None and reset_authorize is not None,'RESET_NOT_AUTHORIZED');k='adb_reset';argv=[PY,'-I','-B',p.HOST[k],'--reset'];limit=180;marks=['ADB_USB_RESET_RECOVERY_OK=1','GUARD16_V2_OK=1']
        else:raise RuntimeError('UNKNOWN_KIND')
        def before():
            pin(k)
            if not idle():
                if kind!='coldboot' or p.BOOT.read_text().strip()==ids['boot']:return False
                # Coldboot core independently waits for Docker and checks active calls.
            if expected_epoch is not None and verify_live_epoch(E,gen)!=expected_epoch:return False
            return reset_authorize is None or reset_authorize()
        def action():pin(k);return checked(argv,limit,marks)
        value=maintain(G,E,kind=kind,preflight=before,run_recovery=action,verify=post,generation=gen,restage=restage_after)
        print('V44_RECOVERY_READY=1',flush=True);return value
PROBE='''echo QDC_SHELL_PROBE_OK=1
P=$(pidof adbd)
echo ADBD_PID=$P
grep -E "VmRSS:|Threads:" /proc/$P/status
printf "ADBD_FD="
ls /proc/$P/fd | wc -l
grep -E "MemAvailable:|SUnreclaim:" /proc/meminfo
grep "^processes " /proc/stat'''
def watch(args):
    m=runpy.run_path(str(original('watch')))
    if args!=['--watch']:
        need(args in (['--test-alert'],['--check-recovery']),'WATCH_ARGS')
        if args==['--check-recovery']:
            with gate(G,True):
                need(idle(),'CALL_ACTIVE')
                rc=subprocess.call([PY,'-I','-B',str(original('watch'))]+args)
        else:rc=subprocess.call([PY,'-I','-B',str(original('watch'))]+args)
        sys.exit(rc)
    def cooldown():
        j=L/'qdc507-adb-usbreset-v1.json'
        if not j.exists():return True
        try:
            s=j.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o077,'UNTRUSTED_RESET_JOURNAL')
            t=float(json.loads(j.read_text())['epoch']);return 0<t<=time.time()-1800
        except Exception:return False
    def queue(kind,obs):
        body=('【NAS内部告警】QDC507 ADB Shell连续失败。' if kind=='ALERT' else '【NAS内部通知】QDC507 ADB已恢复。')+' 时间：'+m['now']()
        m['log'](('ALERT_QUEUED_TO_100 qid=' if kind=='ALERT' else 'RECOVERY_NOTICE_QUEUED_TO_100 qid=')+m['queue_message'](body))
    with m['LOCK'].open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        st=m['load_state']();m['log']('WATCH_V2_4_AUTO_RECOVERY_STARTED V44_COORDINATED=1')
        bridge=WatchBridge(G,E,generation=gen,idle=idle,probe=lambda:bounded_probe_command(baseadb()+['shell',PROBE]),pause=lambda:time.sleep(3),save=m['save_state'],log=m['log'],queue=queue,retry_due=m['qdc_retry_due'],cooldown=cooldown,adapter=Recovery())
        while True:
            bridge.cycle(st);time.sleep(60)
def entry(kind,args):
    if kind=='watch':watch(args);return
    if kind=='usb':
        need(args in (['--recover'],['--check']),'USB_ARGS');pin('usb_inner')
        if args==['--check']:
            with gate(G,True):need(idle(),'CALL_ACTIVE');sys.exit(subprocess.call(['/bin/sh',str(p.HOST['usb_inner']),'--check']))
        Recovery().execute('usb');return
    if kind=='coldboot':
        need(args in (['--boot'],['--check'],['--selftest']),'BOOT_ARGS')
        if args!=['--boot']:sys.exit(subprocess.call([PY,'-I','-B',str(original('startup'))]+args))
        Recovery().execute('coldboot');return
    raise RuntimeError('UNKNOWN_ENTRY')
# Keep original process command line for startup's PID scan and existing audit.
def wrapper(kind):
    return ('#!/usr/bin/python3.8\nimport runpy\nm=runpy.run_path('+repr(str(SELF))+')\nm["entry"]('+repr(kind)+',__import__("sys").argv[1:])\n').encode()
changes={p.HOST['watch']:(saved('original-host-watch'),wrapper('watch')),p.HOST['startup']:(saved('original-host-startup'),wrapper('coldboot')),p.HOST['usb_entry']:(saved('original-host-usb_entry'),saved('original-host-usb_entry').replace(b'exec /bin/sh /volume2/docker/asterisk/qdc507-16k-lab/qdc507-usb-recover-memoryfix-v22.sh "$MODE"',('exec '+PY+' -I -B '+str(SELF)+' usb "$MODE"').encode()))}
need(changes[p.HOST['usb_entry']][0]!=changes[p.HOST['usb_entry']][1],'USB_ANCHOR_DRIFT')
for name in ('v0','v0-edit'):changes[p.BASE/name/'extensions.conf']=(old,new)
def write(path,b):
    fd=os.open(str(path),os.O_RDWR|os.O_NOFOLLOW)
    with os.fdopen(fd,'r+b') as f:
        s=os.fstat(f.fileno());o=meta['host_files'][str(path)]
        need((s.st_dev,s.st_ino,s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))==(o['dev'],o['ino'],o['uid'],o['gid'],o['mode']),'FILE_IDENTITY_DRIFT_'+path.name)
        need(f.read() in changes[path],'UNKNOWN_LIVE_CONTENT_'+path.name)
        f.seek(0);f.write(b);f.truncate();f.flush();os.fsync(f.fileno())
    need(path.read_bytes()==b,'READBACK_FAILED_'+path.name)
def reload(which):
    p.ce('asterisk','-rx','dialplan reload');mark=(p.NS+'/agi-wrapper-v44' if which else p.R+'/agi-wrapper-inbound-v43').encode()
    for ctx in n.CONTEXTS:need(mark in p.ce('asterisk','-rx','dialplan show '+ctx),'LOADED_ROUTE_'+ctx)
def pids():
    a=[]
    for d in Path('/proc').iterdir():
        if not d.name.isdigit():continue
        try:
            v=(d/'cmdline').read_bytes().split(b'\0')
            if os.fsencode(str(p.HOST['watch'])) in v and b'--watch' in v:a.append(int(d.name))
        except OSError:pass
    return a
def children(pid):
    for d in Path('/proc').iterdir():
        if not d.name.isdigit():continue
        try:
            if re.search(r'^PPid:\s+'+str(pid)+r'\s*$',(d/'status').read_text(),re.M):return True
        except OSError:pass
    return False
def stop_watch():
    a=pids();need(len(a)==1,'WATCH_NOT_UNIQUE');pid=a[0]
    os.kill(pid,signal.SIGSTOP)
    try:
        time.sleep(.3);need(re.search(r'^State:\s+T',Path('/proc/%d/status'%pid).read_text(),re.M) and not children(pid),'WATCH_NOT_IDLE')
        os.kill(pid,signal.SIGTERM)
    finally:
        try:os.kill(pid,signal.SIGCONT)
        except ProcessLookupError:pass
    for _ in range(50):
        if not pids():return
        time.sleep(.1)
    raise RuntimeError('WATCH_DID_NOT_STOP')
def start_watch():
    need(not pids(),'WATCH_ALREADY_PRESENT');log=L/('qdc507-v44-watch-'+time.strftime('%Y%m%d-%H%M%S')+'.log')
    with log.open('x') as f:q=subprocess.Popen([PY,'-u','-I','-B',str(p.HOST['watch']),'--watch'],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    time.sleep(4);need(q.poll() is None and pids()==[q.pid] and 'WATCH_V2_4_AUTO_RECOVERY_STARTED' in log.read_text(errors='replace'),'WATCH_START_FAILED');return q.pid
def record(phase):
    value={'phase':phase,'runtime_sha256':sha(SELF.read_bytes()),'prestage':str(B),'changes':{str(k):[sha(a),sha(b)] for k,(a,b) in changes.items()}}
    tmp=J.with_name(J.name+'.'+secrets.token_hex(8))
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(tmp,J)
    fd=os.open(str(L),os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
def rollback():
    need(idle(),'CALL_ACTIVE')
    for path,(a,b) in changes.items():need(path.read_bytes() in (a,b),'UNKNOWN_LIVE_CONTENT_'+path.name)
    if pids():stop_watch()
    try:
        with gate(G,True):
            need(idle(),'CALL_ACTIVE');write_marker(E,'BLOCKED:explicit-rollback');stage()
            for path in reversed(list(changes)):write(path,changes[path][0])
            reload(False)
        start_watch();record('ROLLED_BACK');print('V43_RESTORED=1',flush=True)
    except BaseException:
        print('ROLLBACK_NOT_VERIFIED_KEEP_CURRENT_STATE=1',flush=True);raise
def activate():
    if J.exists():need(json.loads(J.read_text()).get('phase') in ('ROLLED_BACK','NO_ROUTES_CHANGED'),'ACTIVATION_JOURNAL_REVIEW_REQUIRED')
    need(n.current_container()==meta['container'] and n.identity()==ids,'PRODUCTION_IDENTITY_CHANGED')
    for k in p.HOST:pin(k)
    n.check_loaded();need(idle(),'CALL_ACTIVE');n.heartbeat(L/'qdc507-adb-alert.log')
    need(E.read_text().startswith('BLOCKED:'),'EPOCH_NOT_BLOCKED')
    for k in PINS:need((ns/k).read_bytes()==saved('candidate-'+k),'NAMESPACE_DRIFT_'+k)
    need((ns/'agi-wrapper-v44').read_bytes()==w and p.ast('cat',p.NS+'/agi-wrapper-v44')==w,'WRAPPER_DRIFT')
    for path,(a,b) in changes.items():need(path.read_bytes()==a,'BASELINE_DRIFT_'+path.name)
    record('PREPARED');changed=[]
    try:
        stop_watch()
        with gate(G,True):
            need(idle(),'CALL_ACTIVE');stage();post();g=gen()
            for path,(a,b) in changes.items():
                need(idle(),'CALL_STARTED_DURING_SWITCH');changed.append(path);write(path,b)
            reload(True);need(idle() and gen()==g,'BUSINESS_OR_USB_CHANGED')
            start_watch();write_marker(E,'READY:'+g+':'+secrets.token_hex(16));record('ACTIVE')
        print('V44_ACTIVE=1\nWATCH_COORDINATED=1\nRECOVERY_ENTRYPOINTS_ADAPTED=1\nAUDIT_PENDING_REAL_RECOVERY_TEST=1',flush=True)
    except BaseException:
        # Do not allow V43 to take over unknown active audio resources.
        if changed:
            try:
                if pids():stop_watch()
                with gate(G,True):
                    need(idle(),'ROLLBACK_DEFERRED_ACTIVE_CALL');write_marker(E,'BLOCKED:activation-failed');stage()
                    for path in reversed(changed):write(path,changes[path][0])
                    reload(False)
                start_watch();record('ROLLED_BACK');print('V43_RESTORED=1',flush=True)
            except Exception as e:print('ROLLBACK_DEFERRED='+str(e),flush=True)
        elif not pids():start_watch();record('NO_ROUTES_CHANGED')
        else:record('NO_ROUTES_CHANGED')
        raise
if __name__=='__main__':
    if sys.argv[1:]==['--activate']:activate()
    elif sys.argv[1:]==['--rollback']:rollback()
    elif len(sys.argv)>2:entry(sys.argv[1],sys.argv[2:])
    else:raise SystemExit('USE --activate OR usb/coldboot/watch MODE')
