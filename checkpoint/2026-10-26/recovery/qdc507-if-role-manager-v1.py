#!/usr/bin/python3.8
"""QDC507 IF0-IF8 functional USB manager."""
import fcntl, os, sys, time, subprocess, json
from pathlib import Path

USB=Path('/sys/bus/usb/devices')
TTY=Path('/sys/class/tty')
OPT=Path('/sys/bus/usb/drivers/option/unbind')
PREP='/usr/local/sbin/qdc507-boot-prep.py'
AUDIO='/usr/local/sbin/qdc507-host-audio-repair.py'
DOCKER='/usr/local/bin/docker'
LIVE='qdc507-hardware'

EXPECTED={
 5:(0xff,0x42,0x01),
 6:(0x01,0x01,0x00),
 7:(0x01,0x02,0x00),
 8:(0x01,0x02,0x00)
}

class Stop(Exception):pass

def need(ok,msg):
    if not ok:raise Stop(msg)

def read(p):
    return p.read_text().strip()

def hx(p):
    return int(read(p),16)

def owner(p):
    q=p/'driver'
    return q.resolve(strict=True).name if q.is_symlink() else 'none'

def device():
    matches=[]
    for p in USB.iterdir():
        try:
            if (read(p/'idVendor').lower()=='2ca3' and
                read(p/'idProduct').lower()=='4006'):
                matches.append(p)
        except OSError:
            pass
    need(len(matches)==1,'QDC507_NOT_UNIQUE_OR_MISSING')
    return matches[0]

def generation(d):
    return (
        d.name,
        read(d/'busnum'),
        read(d/'devnum'),
        read(d/'serial')
    )

def iface(d,n):
    p=USB/(d.name+':1.'+str(n))
    need(p.is_dir(),'IF%d_MISSING'%n)
    need(hx(p/'bInterfaceNumber')==n,
         'IF%d_NUMBER_CHANGED'%n)

    desc=tuple(hx(p/x) for x in (
        'bInterfaceClass',
        'bInterfaceSubClass',
        'bInterfaceProtocol'
    ))

    if n<5:
        need(desc[0]==0xff,'IF%d_NOT_VENDOR_SERIAL'%n)
        if n==2:
            need(desc==(0xff,0,0),'IF2_AT_DESCRIPTOR_CHANGED')
    else:
        need(desc==EXPECTED[n],
             'IF%d_DESCRIPTOR_CHANGED'%n)
    return p

def ttys(p):
    target=p.resolve(strict=True)
    result=[]
    for t in TTY.glob('ttyUSB*'):
        try:
            x=(t/'device').resolve(strict=True)
            node=Path('/dev')/t.name
            if (x==target or target in x.parents) and \
               node.is_char_device():
                result.append(str(node))
        except OSError:
            pass
    return sorted(result)

def audio_ready(d):
    try:
        names=('controlC0','pcmC0D0p','pcmC0D0c')
        need(all((Path('/dev/snd')/n).is_char_device()
                 for n in names),'SOUND_NODE_MISSING')
        need('Baiwang' in Path('/proc/asound/cards').read_text(),
             'QDC507_CARD_NOT_FOUND')
        control=Path('/sys/class/sound/controlC0/device').resolve()
        target=d.resolve()
        return control==target or target in control.parents
    except (OSError,Stop):
        return False

def snapshot():
    d=device()
    g=generation(d)
    rows=[]

    for n in range(9):
        p=iface(d,n)
        rows.append({
            'n':n,
            'path':p,
            'driver':owner(p),
            'tty':ttys(p) if n<5 else []
        })

    need(generation(d)==g,'USB_CHANGED_DURING_SCAN')

    serial=all(x['driver']=='option' and len(x['tty'])==1
               for x in rows[:5])
    adb=rows[5]['driver'] in ('none','usbfs')
    audio=all(x['driver']=='snd-usb-audio'
              for x in rows[6:]) and audio_ready(d)

    return {
        'generation':g,
        'rows':rows,
        'serial':serial,
        'adb':adb,
        'audio':audio,
        'ready':serial and adb and audio
    }

def stable():
    a=snapshot()
    time.sleep(1)
    b=snapshot()
    need(a['generation']==b['generation'],
         'USB_ENUMERATION_NOT_STABLE')
    return b

