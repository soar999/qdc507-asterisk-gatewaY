#!/usr/bin/python3.8
"""Start existing qdc507-hardware only after verified host QDC507 audio; report status."""
import hashlib,json,os,pathlib,stat,subprocess,sys,time

P=pathlib.Path
DOCKER=P('/volume2/@appstore/Docker/usr/bin/docker')
USB=P('/sys/bus/usb/devices')
REPORT=P('/var/lib/qdc507-coldstart-qtest-last.txt')
IMAGE='sha256:f18b0d39ab4f0849aaf92ea4327d150881eb7dc89790d599a806ed39ab9a923b'
CONTAINER='qdc507-hardware'

class Abort(Exception):pass

def need(c,why):
    if not c: raise Abort(why)

def run(*args,timeout=45):
    try:
        c=subprocess.run(
            [str(DOCKER),*args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root'},
            stdin=subprocess.DEVNULL)
    except (OSError,subprocess.TimeoutExpired):
        raise Abort('DOCKER_TIMEOUT')
    need(c.returncode==0,'DOCKER_COMMAND_FAILED:'+args[0])
    return c.stdout.strip()

def inspect(n):
    arr=json.loads(run('inspect',n))
    need(len(arr)==1,'CONTAINER_UNKNOWN:'+n)
    return arr[0]

def generation():
    d=[]
    for x in USB.iterdir():
        try:
            if ((x/'idVendor').read_text().strip().lower()=='2ca3' and
                (x/'idProduct').read_text().strip().lower()=='4006'):
                d.append(x)
        except OSError:
            pass
    need(len(d)==1,'QDC507_NOT_UNIQUE')
    u=d[0]
    return u,(u.name,
              (u/'busnum').read_text().strip(),
              (u/'devnum').read_text().strip(),
              (u/'serial').read_text().strip())

def driver(p):
    d=p/'driver'
    return d.resolve(strict=True).name if d.is_symlink() else 'none'

def rdev(path):
    st=P(path).stat()
    need(stat.S_ISCHR(st.st_mode),'NOT_CHAR_DEVICE:'+str(path))
    return (os.major(st.st_rdev),os.minor(st.st_rdev))

def container_rdev(path):
    x=run('exec',CONTAINER,'stat','-c','%t:%T',path,timeout=20)
    try:
        return tuple(int(v,16) for v in x.split(':'))
    except ValueError:
        raise Abort('CONTAINER_DEVICE_STAT_BAD:'+path)

def mappings_ok(at):
    if container_rdev('/dev/ttyUSB0') != rdev(at):
        return False

    for n in ('controlC0','pcmC0D0p','pcmC0D0c','timer'):
        host='/dev/snd/'+n
        guest='/dev/snd/'+n
        if container_rdev(guest) != rdev(host):
            return False

    return True

def main():
    need(os.geteuid()==0,'ROOT_REQUIRED')

    need(
        hashlib.sha256(DOCKER.read_bytes()).hexdigest()
        =='273ad259f3dc771edcb185e0fc7d5aefd307615f8e2b256d0b5774482475253c',
        'DOCKER_BINARY_CHANGED')

    u,g=generation()
    print('USB_GENERATION='+g[0]+':'+g[2])

    need(
        all(driver(USB/(u.name+':1.'+str(n)))=='snd-usb-audio'
            for n in (6,7,8)),
        'AUDIO_DRIVERS_NOT_READY')

    cards=P('/proc/asound/cards').read_text()
    need('Baiwang' in cards,'SOUND_CARD_NOT_READY')

    ctl=P('/sys/class/sound/controlC0/device').resolve(strict=True)
    need(u.resolve() in ctl.parents,'SOUND_CARD_DIFFERENT_USB')

    for n in ('controlC0','pcmC0D0p','pcmC0D0c','timer'):
        need((P('/dev/snd')/n).is_char_device(),
             'SOUND_NODE_MISSING:'+n)

    at=P('/dev/qdc507-at')
    need(at.is_symlink() and at.resolve().is_char_device(),
         'IF2_ALIAS_NOT_READY')

    st=at.stat()
    need(st.st_gid==1000 and stat.S_IMODE(st.st_mode)==0o660,
         'IF2_PERMISSIONS_WRONG')

    d=inspect(CONTAINER)

    need(d['Image']==IMAGE,'QDC507_IMAGE_CHANGED')

    src=[
        x.get('PathOnHost')
        for x in (d['HostConfig'].get('Devices') or [])
        if x.get('PathInContainer')=='/dev/ttyUSB0'
    ]
    need(src==['/dev/qdc507-at'],
         'QDC507_DEVICE_SOURCE_CHANGED')

    need(not d['State']['Paused'],'QDC507_CONTAINER_PAUSED')
    need(generation()[1]==g,'USB_GENERATION_CHANGED')

    if not d['State']['Running']:
        print('STEP=START_QDC507_HARDWARE')
        run('start',CONTAINER,timeout=90)
    else:
        print('STEP=QDC507_HARDWARE_ALREADY_RUNNING')

    d=inspect(CONTAINER)
    need(d['State']['Running'],'QDC507_NOT_RUNNING_AFTER_START')

    # If Docker started before the final USB/ALSA generation settled,
    # refresh all device mappings once.
    if not mappings_ok(at):
        print('STEP=RESTART_QDC507_HARDWARE_FOR_DEVICE_MAPPING')
        run('restart','-t','25',CONTAINER,timeout=90)
        d=inspect(CONTAINER)
        need(d['State']['Running'],'QDC507_NOT_RUNNING_AFTER_RESTART')
        need(mappings_ok(at),'QDC507_DEVICE_MAPPING_MISMATCH')

    need(generation()[1]==g,'USB_GENERATION_CHANGED_AFTER_START')

    print('QDC507_RUNNING_DEVICE_MAPPING_OK=1')

if __name__=='__main__':
    try:
        with REPORT.open('w') as f:
            old=sys.stdout
            sys.stdout=f
            try:
                print('TIME='+time.strftime('%F %T %z'))
                main()
                print('EXIT=0')
            finally:
                sys.stdout=old
        rc=0
    except Exception as e:
        with REPORT.open('a') as f:
            f.write('DEFER='+str(e)+'\nEXIT=2\n')
        rc=2

    REPORT.chmod(0o644)
    print('QDC507_START_EXIT='+str(rc))
    sys.exit(rc)
