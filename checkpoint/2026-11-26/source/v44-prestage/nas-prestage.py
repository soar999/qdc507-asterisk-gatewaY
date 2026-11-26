#!/usr/bin/env python3
"""Read-only inspection and private staging of exact NAS V43 baseline.
Never installs, calls ADB, stops processes, edits scheduler, or changes routes.
Use --check for no writes; --stage for a NEW root-only LAB directory.
"""
import argparse, hashlib, importlib.util, json, os, pathlib, re, stat, subprocess, sys, tempfile, time
from datetime import datetime
from stage_routes import CONTEXTS, A, B
P=pathlib.Path
HERE=P(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('prepare_v44',str(HERE/'prepare-v44-stage.py'))
prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep)
from restage_v44 import PINS as PAYLOAD_PINS
PAYLOAD=tuple(PAYLOAD_PINS)+('nas_recovery_adapter.py','nas_watch_bridge.py','watch_adapter.py','observation.py','restage_v44.py','release_transaction.py')
class Stop(RuntimeError):pass
def need(ok,msg):
    if not ok:raise Stop(msg)
def sha(b):return hashlib.sha256(b).hexdigest()
def trusted_read(p, owner=None):
    fd=os.open(str(p),os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_nlink==1,'UNTRUSTED_FILE_'+p.name)
        if owner is not None:need(s.st_uid==owner and not s.st_mode&0o022,'UNTRUSTED_OWNER_OR_MODE_'+p.name)
        with os.fdopen(os.dup(fd),'rb') as f:b=f.read()
        t=os.fstat(fd);need((s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(t.st_size,t.st_mtime_ns,t.st_ctime_ns),'FILE_CHANGED_DURING_READ_'+p.name)
        v=p.lstat();need((s.st_dev,s.st_ino)==(v.st_dev,v.st_ino),'FILE_INODE_CHANGED_'+p.name)
        return b,{'dev':s.st_dev,'ino':s.st_ino,'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,'sha256':sha(b)}
    finally:os.close(fd)
def identity():
    return {'boot':prep.BOOT.read_text().strip(),**{n:(prep.USB/n).read_text().strip() for n in ('idVendor','idProduct','serial','busnum','devnum')}}
def current_container():
    m=json.loads(prep.run('docker','inspect',prep.C))[0]
    need(m['State']['Running'],'CONTAINER_NOT_RUNNING')
    return {'id':m['Id'],'image':m['Image'],'started_at':m['State']['StartedAt']}
def heartbeat(path, now=None):
    now=time.time() if now is None else now
    s=path.stat();need(0<=now-s.st_mtime<=180,'WATCH_HEARTBEAT_STALE')
    with path.open('rb') as f:
        f.seek(max(0,s.st_size-8192));lines=f.read().splitlines()
    need(lines and b' SHELL_OK ' in lines[-1] and b'QDC_SHELL_PROBE_OK=1' in lines[-1],'LAST_WATCH_SAMPLE_NOT_HEALTHY')
    return lines[-1].decode('utf-8','replace')
def scheduler_contract(raw):
    # DSM task records can have multiline script bodies. Do not print raw scripts.
    names={'coldboot':'qdc507-coldboot-startup-v221.py','audit':'qdc507-patch-audit-v3.sh','watch':'qdc507-adb-watch-alert-memoryfix-v2.py','usb':'qdc507-usb-recover.sh'}
    return {k:len(re.findall(r'(?<![A-Za-z0-9_.-])'+re.escape(v)+r'(?![A-Za-z0-9_.-])',raw)) for k,v in names.items()}
def check_loaded():
    for ctx in sorted(CONTEXTS):
        out=prep.ce('asterisk','-rx','dialplan show '+ctx).decode('utf-8','replace')
        need(A in out and B not in out,'LOADED_ROUTE_DRIFT_'+ctx)
    print('LOADED_V43_CONTEXTS=PASS',flush=True)
def idle():
    out=prep.ce('asterisk','-rx','core show channels count').decode('utf-8','replace')
    return bool(re.search(r'^\s*0 active channels\s*$',out,re.M) and re.search(r'^\s*0 active calls\s*$',out,re.M))
def capture():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    # This is an observation, not a maintenance barrier. No mutation follows.
    container=current_container();ids=identity()
    candidates=prep.collect()
    private={};host_meta={}
    for k,p in prep.HOST.items():
        b,meta=trusted_read(p,owner=0);prep.pin(k,b);private['original-host-'+k]=b;host_meta[str(p)]=meta
    for label in ('v0','v0-edit'):
        p=prep.BASE/label/'extensions.conf';b,meta=trusted_read(p);prep.pin('dialplan',b);private['original-extensions-'+label+'.conf']=b;host_meta[str(p)]=meta
    for k,p in prep.CONTAINER.items():
        private['original-container-'+k]=prep.pin(k,prep.ce('cat',p))
    need(private['original-extensions-v0.conf']==private['original-extensions-v0-edit.conf'],'DIALPLAN_PAIR_DRIFT')
    for k in PAYLOAD:
        b,_=trusted_read(HERE/k)
        if k in PAYLOAD_PINS:need(sha(b)==PAYLOAD_PINS[k],'PAYLOAD_DRIFT_'+k)
        private['candidate-'+k]=b
    check_loaded()
    print('IDLE_AT_PREFLIGHT='+str(int(idle())),flush=True)
    sample=heartbeat(prep.LAB/'qdc507-adb-alert.log')
    print('WATCH_LAST_SAMPLE='+sample,flush=True)
    raw=prep.run('/usr/syno/bin/synoschedtask','--get').decode('utf-8','replace')
    task=scheduler_contract(raw)
    for k,v in task.items():print('DSM_TASK_REFERENCE_'+k.upper()+'='+str(v),flush=True)
    # References are inventory, not evidence a task is enabled or safe to switch.
    print('DSM_TASK_REFERENCES_ONLY=1',flush=True)
    private['original-scheduler-inventory.txt']=raw.encode()
    for k,b in candidates.items():private[k]=b
    need(current_container()==container and identity()==ids,'LIVE_IDENTITY_CHANGED_DURING_CAPTURE')
    for p,old in host_meta.items():
        _,new=trusted_read(P(p));need(old==new,'HOST_BASELINE_CHANGED_DURING_CAPTURE')
    for k,p in prep.CONTAINER.items():need(sha(prep.ce('cat',p))==prep.PINS[k],'CONTAINER_BASELINE_CHANGED_DURING_CAPTURE')
    meta={'format':'qdc507-v44-private-prestage-v1','created_at':datetime.now().astimezone().isoformat(),'container':container,'usb_identity':ids,'host_files':host_meta,'scheduler_reference_counts':task,'source_pins':prep.PINS,'files':{k:sha(v) for k,v in private.items()},'activation_authorized':False}
    return private,meta

def save_private(lab, files, meta):
    d=P(tempfile.mkdtemp(prefix='qdc507-v44-private-prestage-',dir=str(lab)));d.chmod(0o700)
    data=dict(files);data['manifest.json']=(json.dumps(meta,indent=2,sort_keys=True)+'\n').encode()
    data['SHA256SUMS']=(''.join(sha(b)+'  '+k+'\n' for k,b in sorted(data.items()))).encode()
    for name,b in data.items():
        need(P(name).name==name and name not in ('.','..'),'INVALID_STAGE_FILENAME')
        fd=os.open(str(d/name),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        need(sha((d/name).read_bytes())==sha(b),'STAGE_READBACK_FAILED')
    fd=os.open(str(d),os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    # Publish receipt LAST; incomplete directory must never be considered ready.
    receipt={'format':'qdc507-v44-prestage-receipt-v1','manifest_sha256':sha(data['manifest.json']),'sums_sha256':sha(data['SHA256SUMS']),'activation_authorized':False}
    fd=os.open(str(d/'PRESTAGE-ONLY.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(receipt,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    fd=os.open(str(d),os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
    return d

def main():
    a=argparse.ArgumentParser();x=a.add_mutually_exclusive_group(required=True);x.add_argument('--check',action='store_true');x.add_argument('--stage',action='store_true');args=a.parse_args()
    files,meta=capture()
    if args.stage:
        d=save_private(prep.LAB,files,meta)
        print('PRIVATE_PRESTAGE='+str(d),flush=True)
        print('PRIVATE_BASELINE_AND_CANDIDATES_SAVED=1',flush=True)
    print('PRESTAGE_CHECK=PASS\nNO_ADB=1\nPRODUCTION_UNCHANGED=1\nDEPLOYMENT_AUTHORIZED=0',flush=True)
if __name__=='__main__':
    try:main()
    except Exception as e:
        print('PRESTAGE_STOP='+type(e).__name__+(':'+str(e) if isinstance(e,Stop) else (':'+str(e)[:140] if type(e) is RuntimeError else '')),file=sys.stderr);sys.exit(2)
