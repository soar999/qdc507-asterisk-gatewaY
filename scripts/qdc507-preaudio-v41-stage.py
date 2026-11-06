#!/usr/bin/env python3
"""Offline-only QDC507 Pre-audio v4.1 HOST-side candidate builder.

Never edits the running container, device /tmp, or Asterisk dialplan.
Copy the currently deployed preaudio.py into LAB root before staging.
"""
import argparse
import ast
import difflib
import hashlib
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
EXPECTED = '2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690'

ERROR_HELPER = '''def report_error(c, name, reason):
    """Preserve the earliest error, even when cleanup later fails."""
    message = str(reason).replace('\\r', ' ').replace('\\n', ' ')[:800]
    atomic(c / name, message)
    try:
        with (c / 'first.error').open('x') as fp:
            fp.write(name + ': ' + message + '\\n')
    except FileExistsError:
        pass

'''

RPC = '''def rpc(action, token, timeout=6):
    if os.environ.get('QDC_RUNTIME_ENABLED') != 'exclusive-short-test':
        raise RuntimeError('exclusive short test opt-in missing')
    cmd = ['/bin/sh', os.environ['QDC_DEVICE_SCRIPT'],
           os.environ['QDC_DEVICE_ROOT'], os.environ['QDC_BIN_ROOT'], action, token]
    adb = [os.environ['QDC_ADB'], '-H', '127.0.0.1', '-P', '5037', '-s',
           os.environ['QDC_SERIAL'], 'shell', shlex.join(cmd)]
    if os.environ.get('QDC_TEST_INTERPRETERS') == '1':
        adb.insert(0, sys.executable)
    # All Pre-audio callers share one host-side gate. At most one ADB Shell
    # session from THIS subsystem is in flight; unrelated services are untouched.
    STATE.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(STATE / '.adb-shell-gate'), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + 3.0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'{action}: ADB_GATE_BUSY (not sent)')
                time.sleep(0.05)
        p = subprocess.Popen(adb, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            out, err = p.communicate()
            detail = err.decode(errors='replace').replace('\\r', ' ').replace('\\n', ' ')[:200]
            raise RuntimeError(f'{action}: ADB timeout; remote outcome UNKNOWN; stderr={detail}')
        output = out.decode(errors='replace').replace('\\r', '').strip()
        error = err.decode(errors='replace').replace('\\r', '').strip()
        if p.returncode:
            raise RuntimeError(f'{action}: rc={p.returncode}; stdout={output[:200]!r}; stderr={error[:300]!r}')
        return output
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

'''

CANCEL = '''def cancel(t, c):
    # One attempt per call, regardless of overlapping AGI/worker/hangup paths.
    # A failed/unknown remote outcome is NOT retried automatically. Recovery is
    # a separate, operator-controlled procedure; never delete the remote lock.
    c.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(c / 'cancel.serialize'), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        (c / 'cancel').mkdir(exist_ok=True)
        if (c / 'cancel.result').exists():
            return True
        if (c / 'cancel.attempted').exists():
            return False
        atomic(c / 'cancel.attempted', '1')
        try:
            result = rpc('cancel', t, 11)
            if result not in [f'{s} {t}' for s in ('CLEAN', 'FENCED', 'STALE_FENCED')]:
                raise RuntimeError(f'unrecognized cancellation receipt: {result[:200]!r}')
            atomic(c / 'cancel.result', result)
            return True
        except Exception as exc:
            report_error(c, 'cleanup.error', 'CLEANUP_UNCONFIRMED: ' + str(exc))
            return False
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

'''


def replace_func(src, name, after, replacement):
    start = src.index('def ' + name + '(')
    end = src.index('\ndef ' + after + '(', start)
    return src[:start] + replacement.rstrip() + '\n' + src[end:]


