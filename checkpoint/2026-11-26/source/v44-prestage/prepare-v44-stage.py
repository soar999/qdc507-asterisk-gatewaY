#!/usr/bin/env python3
"""NAS read-only snapshot verification and PRIVATE staging, no ADB, no activation.
--check reads only. --stage creates a fresh root-only staging directory in LAB.
Never modify profiles, scheduler, container, active configs or recovery scripts.
Python 3.8 compatible; use python3.8 -B (not -I: imports sibling candidates).
"""
import argparse, hashlib, json, os, pathlib, re, shlex, stat, subprocess, sys, tempfile
from stage_routes import stage
P=pathlib.Path
LAB=P('/volume2/docker/asterisk/qdc507-16k-lab'); BASE=P('/volume2/docker/asterisk/qdc507-current')
PROFILE=LAB/'qdc507-mode-upgrade-c3e-preaudio-v2/profiles'; REMOTE='/opt/qdc507/runtime'
C='qdc507-hardware'; USB=P('/sys/bus/usb/devices/1-3.1'); BOOT=P('/proc/sys/kernel/random/boot_id')
R='/opt/qdc16k-preaudio-manual'; NS=REMOTE+'/v44-coordination'
PINS={
 'watch':'fdd4dcb51e5d3e646f6976c0d3447246fa814734568ab9d65749f0586574246b',
 'adb_reset':'9d656bea91419f5a46e3e4a4ac6b85163f61d67929f48030b3e2292e3c5888a6',
 'usb_inner':'a86b873982209df25a51ee3bf370b2cc651b80d9d4e5a9239cc940aee61826a4',
 'startup':'4c88ca59502c85a022148664ecbcc602c626bda3616944ef75dcd637643caa55',
 'cold_inner':'cb12e806767ede5007b484b598455d60388a1989e09be9b5ac10f0bb48f09756',
 'audit':'70513e059ef2c4e6c7451cae1fc76f7d74f2e82c07224968162510309801e4a8',
 'audit_inner':'3eae922542c792ec5a9080c5d9a6aa96a96da868ed1bec6a5260ca85715b2469',
 'usb_entry':'fb4025303fa51e8a3cac03039ab516c22f8cba5ba0bdcf902910d9f481e6d6d4',
 'dialplan':'1cf546b4ba413f34c1fd1cb4f48a326cdd1b15d9e5860990fbc3997ff10fd53a',
 'entrypoint':'53b2c8a678f7b5b61a2eb25d268810ef5d6a73947f60451db81b336d3138ae18',
 'host':'13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf',
 'device':'ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1',
 'wrapper':'472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98',
 'restager':'c85fbadc6c152729dfff1ff3aabbaac0ae8698cfe9469a120754cd36302aff7d'}
HOST={'watch':LAB/'qdc507-adb-watch-alert-memoryfix-v2.py','adb_reset':LAB/'qdc507-adb-recover-once-memoryfix-v2.py','usb_inner':LAB/'qdc507-usb-recover-memoryfix-v22.sh','startup':LAB/'qdc507-coldboot-startup-v221.py','cold_inner':LAB/'qdc507-coldboot-memoryfix-v221.py','audit':LAB/'qdc507-patch-audit-v3.sh','audit_inner':LAB/'qdc507-memoryfix-v22-audit.py','usb_entry':BASE/'recovery/qdc507-usb-recover.sh'}
CONTAINER={'entrypoint':'/usr/local/bin/entrypoint.sh','host':R+'/preaudio-inbound-v43.py','device':R+'/device-v43.sh','wrapper':R+'/agi-wrapper-inbound-v43','restager':R+'/qdc507-v43-device-restore.py'}
def need(ok, why):
    if not ok:raise RuntimeError(why)
def sha(b):return hashlib.sha256(b).hexdigest()
def run(*args):return subprocess.check_output(args,stderr=subprocess.PIPE,timeout=30)
def ce(*args):return run('docker','exec',C,*args)
def ast(*args):return run('docker','exec','-u','asterisk',C,*args)
def pin(k,b):
    need(sha(b)==PINS[k],'PRODUCTION_HASH_DRIFT_'+k);return b
def inventory(p):
    need(p.is_dir() and not p.is_symlink(),'PROFILE_DIRECTORY_INVALID')
    out={}
    for f in sorted(p.rglob('*')):
        need(not f.is_symlink(),'PROFILE_SYMLINK')
        if f.is_file():out[str(f.relative_to(p))]=sha(f.read_bytes())
    return out
