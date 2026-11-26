"""Offline lifecycle coordination prototype. No production commands or paths.
Callers supply pinned, independently verified operations. All clients must use
one shared gate inode and see atomic epoch replacements via a directory mount.
"""
import os, re, pathlib, stat, secrets, json, time
from adb_coordination import gate, GateBusy, validate_protected

# Marker: READY:<NAS boot id>:<USB port/bus/devnum>:<fresh lifecycle nonce>.
# The provider must read real sysfs identity and verify the exact device serial.
# No marker or BLOCKED marker is an admission failure, not a usable epoch.
GEN_RE = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}:[0-9]+-[0-9]+(?:\.[0-9]+)*:[0-9]+:[0-9]+\Z')
READY_RE = re.compile(r'READY:([^\n]+):([a-f0-9]{32})\Z')

class LifecycleError(RuntimeError): pass

def read_epoch(path):
    p=pathlib.Path(path)
    fd=os.open(str(p),os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>512:
            raise LifecycleError('INVALID_EPOCH_FILE')
        if os.environ.get("QDC_PROTECTED_GATE") == "1":
            validate_protected(p, st)
        val=os.read(fd,513).decode('ascii').strip()
    finally: os.close(fd)
    m=READY_RE.fullmatch(val)
    if not m or not GEN_RE.fullmatch(m.group(1)):
        raise LifecycleError('LIFECYCLE_NOT_READY')
    return val

def write_marker(path, value):
    p=pathlib.Path(path)
    # Prototype assumes protected, local, preprovisioned directory. Never change gate inode.
    fd=os.open(str(p.parent),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    tmp='.'+p.name+'.'+secrets.token_hex(12)
    try:
        f=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=fd)
        with os.fdopen(f,'w') as out:
            out.write(value+'\n');out.flush();os.fsync(out.fileno())
        os.replace(tmp,p.name,src_dir_fd=fd,dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:os.unlink(tmp,dir_fd=fd)
        except FileNotFoundError:pass
        os.close(fd)

def maintain(gate_path, epoch_path, *, kind, preflight, run_recovery, verify, generation, restage):
    """Outer transaction: locks ALL coordinated voice RPCs, monitor and reconcile.
    Child recovery/restage must not re-acquire this gate (non-reentrant).
    Existing helper locks, pins, cooldowns, and pending journals stay intact.
    'preflight' checks no calls, no pending admitted call, appropriate recovery state.
    'verify' checks full business/runtime/Guard16 status after staging V44.
    Recovery can run with marker BLOCKED, but only explicit authorized invocation.
    """
    if kind not in ('usb','coldboot','adb-reset'):raise LifecycleError('INVALID_KIND')
    with gate(gate_path,True):
        if not preflight():raise LifecycleError('BUSINESS_NOT_QUIESCENT')
        nonce=secrets.token_hex(16)
        write_marker(epoch_path,'BLOCKED:'+kind+':'+nonce)
        # Any failure leaves marker BLOCKED, no automatic rollback/retry or unlock bypass.
        if not run_recovery():raise LifecycleError('RECOVERY_NOT_VERIFIED')
        first=generation()
        if not GEN_RE.fullmatch(first):raise LifecycleError('INVALID_GENERATION')
        if not restage():raise LifecycleError('V44_RESTAGE_NOT_VERIFIED')
        if not verify():raise LifecycleError('FINAL_BUSINESS_NOT_VERIFIED')
        if generation()!=first:raise LifecycleError('GENERATION_CHANGED_DURING_VERIFY')
        ready='READY:'+first+':'+nonce
        write_marker(epoch_path,ready)
        return ready

def verify_live_epoch(path,generation):
    """Reject a stale marker if NAS boot or USB enumeration changed out of band."""
    current=read_epoch(path)
    if READY_RE.fullmatch(current).group(1)!=generation():
        raise LifecycleError('LIVE_GENERATION_MISMATCH')
    return current


def monitor_cycle(gate_path,epoch_path,generation,idle,probe,pause):
    """One watcher cycle; skip = no evidence, don't increment/reset failure counters.
    Failures never themselves trigger recovery: caller must use existing guarded path.
    """
    def once():
        try:
            with gate(gate_path,True):
                try: before=verify_live_epoch(epoch_path,generation)
                except (OSError,LifecycleError):return 'LIFECYCLE_BLOCKED',None
                if not idle():return 'BUSY',None
                result=probe()
                try: after=verify_live_epoch(epoch_path,generation)
                except (OSError,LifecycleError):return 'LIFECYCLE_BLOCKED',None
                if after!=before:return 'LIFECYCLE_BLOCKED',None
                return 'OBSERVED',(before,result)
        except GateBusy:return 'BUSY',None
    status,one=once()
    if status!='OBSERVED':return status,None
    first_epoch,one=one
    if one[0]:return 'OK',one
    pause()
    status,two=once()
    if status!='OBSERVED':return 'SKIP_RETRY_'+status,None
    second_epoch,two=two
    if first_epoch!=second_epoch:return 'SKIP_RETRY_EPOCH_CHANGED',None
    return ('RETRY_OK' if two[0] else 'FAIL_CONFIRMED'),two

def sysfs_generation(usb_dir, boot_id_file, expected_vid, expected_pid, expected_serial):
    """Read-only prototype; explicit device pins required. Serial is sysfs serial,
    not assumed to equal ADB serial. On NAS validate these pins before deployment.
    Read twice to detect common enumeration races; cannot exclude every hotplug race.
    """
    p=pathlib.Path(usb_dir)
    if not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*',p.name):
        raise LifecycleError('INVALID_USB_PORT')
    if not all(re.fullmatch(r'[0-9a-f]{4}',v) for v in (expected_vid,expected_pid)) or not expected_serial:
        raise LifecycleError('USB_PINS_REQUIRED')
    def snap():
        vals=tuple((p/n).read_text().strip() for n in ('idVendor','idProduct','serial','busnum','devnum'))
        if vals[:3]!=(expected_vid,expected_pid,expected_serial):raise LifecycleError('USB_IDENTITY_MISMATCH')
        boot=pathlib.Path(boot_id_file).read_text().strip()
        val=boot+':'+p.name+':'+vals[3]+':'+vals[4]
        if not GEN_RE.fullmatch(val):raise LifecycleError('INVALID_GENERATION')
        return val
    a=snap()
    if snap()!=a:raise LifecycleError('USB_GENERATION_UNSTABLE')
    return a

def configured_generation():
    names=('QDC_USB_SYSFS','QDC_NAS_BOOT_ID','QDC_USB_VID','QDC_USB_PID','QDC_USB_SYSFS_SERIAL')
    try:return sysfs_generation(*(os.environ[k] for k in names))
    except KeyError as exc:raise LifecycleError('GENERATION_CONFIGURATION_MISSING') from exc
