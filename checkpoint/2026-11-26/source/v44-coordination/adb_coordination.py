"""OFFLINE candidate. Explicit shared inode; never creates/repairs production lock.
Host and container must bind the SAME existing regular file; epoch provider
must be independently verified against actual USB/boot generation.
"""
import contextlib, fcntl, os, pathlib, stat, time

class GateBusy(RuntimeError): pass

def validate_protected(path, st):
    """Extra live checks for root-owned files in existing read-only profile bind.
    Root ownership/mode checks are not an ACL or mount verification; deployment
    must independently verify the actual mount and path on both sides.
    """
    p = pathlib.Path(path)
    if st.st_uid != 0 or st.st_mode & 0o022 or st.st_nlink != 1:
        raise RuntimeError('UNTRUSTED_COORDINATION_FILE')
    for parent in (p.parent, p.parent.parent):
        d = parent.lstat()
        if not stat.S_ISDIR(d.st_mode) or d.st_uid != 0 or d.st_mode & 0o022:
            raise RuntimeError('UNTRUSTED_COORDINATION_DIRECTORY')
    current = p.lstat()
    if (current.st_dev, current.st_ino) != (st.st_dev, st.st_ino):
        raise RuntimeError('COORDINATION_INODE_CHANGED')

@contextlib.contextmanager
def gate(path, exclusive, timeout=0):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    protected = os.environ.get("QDC_PROTECTED_GATE") == "1"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise RuntimeError('INVALID_GATE_FILE')
        if protected:
            validate_protected(path, st)
        deadline = time.monotonic()+timeout
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline: raise GateBusy('ADB_GATE_BUSY')
                time.sleep(.025)
        current = os.stat(path, follow_symlinks=False)
        if (st.st_dev, st.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError('GATE_INODE_CHANGED')
        if protected:
            validate_protected(path, st)
        yield
    finally:
        os.close(fd)

def epoch(path):
    value=pathlib.Path(path).read_text().strip()
    if not value or len(value)>256 or '\n' in value: raise RuntimeError('INVALID_EPOCH')
    return value

def probe_if_idle(path, idle_check, probe):
    """probe must enforce its OWN bounded timeout. None means skipped, not failure."""
    try:
        with gate(path, True):
            if not idle_check(): return None
            return probe()
    except GateBusy:
        return None

def confirmed_cycle(path, idle_check, probe, pause=lambda:time.sleep(3)):
    """No failure-cycle or recovery decision from an incomplete observation."""
    first=probe_if_idle(path,idle_check,probe)
    if first is None: return 'SKIP',None
    if first[0]: return 'OK',first
    pause()
    second=probe_if_idle(path,idle_check,probe)
    if second is None: return 'SKIP_RETRY',None
    return ('RETRY_OK' if second[0] else 'FAIL_CONFIRMED'),second

def reconcile_one(host, token, gate_path, expected_epoch, actual_epoch, idle_check):
    """One operator/coordinator-authorized token; never scans or deletes state.
    actual_epoch and idle_check are trusted host callbacks. No auto reset.
    Reuses existing device token fence; no recursive shared lock under exclusive.
    """
    if len(token)!=64 or any(c not in '0123456789abcdef' for c in token):return False
    c=host.STATE/token
    try:
        # Reject a coordinator accidentally wired to a different gate inode.
        if not os.path.samefile(gate_path,os.environ['QDC_ADB_GATE']):return False
        with gate(gate_path, True):
            def valid():
                return (epoch(os.environ['QDC_EPOCH_FILE'])==expected_epoch and
                        actual_epoch()==expected_epoch and idle_check() and
                        actual_epoch()==expected_epoch and
                        (c/'launch.epoch').read_text().strip()==expected_epoch)
            if not valid(): return False
            # Recheck under per-token lock immediately before dispatch.
            return host.reconcile_cancel(token,c,valid,rpc_call=host.rpc_raw)
    except (GateBusy,OSError,RuntimeError,KeyError):
        return False


def bounded_probe_command(argv, timeout=2.0):
    """Local, directly executed pinned ADB client only (not docker exec).
    Prototype: bounds local wait, cannot guarantee remote shell already exited.
    """
    import signal, subprocess
    if not 0 < timeout <= 2.0: raise ValueError('PROBE_TIMEOUT_OUT_OF_RANGE')
    try:
        p=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,start_new_session=True,text=True)
    except OSError as exc:return False,125,type(exc).__name__
    try:
        out,err=p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        try:p.communicate(timeout=.25)
        except subprocess.TimeoutExpired:
            # No unbounded communicate after kill; mark unknown and fail closed.
            return False,124,'TIMEOUT_LOCAL_EXIT_UNCONFIRMED'
        return False,124,'TIMEOUT_REMOTE_OUTCOME_UNKNOWN'
    detail=' '.join((out+' '+err).split())[:350]
    return p.returncode==0 and 'QDC_SHELL_PROBE_OK=1' in out,p.returncode,detail