def make_wrapper(old, identities, expected):
    need(sha(old)==expected,'WRAPPER_HASH_DRIFT')
    s=old.decode('utf-8')
    need(len(re.findall(r'\bQDC_SERIAL=[A-Za-z0-9._:-]+',s))==1,'WRAPPER_SERIAL_NOT_UNIQUE')
    need(re.fullmatch(r'[0-9a-f]{4}',identities['idVendor']) and re.fullmatch(r'[0-9a-f]{4}',identities['idProduct']) and re.fullmatch(r'[A-Za-z0-9._:-]+',identities['serial']),'SYSFS_IDENTITY_FORMAT')
    olddev='QDC_DEVICE_SCRIPT=/tmp/qdc16k-preaudio-manual/device-v43.sh'
    oldhost=R+'/preaudio-inbound-v43.py'
    need(s.count(olddev)==s.count(oldhost)==1 and 'QDC_PROTECTED_GATE' not in s,'WRAPPER_LAYOUT_DRIFT')
    vals={'QDC_ADB_GATE':NS+'/adb.lock','QDC_EPOCH_FILE':NS+'/epoch','QDC_PROTECTED_GATE':'1','QDC_USB_SYSFS':str(USB),'QDC_NAS_BOOT_ID':str(BOOT),'QDC_USB_VID':identities['idVendor'],'QDC_USB_PID':identities['idProduct'],'QDC_USB_SYSFS_SERIAL':identities['serial']}
    addition=' '.join(k+'='+shlex.quote(v) for k,v in vals.items())
    s=s.replace(olddev,addition+' QDC_DEVICE_SCRIPT=/tmp/qdc16k-preaudio-manual/device-v44.sh').replace(oldhost,R+'/preaudio-v44-candidate.py')
    z=subprocess.run(['/bin/sh','-n'],input=s.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE);need(z.returncode==0,'WRAPPER_SYNTAX')
    return s.encode()
def collect():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    m=json.loads(run('docker','inspect',C))[0];need(m['State']['Running'],'CONTAINER_NOT_RUNNING')
    bind=[x for x in m['Mounts'] if x['Destination']==REMOTE]
    need(len(bind)==1 and bind[0]['Type']=='bind' and bind[0]['Source']==str(PROFILE) and bind[0]['RW'] is False,'PROFILE_BIND_DRIFT')
    st=PROFILE.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,'PROFILE_ROOT_UNTRUSTED')
    hoststat=f'{st.st_dev}:{st.st_ino}:{st.st_mode & 0o7777:o}:{st.st_uid}:{st.st_gid}'
    need(ast('stat','-c','%d:%i:%a:%u:%g',REMOTE).decode().strip()==hoststat,'PROFILE_INODE_PERMISSION_MISMATCH')
    need(ast('id','-u').strip()==b'1000' and ast('id','-g').strip()==b'1000','ASTERISK_ID_DRIFT')
    need(ast('cat','/etc/qdc507-mode/current').strip()==b'16k','NOT_16K')
    plan=json.loads((PROFILE.parent/'plan.json').read_bytes()); expected=plan['source_profile_hashes']
    for mode in ('8k','16k'):
        need(inventory(PROFILE/mode)==expected[mode],'PROFILE_INVENTORY_DRIFT_'+mode)
    need(not (PROFILE/'v44-coordination').exists() and not (PROFILE/'v44-coordination').is_symlink(),'COORDINATION_ALREADY_EXISTS')
    print('PROFILE_READONLY_BIND_AND_INVENTORIES=PASS',flush=True)
    def identity():return {n:(USB/n).read_text().strip() for n in ('idVendor','idProduct','serial','busnum','devnum')}
    ids=identity();boot=BOOT.read_text().strip()
    for n,v in ids.items():need(v and ast('cat',str(USB/n)).decode().strip()==v,'SYSFS_MISMATCH_'+n)
    need(boot and ast('cat',str(BOOT)).decode().strip()==boot,'BOOT_ID_MISMATCH')
    out={k:pin(k,p.read_bytes()) for k,p in HOST.items()}
    out.update({k:pin(k,ce('cat',p)) for k,p in CONTAINER.items()})
    dp=pin('dialplan',(BASE/'v0/extensions.conf').read_bytes());need(dp==pin('dialplan',(BASE/'v0-edit/extensions.conf').read_bytes()),'DIALPLAN_PAIR_DRIFT')
    wrapper=make_wrapper(out['wrapper'],ids,PINS['wrapper']); candidate,count=stage(dp.decode('utf-8'))
    need(identity()==ids and BOOT.read_text().strip()==boot,'USB_CHANGED_DURING_STAGE')
    need(json.loads(run('docker','inspect',C))[0]['Id']==m['Id'],'CONTAINER_CHANGED_DURING_STAGE')
    print('PRODUCTION_PINS=PASS\nWRAPPER_AND_ROUTES_DERIVED=PASS\nROUTE_WRAPPER_REFERENCES='+str(count),flush=True)
    return {'agi-wrapper-v44':wrapper,'extensions-v0.candidate.conf':candidate.encode(),'extensions-v0-edit.candidate.conf':candidate.encode()}
def main():
    a=argparse.ArgumentParser();x=a.add_mutually_exclusive_group(required=True);x.add_argument('--check',action='store_true');x.add_argument('--stage',action='store_true');args=a.parse_args();files=collect()
    if args.stage:
        d=P(tempfile.mkdtemp(prefix='qdc507-v44-private-stage-',dir=str(LAB)));d.chmod(0o700)
        for n,b in files.items():
            fd=os.open(str(d/n),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        (d/'SHA256SUMS').write_text(''.join(sha(b)+'  '+n+'\n' for n,b in files.items()))
        print('PRIVATE_STAGE='+str(d))
    print('PRODUCTION_UNCHANGED=1\nNO_ADB=1\nDEPLOYMENT_AUTHORIZED=0')
if __name__=='__main__':
    try:main()
    except Exception as e:print('PREPARE_STOP='+type(e).__name__+':'+(str(e)[:120] if isinstance(e,RuntimeError) else 'CHECK_FAILED'),file=sys.stderr);sys.exit(2)