def report(s):
    roles=[
        'VENDOR','PCM16','AT_SMS',
        'VENDOR','VENDOR','ADB',
        'UAC_CONTROL','UAC_CAPTURE','UAC_PLAYBACK'
    ]
    print('USB_GENERATION=%s:%s:%s'%s['generation'][:3])

    for x in s['rows']:
        n=x['n']
        tty=','.join(x['tty']) or '-'
        print('IF%d_%s=%s driver=%s'%(
            n,roles[n],tty,x['driver']
        ))

    print('SERIAL_OK=%d'%s['serial'])
    print('ADB_OK=%d'%s['adb'])
    print('AUDIO_OK=%d'%s['audio'])
    print('HOST_USB_READY=%d'%s['ready'])

def run(args,timeout=90):
    p=subprocess.run(args,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        universal_newlines=True,timeout=timeout)
    need(p.returncode==0,
         'COMMAND_FAILED_'+str(args[0])+':'+
         (p.stdout+p.stderr)[-250:])

def no_calls():
    p=subprocess.run([DOCKER,'inspect',LIVE],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        universal_newlines=True,timeout=15)

    need(p.returncode==0,'FORMAL_CONTAINER_MISSING')
    rows=json.loads(p.stdout or '[]')
    need(len(rows)==1,'CONTAINER_AMBIGUOUS')

    if not rows[0]['State']['Running']:
        return

    p=subprocess.run([
        DOCKER,'exec',LIVE,'asterisk','-rx',
        'core show channels count'
    ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
      universal_newlines=True,timeout=15)

    need(p.returncode==0 and
         '0 active channels' in p.stdout and
         '0 active calls' in p.stdout,
         'ACTIVE_OR_UNKNOWN_CALLS')

def lock(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    st=os.fstat(fd)
    need(st.st_uid==0 and not(st.st_mode&0o077),
         'LOCK_PERMISSIONS_BAD')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    return fd

def same(g):
    need(generation(device())==g,
         'USB_GENERATION_CHANGED_ABORT')

def prepare():
    a=lock('/run/qdc507-if-role-manager.lock')
    b=lock('/var/run/qdc-dsm-registry-sync.lock')

    try:
        s=stable()
        report(s)

        if s['ready']:
            print('HOST_ALREADY_READY_NO_WRITES=1')
            return

        no_calls()
        g=s['generation']

        # Restore serial support and ensure sound modules are loaded.
        if not s['serial'] or not s['audio']:
            need(Path(PREP).is_file(),'BOOT_PREP_MISSING')
            print('PREPARING_HOST_DRIVERS=1',flush=True)
            run(['/usr/bin/python3.8','-B',PREP])
            same(g)

        s=stable()
        need(s['generation']==g,'USB_CHANGED_AFTER_PREP')

        # IF5 belongs to ADB, never to option.
        x=s['rows'][5]

        if x['driver']=='option':
            no_calls()
            same(g)
            need(OPT.is_file(),'OPTION_UNBIND_MISSING')
            with OPT.open('w') as f:
                f.write(x['path'].name)
            same(g)
            print('IF5_OPTION_RELEASED=1',flush=True)
        else:
            need(x['driver'] in ('none','usbfs'),
                 'IF5_UNEXPECTED_DRIVER')

        s=stable()
        need(s['generation']==g,'USB_CHANGED_AFTER_IF5')

        # IF6-IF8 belong to snd-usb-audio.
        if not s['audio']:
            no_calls()
            same(g)
            need(Path(AUDIO).is_file(),'AUDIO_HELPER_MISSING')
            print('REPAIRING_IF6_IF8=1',flush=True)
            run(['/usr/bin/python3.8','-B',AUDIO])
            same(g)

        s=stable()
        need(s['generation']==g,
             'USB_CHANGED_DURING_RECOVERY')

        report(s)
        need(s['ready'],'HOST_ROLES_STILL_INCORRECT')
        print('IF0_IF8_HOST_RECOVERY_COMPLETE=1')
        print('DOCKER_NOT_MODIFIED=1')

    finally:
        os.close(b)
        os.close(a)

def main():
    need(len(sys.argv)==2 and
         sys.argv[1] in ('--check','--prepare'),
         'USE_--check_OR_--prepare')
    need(os.geteuid()==0,'ROOT_REQUIRED')

    if sys.argv[1]=='--check':
        s=stable()
        report(s)
        need(s['ready'],'HOST_ROLES_NEED_REPAIR')
    else:
        prepare()

if __name__=='__main__':
    try:
        main()
    except (Stop,OSError,ValueError,
            subprocess.TimeoutExpired,BlockingIOError) as e:
        print('IF_ROLE_STOPPED='+type(e).__name__+
              ':'+str(e),file=sys.stderr)
        sys.exit(2)
