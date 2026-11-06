#!/usr/bin/env python3
"""Executable AGI + one worker per call. Host Python only; no device Python."""
import fcntl, hashlib, os, pathlib, select, shlex, signal, subprocess, sys, time
BASE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path(os.environ.get('QDC_LOCAL_STATE', str(BASE / 'state')))
STOP = False

def stop(signum, frame):
    global STOP
    STOP = True

for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, stop)

def atomic(path, value):
    tmp = path.with_name(path.name + '.' + str(os.getpid()))
    tmp.write_text(value)
    tmp.replace(path)

def report_error(c, name, reason):
    """Preserve the earliest error, even when cleanup later fails."""
    message = str(reason).replace('\r', ' ').replace('\n', ' ')[:800]
    atomic(c / name, message)
    try:
        with (c / 'first.error').open('x') as fp:
            fp.write(name + ': ' + message + '\n')
    except FileExistsError:
        pass

class NotSentError(RuntimeError):
    """Definitely no ADB client process was started by this call."""


def rpc(action, token, timeout=6):
    if os.environ.get('QDC_RUNTIME_ENABLED') != 'exclusive-short-test':
        raise NotSentError('exclusive short test opt-in missing (not sent)')
    required = ('QDC_DEVICE_SCRIPT', 'QDC_DEVICE_ROOT', 'QDC_BIN_ROOT', 'QDC_ADB', 'QDC_SERIAL')
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise NotSentError(f'{action}: missing configuration {missing!r} (not sent)')
    cmd = ['/bin/sh', os.environ['QDC_DEVICE_SCRIPT'],
           os.environ['QDC_DEVICE_ROOT'], os.environ['QDC_BIN_ROOT'], action, token]
    adb = [os.environ['QDC_ADB'], '-H', '127.0.0.1', '-P', '5037', '-s',
           os.environ['QDC_SERIAL'], 'shell', shlex.join(cmd)]
    if os.environ.get('QDC_TEST_INTERPRETERS') == '1':
        adb.insert(0, sys.executable)
    # Query requests share one non-blocking gate. LAUNCH and CANCEL are NEVER
    # serialized behind a pending query. Preserve original early launch/query
    # overlap; a cancellation tombstone is set before dispatch by cancel().
    query_fd = None
    if action == 'query':
        STATE.mkdir(parents=True, exist_ok=True)
        query_fd = os.open(str(STATE / '.adb-query-gate'), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(query_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(query_fd)
            raise NotSentError('query: ADB_QUERY_BUSY (not sent)')
    try:
        try:
            p = subprocess.Popen(adb, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise NotSentError(f'{action}: ADB subprocess not started: {exc}') from exc
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            out, err = p.communicate()
            detail = err.decode(errors='replace').replace('\r', ' ').replace('\n', ' ')[:200]
            raise RuntimeError(f'{action}: ADB timeout; remote outcome UNKNOWN; stderr={detail}')
        output = out.decode(errors='replace').replace('\r', '').strip()
        error = err.decode(errors='replace').replace('\r', '').strip()
        # Device script uses exit 74 for an expected negative READY answer.
        # Do not misclassify this as the first unexpected ADB failure.
        if action == 'query' and p.returncode == 74 and output == f'NOT_READY {token}':
            return output
        if p.returncode:
            raise RuntimeError(f'{action}: rc={p.returncode}; stdout={output[:200]!r}; stderr={error[:300]!r}')
        return output
    finally:
        if query_fd is not None:
            fcntl.flock(query_fd, fcntl.LOCK_UN)
            os.close(query_fd)

def identity(pid):
    try:
        fields = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None

def worker_alive(c):
    try:
        pid, birth = (c / 'worker.pid').read_text().split()
        return identity(int(pid)) == birth
    except (OSError, ValueError):
        return False

def startup_valid(t, c):
    # A normal short-lived worker exit is not a call-liveness failure.
    try:
        if (c / 'launch.confirmed').read_text() == t:
            return True
    except OSError:
        pass
    return worker_alive(c)

def cancel(t, c):
    # Per-call lock prevents duplicate Worker/AGI/hangup cancellation.
    # 'cancel' is a LOCAL tombstone: subsequent query/launch checks stop.
    c.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(c / 'cancel.serialize'), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        (c / 'cancel').mkdir(exist_ok=True)
        if (c / 'cancel.result').exists():
            return True
        # Unknown remote outcome: never automatically send a duplicate cancel.
        if (c / 'cancel.attempted').exists():
            return False
        # If a Popen was definitely never created, permit at most one later
        # automatic attempt. Otherwise leave cleanup uncertain for an operator.
        count_path = c / 'cancel.notsent-count'
        try:
            count = int(count_path.read_text())
        except (OSError, ValueError):
            count = 0
        if count >= 2:
            report_error(c, 'cleanup.error', 'CLEANUP_NOT_SENT_RETRY_LIMIT')
            return False
        # Persist BEFORE dispatch to fail closed on a host process crash.
        # If rpc reports NotSentError, removing it is safe: Popen never started.
        atomic(c / 'cancel.attempted', '1')
        try:
            result = rpc('cancel', t, 11)
            if result not in [f'{s} {t}' for s in ('CLEAN', 'FENCED', 'STALE_FENCED')]:
                raise RuntimeError(f'unrecognized cancellation receipt: {result[:200]!r}')
            atomic(c / 'cancel.result', result)
            return True
        except NotSentError as exc:
            atomic(count_path, str(count + 1))
            (c / 'cancel.attempted').unlink()
            report_error(c, 'cleanup.deferred', 'CLEANUP_NOT_SENT: ' + str(exc))
            return False
        except Exception as exc:
            report_error(c, 'cleanup.error', 'CLEANUP_UNCONFIRMED: ' + str(exc))
            return False
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

def worker(t, c):
    atomic(c / 'worker.pid', f'{os.getpid()} {identity(os.getpid())}')
    try:
        if STOP or (c / 'cancel').exists():
            return
        # AGI queries early receipt on another ADB connection while this waits
        # through the unmodified helper launch sleep 1.
        if rpc('launch', t) != f'LAUNCH_HELD {t}':
            raise RuntimeError('unrecognized launch receipt')
        if STOP or (c / 'cancel').exists():
            cancel(t, c)
        else:
            # Submission completion, NOT audio readiness. No ongoing host work.
            atomic(c / 'launch.confirmed', t)
    except Exception as exc:
        report_error(c, 'fault', exc)
        cancel(t, c)
    finally:
        atomic(c / 'worker.done', '1')

def line(timeout):
    # Unbuffered stdin avoids select missing a line already buffered by Python.
    end = time.monotonic() + timeout
    data = bytearray()
    while time.monotonic() < end and not STOP:
        if select.select([0], [], [], min(.1, max(0, end-time.monotonic())))[0]:
            b = os.read(0, 1)
            if not b:
                raise RuntimeError('AGI EOF')
            if b == b'\n':
                return data.decode(errors='replace').rstrip('\r')
            data.extend(b)
            if len(data) > 8192:
                raise RuntimeError('AGI line too long')
    raise RuntimeError('AGI input timeout or signal')

def variable(name, value):
    print(f'SET VARIABLE {name} "{value}"', flush=True)
    reply = line(2)
    if reply != '200 result=1':
        raise RuntimeError('AGI rejected SET VARIABLE')

def agi(action, uid):
    t = hashlib.sha256(uid.encode()).hexdigest()
    c = STATE / t
    c.mkdir(parents=True, exist_ok=True)
    # Cleanup must run even when the hung-up channel has no AGI input/output.
    cleaned = cancel(t, c) if action == 'cancel' else None
    try:
        end = time.monotonic() + 3
        while line(max(.01, end-time.monotonic())):
            if time.monotonic() >= end:
                raise RuntimeError('AGI environment timeout')
        if action == 'cancel':
            variable('QDC_PA_CLEAN', int(cleaned))
            return
        name = {'submit': 'QDC_PA_SUBMITTED', 'wait': 'QDC_PA_READY'}[action]
        variable(name, 0)
        if action == 'submit':
            # Never reuse a token or restart a worker on duplicate submit.
            (c / 'submitted').mkdir()
            if (c / 'cancel').exists():
                raise RuntimeError('local tombstone')
            with (c / 'worker.log').open('ab', buffering=0) as log:
                subprocess.Popen([sys.executable, __file__, 'worker', t],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
        end = time.monotonic() + (5 if action == 'submit' else 12)
        next_query = 0.0
        while not STOP and time.monotonic() < end:
            if (c / 'cancel').exists() or (c / 'fault').exists():
                break
            if startup_valid(t, c):
                if time.monotonic() < next_query:
                    time.sleep(0.05)
                    continue
                next_query = time.monotonic() + 0.5
                try:
                    state = rpc('query', t, 2)
                except Exception as exc:
                    report_error(c, 'query.error', exc)
                    state = ''
                acceptable = [f'READY {t}']
                if action == 'submit':
                    acceptable.append(f'ACCEPTED {t}')
                if state in acceptable and not STOP and not (c / 'cancel').exists() and not (c / 'fault').exists() and startup_valid(t, c):
                    variable(name, 1)
                    return
            elif action == 'wait' or (c / 'worker.pid').exists():
                break
            time.sleep(.05)
        raise RuntimeError('receipt/ready failed or worker lost')
    except Exception as exc:
        report_error(c, 'agi.error', exc)
        if action != 'cancel':
            cancel(t, c)

if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('worker', 'submit', 'wait', 'cancel'):
        sys.exit(64)
    STATE.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == 'worker':
        worker(sys.argv[2], STATE / sys.argv[2])
    else:
        agi(sys.argv[1], sys.argv[2])