def patch(src):
    if 'import hashlib, os, pathlib, select, shlex, signal, subprocess, sys, time' not in src:
        raise ValueError('ORIGINAL_IMPORT_SIGNATURE_CHANGED')
    src = src.replace('import hashlib, os, pathlib, select, shlex, signal, subprocess, sys, time',
                      'import fcntl, hashlib, os, pathlib, select, shlex, signal, subprocess, sys, time', 1)
    if src.count('def rpc(action, token, timeout=6):') != 1:
        raise ValueError('EXPECTED_SINGLE_RPC')
    src = src.replace('def rpc(action, token, timeout=6):', ERROR_HELPER + 'def rpc(action, token, timeout=6):', 1)
    src = replace_func(src, 'rpc', 'identity', RPC)
    src = replace_func(src, 'cancel', 'worker', CANCEL)
    checks = {
        "atomic(c / 'fault', str(exc))": "report_error(c, 'fault', exc)",
        "atomic(c / 'agi.error', str(exc))": "report_error(c, 'agi.error', exc)",
        "                except Exception:\n                    state = ''": "                except Exception as exc:\n                    report_error(c, 'query.error', exc)\n                    state = ''",
    }
    for old, new in checks.items():
        if src.count(old) != 1:
            raise ValueError('UNEXPECTED_SNIPPET_COUNT: ' + old[:50])
        src = src.replace(old, new, 1)
    # Keep the pre-ring and post-answer READY semantics, but cut Shell query rate.
    anchor = "        end = time.monotonic() + (5 if action == 'submit' else 12)\n"
    if src.count(anchor) != 1 or src.count("            if startup_valid(t, c):") != 1:
        raise ValueError('WAIT_LOOP_SIGNATURE_CHANGED')
    src = src.replace(anchor, anchor + '        next_query = 0.0\n', 1)
    src = src.replace('            if startup_valid(t, c):\n',
                      '            if startup_valid(t, c):\n'
                      '                if time.monotonic() < next_query:\n'
                      '                    time.sleep(0.05)\n'
                      '                    continue\n'
                      '                next_query = time.monotonic() + 0.5\n', 1)
    ast.parse(src)
    return src


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True,
                    help='Local LAB copy of the deployed preaudio.py (never the live path)')
    ap.add_argument('--output-dir', type=Path, default=LAB)
    args = ap.parse_args()
    source = args.source.resolve()
    outdir = args.output_dir.resolve()
    if source.parent != outdir or outdir != LAB:
        raise SystemExit('REFUSE: source and outputs must be in LAB root')
    if source.name != 'preaudio-original.py':
        raise SystemExit('REFUSE: source must be preaudio-original.py')
    if args.source.is_symlink() or not args.source.is_file() or args.output_dir.is_symlink():
        raise SystemExit('REFUSE: invalid or symlink source/output')
    original_bytes = args.source.read_bytes()
    digest = hashlib.sha256(original_bytes).hexdigest()
    if digest != EXPECTED:
        raise SystemExit(f'REFUSE: original SHA changed: {digest}')
    original = original_bytes.decode('utf-8')
    candidate = patch(original)
    compiled = compile(candidate, 'qdc507-preaudio-v41-candidate.py', 'exec')
    assert compiled
    target = outdir / 'qdc507-preaudio-v41-candidate.py'
    diff = outdir / 'qdc507-preaudio-v41.diff'
    for p in (target, diff):
        if p.exists() or p.is_symlink():
            raise SystemExit(f'REFUSE: output already exists: {p}')
    summary = ''.join(difflib.unified_diff(original.splitlines(keepends=True),
                 candidate.splitlines(keepends=True),
                 fromfile='preaudio-original.py',
                 tofile='qdc507-preaudio-v41-candidate.py'))
    for p, content in ((target, candidate), (diff, summary)):
        with p.open('x', encoding='utf-8') as f:
            f.write(content)
        p.chmod(0o600)
    print('SOURCE_SHA256_OK=1')
    print('CANDIDATE_SYNTAX_OK=1')
    print('OUTPUT=' + str(target))
    print('DIFF=' + str(diff))
    print('NO_PRODUCTION_CHANGES=1')
    print('NOT_APPROVED_FOR_LIVE_DEPLOYMENT=1')


if __name__ == '__main__':
    main()
