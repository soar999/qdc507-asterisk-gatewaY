#!/usr/bin/python3.8
"""QDC507 IF1/IF2 functional device node manager."""
import os, sys, json, stat, fcntl, subprocess, importlib.util
from pathlib import Path

ROOT=Path('/volume2/docker/asterisk/qdc507-16k-lab')
ROLES=ROOT/'qdc507-if-role-manager-v1.py'
D=Path('/dev/qdc507')
NAME='qdc507-hardware'

def need(ok,msg):
    if not ok:
        raise RuntimeError(msg)

def run(*args,required=True):
    p=subprocess.run(
        args,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        universal_newlines=True,timeout=20
    )
    if required and p.returncode:
        raise RuntimeError('COMMAND_FAILED '+str(args[:2]))
    return p.stdout.strip()

def docker():
    s=run('docker','inspect',NAME,required=False)
    rows=json.loads(s or '[]')
    need(len(rows)==1,'FORMAL_CONTAINER_NOT_FOUND')
    return rows[0]

def discover():
    spec=importlib.util.spec_from_file_location(
        'qdc_roles',str(ROLES)
    )
    need(spec and spec.loader,'ROLE_MODULE_MISSING')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    first=mod.stable()
    need(first['ready'],'HOST_IF0_IF8_NOT_READY')

    mapping={}
    for n,role in ((2,'at'),(1,'pcm16')):
        x=first['rows'][n]
        need(x['driver']=='option' and len(x['tty'])==1,
             'IF%d_NOT_READY'%n)

        p=Path(x['tty'][0])
        st=p.stat()
        need(stat.S_ISCHR(st.st_mode) and
             os.major(st.st_rdev)==188,
             'INVALID_TTY_'+role)
        mapping[role]=(str(p),st.st_rdev)

    second=mod.stable()
    need(
        second['ready'] and
        second['generation']==first['generation'] and
        all(second['rows'][n]['tty']==first['rows'][n]['tty']
            for n in (1,2)),
        'USB_NOT_STABLE'
    )
    return first['generation'],mapping

def node(p):
    if not os.path.lexists(str(p)):
        return None
    st=p.lstat()
    need(stat.S_ISCHR(st.st_mode) and st.st_uid==0,
         'UNTRUSTED_DEVICE_NODE')
    return st

def check(g,mapping):
    if os.path.lexists(str(D)):
        st=D.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==0,
             'UNTRUSTED_DEVICE_DIRECTORY')
        mode=stat.S_IMODE(st.st_mode)
    else:
        mode=0

    ready=True
    for role,(host,rdev) in mapping.items():
        st=node(D/role) if mode else None
        ok=bool(
            mode==0o755 and st and
            st.st_rdev==rdev and st.st_gid==1000 and
            stat.S_IMODE(st.st_mode)==0o660
        )
        print(role.upper()+'_HOST='+host)
        print(role.upper()+'_NODE_OK='+str(int(ok)))
        ready=ready and ok

    print('USB_GENERATION='+':'.join(g[:3]))
    print('FUNCTION_NODES_STALE='+str(int(not ready)))
    return ready

def safe_to_change():
    c=docker()
    need(
        c['Config']['Image']==
        'qdc507-asterisk:dualmode-remotehangup-v4-prod',
        'FORMAL_IMAGE_CHANGED'
    )

    if c['State']['Running']:
        mounts={m['Destination']:m for m in c['Mounts']}
        need(
            '/dev/qdc507' in mounts and
            mounts['/dev/qdc507']['Source']==str(D),
            'FORMAL_NOT_YET_DYNAMIC_USB'
        )
        need(
            'c 188:* rwm' in
            (c['HostConfig'].get('DeviceCgroupRules') or []),
            'DYNAMIC_DEVICE_RULE_MISSING'
        )
        result=run(
            'docker','exec',NAME,'asterisk','-rx',
            'core show channels count'
        )
        need(
            '0 active channels' in result and
            '0 active calls' in result,
            'ACTIVE_CALLS'
        )
    else:
        state=c['State']
        need(
            state['Status']=='exited' and
            state['ExitCode']==0 and
            not state['OOMKilled'] and
            not state.get('Error'),
            'CONTAINER_EXIT_UNCLEAN'
        )

    need(
        run('systemctl','is-active',
            'qdc507-sms-tick.timer',
            required=False)=='inactive',
        'SMS_TIMER_MUST_BE_PAUSED'
    )
    need(
        run('systemctl','is-active',
            'qdc507-sms-tick.service',
            required=False)=='inactive',
        'SMS_WORKER_NOT_IDLE'
    )

def lock(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    need(st.st_uid==0 and not(st.st_mode&0o077),
         'LOCK_UNTRUSTED')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return fd

def sync(g,mapping):
    if not D.exists():
        D.mkdir(mode=0o755)

    st=D.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid==0,
         'UNTRUSTED_DEVICE_DIRECTORY')
    need(
        all(x.name in ('at','pcm16') for x in D.iterdir()),
        'UNKNOWN_FILE_IN_DEVICE_DIRECTORY'
    )
    os.chmod(str(D),0o755)

    for role,(host,rdev) in mapping.items():
        dest=D/role
        old=node(dest)

        if old and old.st_rdev==rdev and \
           old.st_gid==1000 and \
           stat.S_IMODE(old.st_mode)==0o660:
            continue

        current,ports=discover()
        need(current==g and ports==mapping,
             'USB_CHANGED_DURING_SYNC')

        temp=D/('.'+role+'-'+str(os.getpid()))
        need(not os.path.lexists(str(temp)),
             'TEMP_NODE_EXISTS')

        os.mknod(str(temp),stat.S_IFCHR|0o600,rdev)
        try:
            os.chown(str(temp),0,1000)
            os.chmod(str(temp),0o660)
            os.replace(str(temp),str(dest))
        finally:
            if os.path.lexists(str(temp)):
                os.unlink(str(temp))

    current,ports=discover()
    need(current==g and ports==mapping,
         'USB_CHANGED_AFTER_SYNC')
    need(check(current,ports),
         'FUNCTION_NODE_VERIFY_FAILED')

    print('FUNCTION_NODES_SYNCHRONIZED=1')
    print('DOCKER_UNCHANGED=1')

def main():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    need(sys.argv[1:] in (['--check'],['--sync']),
         'USE --check OR --sync')

    if sys.argv[1]=='--check':
        g,m=discover()
        need(check(g,m),'FUNCTION_NODES_NEED_SYNC')
        print('READ_ONLY_CHECK_PASSED=1')
        return

    locks=[]
    try:
        for name in (
            '/run/qdc507-if-role-manager.lock',
            '/run/qdc507-dynusb-layout.lock',
            '/var/run/qdc-dsm-registry-sync.lock'
        ):
            locks.append(lock(name))

        g,m=discover()
        if check(g,m):
            print('ALREADY_SYNCHRONIZED_NO_WRITES=1')
            return

        safe_to_change()
        sync(g,m)
    finally:
        for fd in reversed(locks):
            os.close(fd)

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        print('FUNCTION_NODE_STOPPED='+
              type(e).__name__+':'+str(e),
              file=sys.stderr)
        sys.exit(2)
