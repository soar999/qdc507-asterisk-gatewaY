#!/usr/bin/env python3
"""Build & offline-test a QDC507 Pre-audio v4.2 HOST controller candidate.

This tool is deliberately OFFLINE ONLY: it never calls Docker, a real ADB,
Asterisk, or QDC507, and it never edits production code or dialplan. It reads
verified local LAB copies of v4.1 and the known good original and writes a v4.2
candidate plus diff in the LAB ROOT. --all runs a synthetic end-to-end call.

No code generated here is approved for live deployment without further review.
"""
import argparse
import ast
import difflib
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
ORIGINAL_SHA = '2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690'
V41_SHA = '887719e6a544e8887a587cc28dd386cfd3dcb9b604a8c0c9093bd79d3704fbe4'
CANDIDATE = 'qdc507-preaudio-v42-candidate.py'
DIFF = 'qdc507-preaudio-v42.diff'

RPC = '''class NotSentError(RuntimeError):
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
            detail = err.decode(errors='replace').replace('\\r', ' ').replace('\\n', ' ')[:200]
            raise RuntimeError(f'{action}: ADB timeout; remote outcome UNKNOWN; stderr={detail}')
        output = out.decode(errors='replace').replace('\\r', '').strip()
        error = err.decode(errors='replace').replace('\\r', '').strip()
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

'''

CANCEL = '''def cancel(t, c):
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

'''


def need(condition, label):
    if not condition:
        raise RuntimeError(label)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_func(src, name, next_func, replacement):
    a = src.index('def ' + name + '(')
    b = src.index('\ndef ' + next_func + '(', a)
    return src[:a] + replacement.rstrip() + '\n' + src[b:]


def patch(old):
    need(old.count('def rpc(action, token, timeout=6):') == 1,
         'V41_RPC_SIGNATURE_MISMATCH')
    need(old.count('def cancel(t, c):') == 1, 'V41_CANCEL_SIGNATURE_MISMATCH')
    need('import fcntl, hashlib, os, pathlib, select, shlex, signal, subprocess, sys, time' in old,
         'V41_IMPORT_SIGNATURE_MISMATCH')
    # Two function replacements only; preserve v4.1 first-error reporting,
    # 500ms READY polling, and all original AGI/worker lifecycle code unchanged.
    new = replace_func(old, 'rpc', 'identity', RPC)
    new = replace_func(new, 'cancel', 'worker', CANCEL)
    need('next_query = time.monotonic() + 0.5' in new, 'QUERY_THROTTLE_MISSING')
    need("report_error(c, 'fault', exc)" in new, 'FAULT_PRESERVATION_MISSING')
    ast.parse(new)
    return new


def verify_sources(root):
    need(root.is_dir() and not root.is_symlink(), 'INVALID_LAB_ROOT')
    p0 = root / 'preaudio-original.py'
    p1 = root / 'qdc507-preaudio-v41-candidate.py'
    for p in (p0, p1):
        need(p.is_file() and not p.is_symlink() and p.resolve().parent == root,
             'SOURCE_MISSING_OR_INVALID: ' + p.name)
    need(sha(p0.read_bytes()) == ORIGINAL_SHA, 'ORIGINAL_SHA_MISMATCH')
    need(sha(p1.read_bytes()) == V41_SHA, 'V41_SHA_MISMATCH')
    return p1.read_text('utf-8')


def stage(root):
    source = verify_sources(root)
    new = patch(source)
    destination = root / CANDIDATE
    diff = root / DIFF
    summary = ''.join(difflib.unified_diff(source.splitlines(True), new.splitlines(True),
                           fromfile='qdc507-preaudio-v41-candidate.py', tofile=CANDIDATE))
    for p, value in ((destination, new), (diff, summary)):
        if p.exists() or p.is_symlink():
            need(p.is_file() and not p.is_symlink() and
                 p.read_text('utf-8') == value, 'OUTPUT_EXISTS_BUT_DIFFERENT: ' + p.name)
        else:
            with p.open('x', encoding='utf-8') as f:
                f.write(value)
            p.chmod(0o600)
    print('ORIGINAL_SHA256_OK=1')
    print('V41_SHA256_OK=1')
    print('V42_CANDIDATE_SHA256=' + sha(new.encode('utf-8')))
    print('OUTPUT=' + str(destination))
    print('DIFF=' + str(diff))


