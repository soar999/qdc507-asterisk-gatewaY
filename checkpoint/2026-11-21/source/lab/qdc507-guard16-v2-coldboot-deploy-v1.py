from pathlib import Path
import os,json,stat,hashlib,time,subprocess

L=Path('/volume2/docker/asterisk/qdc507-16k-lab')
B2=L/'qdc507-dynusb-memoryfix-v2-baseline.json'
B22=L/'qdc507-dynusb-memoryfix-v22-baseline.json'
H=L/'qdc507-guard16-runtime-memoryfix-v2.py'
G=L/'qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py'
R=L/'qdc507-dynusb-oneclick-complete-20261121-121411.json'
PY='/usr/bin/python3.8'
C='qdc507-hardware'

def need(x,s):
    if not x: raise SystemExit('STOP_'+s)

def readj(p):
    st=p.lstat()
    need(stat.S_ISREG(st.st_mode) and st.st_uid==0
         and not (st.st_mode&0o077),'UNTRUSTED_'+p.name)
    raw=p.read_bytes()
    need(20<=len(raw)<=200000,'SIZE_'+p.name)
    return raw,json.loads(raw.decode())

def run(a,t=30):
    return subprocess.run(a,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,timeout=t)

def generation():
    found=[]
    for d in Path('/sys/bus/usb/devices').iterdir():
        try:
            if ((d/'idVendor').read_text().strip().lower()=='2ca3'
                and (d/'idProduct').read_text().strip().lower()=='4006'):
                found.append([d.name,(d/'busnum').read_text().strip(),
                    (d/'devnum').read_text().strip(),
                    (d/'serial').read_text().strip()])
        except OSError:
            pass
    need(len(found)==1,'QDC507_USB_NOT_UNIQUE')
    return found[0]

need(os.geteuid()==0 and L.is_dir() and not L.is_symlink(),
     'ROOT_OR_LAB')

for p in (B2,B22,H,G,R):
    need(p.is_file() and not p.is_symlink(),'MISSING_'+p.name)

helper=H.read_text()
need('qdc507-dynusb-memoryfix-v2-baseline.json' in helper,
     'V2_BASELINE_PATH_CHANGED')

b2raw,b2=readj(B2)
_,b22=readj(B22)
_,receipt=readj(R)

need(b2.get('format')==b22.get('format')=='qdc507-dynusb-oneclick-v1',
     'BASELINE_FORMAT')
need(b2.get('mode')==b22.get('mode')=='16k','BASELINE_MODE')

for k in ('serial','container_id','image_id'):
    need(b2.get(k)==b22.get(k),'BASELINE_CONTEXT_'+k)

gsha=hashlib.sha256(G.read_bytes()).hexdigest()
need(b2.get('scripts',{}).get(G.name)==gsha
     and b22.get('scripts',{}).get(G.name)==gsha,'GUARD_PIN')

boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
need(b22.get('host_boot_id')==boot,'V22_COLD_BOOT_NOT_SEALED')

gen=generation()
need(b22.get('usb_generation')==gen,'V22_USB_GENERATION_NOT_CURRENT')

need(receipt.get('format')=='qdc507-dynusb-oneclick-attempt-v1'
     and receipt.get('phase')=='COMPLETE'
     and receipt.get('container_id')==b22.get('container_id')
     and receipt.get('generation_detected')==gen,
     'COLD_BOOT_RECEIPT_INVALID')

p=run(['docker','inspect',C],20)
need(p.returncode==0,'DOCKER_INSPECT_FAILED')
c=json.loads(p.stdout)
need(len(c)==1 and c[0].get('Id')==b22.get('container_id')
     and c[0].get('Image')==b22.get('image_id')
     and c[0].get('State',{}).get('Running') is True,
     'CONTAINER_CONTEXT')

print('COLD_BOOT_RECEIPT_VERIFIED=1')
print('V22_BASELINE_CURRENT_BOOT_USB_CONTAINER_OK=1')

# 仅同步冷启动基线，不改变脚本及设备文件
if (b2.get('host_boot_id')!=b22.get('host_boot_id')
    or b2.get('usb_generation')!=b22.get('usb_generation')):

    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    backup=L/('qdc507-dynusb-memoryfix-v2-before-coldboot-sync-'+stamp+'.json')

    fd=os.open(str(backup),os.O_WRONLY|os.O_CREAT|
               os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(b2raw)
        f.flush()
        os.fsync(f.fileno())

    updated=dict(b2,host_boot_id=b22['host_boot_id'],
                 usb_generation=b22['usb_generation'])
    payload=(json.dumps(updated,indent=2,ensure_ascii=False)+'\n').encode()

    tmp=L/(B2.name+'.tmp-'+str(os.getpid()))
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|
               os.O_EXCL|os.O_NOFOLLOW,0o600)

    try:
        with os.fdopen(fd,'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

        need(B2.read_bytes()==b2raw,'BASELINE_CHANGED')
        need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot
             and generation()==gen,'BOOT_USB_CHANGED')

        os.replace(str(tmp),str(B2))
    finally:
        if tmp.exists():
            tmp.unlink()

    need(hashlib.sha256(B2.read_bytes()).hexdigest()
         ==hashlib.sha256(payload).hexdigest(),'BASELINE_SYNC_VERIFY')

    print('V2_BASELINE_BACKUP='+str(backup))
    print('V2_BASELINE_COLD_BOOT_CONTEXT_SYNCED=1')
else:
    print('V2_BASELINE_ALREADY_CURRENT=1')

# 直接部署 V2
print('===== ENSURE V2 =====')
p=run([PY,'-I','-B',str(H),'--ensure-v2'],240)
print(p.stdout,end='')
if p.stderr:
    print(p.stderr,end='')

if p.returncode:
    print('ENSURE_V2_RC='+str(p.returncode))
    q=run([PY,'-I','-B',str(H),'--check'],100)
    print(q.stdout,end='')
    if q.stderr:
        print(q.stderr,end='')
    raise SystemExit('V2_NOT_CONFIRMED_NO_RETRY')

# 最终验收
q=run([PY,'-I','-B',str(H),'--check'],100)
print(q.stdout,end='')
if q.stderr:
    print(q.stderr,end='')

need(q.returncode==0
     and 'READ_ONLY_RUNTIME_CHECK_OK=1' in q.stdout
     and 'DEVICE_CURRENT_MODE=v2' in q.stdout
     and 'DEVICE_V2_STAGE=present' in q.stdout
     and 'DEVICE_V1_BACKUP=present' in q.stdout,
     'V2_FINAL_VERIFY_FAILED')

print('GUARD16_V2_DEPLOYMENT_COMPLETE=1')
print('V1_INACTIVE_BACKUP_RETAINED_FOR_ROLLBACK=1')