def load_candidate(path, state):
    os.environ['QDC_LOCAL_STATE'] = str(state)
    spec = importlib.util.spec_from_file_location('v42_offline', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offline_tests(root):
    verify_sources(root)
    candidate = root / CANDIDATE
    need(candidate.is_file() and not candidate.is_symlink(), 'STAGE_FIRST')
    src = candidate.read_text('utf-8')
    need(src == patch((root / 'qdc507-preaudio-v41-candidate.py').read_text('utf-8')),
         'CANDIDATE_CHANGED')
    with tempfile.TemporaryDirectory(prefix='qdc-v42-offline-', dir=str(root)) as temp:
        scratch = Path(temp)
        state = scratch / 'host'
        state.mkdir()
        mod = load_candidate(candidate, state)
        print('PASS=EXACT_V41_TO_V42_DIFF_AND_SYNTAX')
        # Basic deterministic tests without spawning any real ADB client.
        c = state / 'first'; c.mkdir()
        mod.report_error(c, 'fault', 'first READY problem')
        mod.report_error(c, 'cleanup.error', 'error: closed')
        need((c / 'first.error').read_text().startswith('fault: first READY problem'),
             'FIRST_ERROR_LOST')
        print('PASS=FIRST_ERROR_PRESERVED')

        c = state / 'unsent'; c.mkdir()
        counter = []
        def unsent_rpc(action, token, timeout=6):
            counter.append('unsent')
            raise mod.NotSentError('ADB executable missing before spawn')
        mod.rpc = unsent_rpc
        need(not mod.cancel('unsent', c) and not (c / 'cancel.attempted').exists(),
             'NOT_SENT_ERRONEOUSLY_FENCED')
        def good_rpc(action, token, timeout=6):
            counter.append('sent')
            return 'CLEAN ' + token
        mod.rpc = good_rpc
        need(mod.cancel('unsent', c), 'DEFERRED_CANCEL_CANNOT_RECOVER')
        need(counter == ['unsent', 'sent'], 'NOT_SENT_RETRY_BAD')
        print('PASS=NOT_SENT_RETRY_ONCE_THEN_CLEAN')

        c = state / 'unknown'; c.mkdir()
        counter = []
        def unknown_rpc(action, token, timeout=6):
            counter.append('sent')
            raise RuntimeError('cancel: rc=1 stderr=error: closed')
        mod.rpc = unknown_rpc
        need(not mod.cancel('unknown', c) and not mod.cancel('unknown', c),
             'UNKNOWN_REMOTE_RETRIED')
        need(counter == ['sent'] and (c / 'cancel.attempted').exists(),
             'UNKNOWN_REMOTE_DUPLICATED')
        print('PASS=UNKNOWN_OUTCOME_NO_DUPLICATE_CANCEL')

        c = state / 'concurrent'; c.mkdir()
        counter = []
        lock = threading.Lock()
        def slow_rpc(action, token, timeout=6):
            with lock: counter.append(1)
            time.sleep(0.15)
            return 'CLEAN ' + token
        mod.rpc = slow_rpc
        results = []
        threads = [threading.Thread(target=lambda: results.append(mod.cancel('concurrent', c)))
                   for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(5)
        need(all(not t.is_alive() for t in threads) and results == [True, True]
             and counter == [1], 'CONCURRENT_CANCEL_BROKEN')
        print('PASS=CONCURRENT_CANCEL_ONLY_ONE_RPC')

        # Full local fake-ADB lifecycle; no real ADB, no Docker, no device calls.
        fake = scratch / 'fake-adb.py'
        fake.write_text('#!' + sys.executable + '''
import os, pathlib, shlex, sys, time
root=pathlib.Path(os.environ['QDC_FAKE_ROOT'])
token_cmd=shlex.split(sys.argv[-1]); action=token_cmd[-2]; token=token_cmd[-1]
with (root/'events').open('a') as f:
 f.write(f'{time.monotonic():.6f} {action}_start\\n')
if action == 'launch':
 (root/'accepted').write_text(token)
 time.sleep(1.15)
 print('LAUNCH_HELD '+token)
elif action == 'query':
 if (root/('cancelled-'+token)).exists() or not (root/'accepted').exists():
  print('NOT_READY '+token)
  sys.exit(74)
 age=time.time()-(root/'accepted').stat().st_mtime
 print(('READY ' if age>.72 else 'ACCEPTED ')+token)
elif action == 'cancel':
 (root/('cancelled-'+token)).write_text(token)
 print('CLEAN '+token)
else: sys.exit(65)
''')
        fake.chmod(0o700)
        os.environ.update(QDC_RUNTIME_ENABLED='exclusive-short-test',
                          QDC_DEVICE_SCRIPT='/offline/device.sh',
                          QDC_DEVICE_ROOT='/offline/device-state',
                          QDC_BIN_ROOT='/offline/binaries',
                          QDC_ADB=str(fake),
                          QDC_SERIAL='OFFLINE-ONLY',
                          QDC_FAKE_ROOT=str(scratch))
        # Test real pre-dispatch subprocess failure, not just a mocked exception.
        missing_adb = scratch / 'missing-adb-binary'
        os.environ['QDC_ADB'] = str(missing_adb)
        mod = load_candidate(candidate, state)
        missing_case = state / 'binary-absent'
        need(not mod.cancel('binary-absent', missing_case) and
             not (missing_case / 'cancel.attempted').exists() and
             (missing_case / 'cleanup.deferred').exists(),
             'Popen_NOT_SENT_WRONGLY_MARKED_ATTEMPTED')
        os.environ['QDC_ADB'] = str(fake)
        need(mod.cancel('binary-absent', missing_case),
             'Popen_NOT_SENT_COULD_NOT_RETRY')
        print('PASS=REAL_POPEN_FAILURE_SAFE_RETRY')
        # Copy candidate locally: worker subprocess uses __file__, must stay offline.
        local_candidate = scratch / 'preaudio.py'
        shutil.copyfile(candidate, local_candidate)
        mod = load_candidate(local_candidate, state)
        values = []
        mod.line = lambda timeout: ''
        mod.variable = lambda name, value: values.append((name, value))
        uid = 'offline-inbound-v4-fast-answer'
        token = hashlib.sha256(uid.encode()).hexdigest()
        mod.agi('submit', uid)
        need(('QDC_PA_SUBMITTED', 1) in values, 'SUBMIT_FAILED')
        # Wait for READY before ringing, then recheck after answer.
        mod.agi('wait', uid)
        need(('QDC_PA_READY', 1) in values, 'PRERING_READY_FAILED')
        values.clear()
        mod.agi('wait', uid)
        need(('QDC_PA_READY', 1) in values, 'POSTANSWER_RECHECK_FAILED')
        values.clear()
        mod.agi('cancel', uid)
        need(('QDC_PA_CLEAN', 1) in values, 'CLEANUP_AFTER_ANSWER_FAILED')
        need(not (state / token / 'fault').exists(), 'UNEXPECTED_WORKER_FAULT')
        need(not (state / token / 'first.error').exists(), 'EXPECTED_NOT_READY_AS_ERROR')
        events = (scratch / 'events').read_text().splitlines()
        launch_times = [float(x.split()[0]) for x in events if 'launch_start' in x]
        query_times = [float(x.split()[0]) for x in events if 'query_start' in x]
        need(len(launch_times) == 1 and len(query_times) >= 2,
             'FAKE_ADB_EVENT_COUNT_WRONG')
        need(query_times[0] < launch_times[0] + 1.15,
             'EARLY_QUERY_BLOCKED_BY_LAUNCH')
        print('PASS=SUBMIT_PRERING_READY_POSTANSWER_READY_CANCEL')
        print('PASS=EARLY_QUERY_CONCURRENT_WITH_LAUNCH')
        print('PASS=EXPECTED_NOT_READY_NOT_RECORDED_AS_ERROR')

        # Real RPC parser, but fake ADB always reports error: closed.
        bad = scratch / 'bad-adb.sh'
        bad.write_text('#!/bin/sh\necho "error: closed" >&2\nexit 1\n')
        bad.chmod(0o700)
        os.environ['QDC_ADB'] = str(bad)
        try:
            mod.rpc('query', token, timeout=1)
        except RuntimeError as exc:
            need('error: closed' in str(exc) and 'stderr=' in str(exc),
                 'ADB_STDERR_NOT_PRESERVED')
        else:
            raise RuntimeError('EXPECTED_FAKE_ADB_FAILURE')
        print('PASS=ADB_STDERR_PRESERVED')
    print('OFFLINE_V42_TESTS_PASSED=1')
    print('NO_DEVICE_ADB_USED=1')
    print('NO_PRODUCTION_CHANGES=1')
    print('NOT_APPROVED_FOR_LIVE_DEPLOYMENT=1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all', action='store_true', help='Stage and run offline tests')
    parser.add_argument('--stage', action='store_true')
    parser.add_argument('--test', action='store_true')
    # --dev-lab is for authoring checks in an isolated sandbox, never for NAS.
    parser.add_argument('--dev-lab', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.dev_lab.resolve() if args.dev_lab else LAB
    need(root == LAB or os.environ.get('QDC_V42_LOCAL_AUTHOR_TEST') == '1',
         'NON_LAB_PATH_REFUSED')
    need(args.all or args.stage or args.test, 'SPECIFY_--all_OR_--stage_OR_--test')
    if args.all or args.stage:
        stage(root)
    if args.all or args.test:
        offline_tests(root)


if __name__ == '__main__':
    main()
