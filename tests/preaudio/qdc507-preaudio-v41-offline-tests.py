#!/usr/bin/env python3
"""Offline-only smoke/regression tests for a staged QDC507 Pre-audio v4.1 candidate.

No Docker, Asterisk, device adb, device shell, config edits, or production writes.
All scratch files live inside the LAB root in a temporary directory.
"""
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
BASELINE_SHA = '2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690'


def require(test, label):
    if not test:
        raise AssertionError(label)
    print('PASS=' + label, flush=True)


def load(candidate, scratch):
    os.environ['QDC_LOCAL_STATE'] = str(scratch)
    spec = importlib.util.spec_from_file_location('preaudio_v41_offline', str(candidate))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run():
    candidate = LAB / 'qdc507-preaudio-v41-candidate.py'
    original = LAB / 'preaudio-original.py'
    if not LAB.is_dir() or LAB.is_symlink():
        raise AssertionError('INVALID_LAB_ROOT')
    for p in [candidate, original]:
        if not p.is_file() or p.is_symlink() or p.resolve().parent != LAB:
            raise AssertionError('SOURCE_INVALID: ' + p.name)
    require(hashlib.sha256(original.read_bytes()).hexdigest() == BASELINE_SHA,
            'BASELINE_SHA256_OK')
    src = candidate.read_text('utf-8')
    ast.parse(src)
    require('fcntl.flock' in src and 'ADB_GATE_BUSY' in src and 'next_query = time.monotonic() + 0.5' in src,
            'GATE_AND_QUERY_THROTTLE_PRESENT_STATIC')
    require('def cancel(t, c):' in src and 'cancel.attempted' in src,
            'CANCEL_SINGLE_ATTEMPT_GUARD_PRESENT_STATIC')

    with tempfile.TemporaryDirectory(prefix='qdc-preaudio-v41-offline-', dir=str(LAB)) as temporary:
        scratch = Path(temporary)
        mod = load(candidate, scratch)
        # Per-call early fault must survive any cleanup failure.
        c = scratch / 'first-error'; c.mkdir()
        mod.report_error(c, 'fault', 'original bridge or READY problem')
        mod.report_error(c, 'cleanup.error', 'ADB transport: error: closed')
        require((c / 'first.error').read_text().startswith('fault: original bridge'),
                'FIRST_ERROR_PRESERVED')
        require('error: closed' in (c / 'cleanup.error').read_text(),
                'CLEANUP_ERROR_SEPARATE')

        # A valid receipt must be remembered; a duplicate cancel MUST NOT repeat RPC.
        c = scratch / 'cancel-success'; c.mkdir()
        calls = []
        def good_rpc(action, token, timeout=6):
            calls.append((action, token))
            return 'CLEAN ' + token
        mod.rpc = good_rpc
        require(mod.cancel('success', c) is True and mod.cancel('success', c) is True,
                'CANCEL_SUCCESS_IDEMPOTENT')
        require(calls == [('cancel', 'success')], 'CANCEL_SUCCESS_RPC_ONCE')

        # A failed/unknown remote outcome must not cause an automatic retry.
        c = scratch / 'cancel-failure'; c.mkdir()
        mod.report_error(c, 'fault', 'READY failed first')
        calls = []
        def bad_rpc(action, token, timeout=6):
            calls.append((action, token))
            raise RuntimeError('cancel: rc=1 stderr=error: closed')
        mod.rpc = bad_rpc
        require(mod.cancel('failure', c) is False and mod.cancel('failure', c) is False,
                'CANCEL_FAILURE_NOT_RETRIED')
        require(len(calls) == 1, 'CANCEL_FAILURE_RPC_ONCE')
        require((c / 'first.error').read_text().startswith('fault: READY failed first'),
                'FIRST_ERROR_SURVIVES_CANCEL_FAILURE')

        # Verify two independent callers cannot issue concurrent cancel RPCs.
        c = scratch / 'cancel-race'; c.mkdir()
        calls = []
        lock = threading.Lock()
        def slow_rpc(action, token, timeout=6):
            with lock:
                calls.append((action, token))
            time.sleep(0.2)
            return 'CLEAN ' + token
        mod.rpc = slow_rpc
        results = []
        t1 = threading.Thread(target=lambda: results.append(mod.cancel('race', c)))
        t2 = threading.Thread(target=lambda: results.append(mod.cancel('race', c)))
        t1.start(); t2.start(); t1.join(3); t2.join(3)
        require(not t1.is_alive() and not t2.is_alive() and results == [True, True],
                'CANCEL_CONCURRENT_JOIN')
        require(len(calls) == 1, 'CANCEL_CONCURRENT_RPC_ONCE')

        # Exercise real rpc() parser with a LOCAL fake ADB, not device ADB.
        fake = scratch / 'fake-adb'
        fake.write_text('#!/bin/sh\necho "error: closed" >&2\nexit 1\n')
        fake.chmod(0o700)
        os.environ.update(QDC_RUNTIME_ENABLED='exclusive-short-test',
                          QDC_DEVICE_SCRIPT='/offline/device.sh',
                          QDC_DEVICE_ROOT='/offline/state',
                          QDC_BIN_ROOT='/offline/bin',
                          QDC_ADB=str(fake),
                          QDC_SERIAL='OFFLINE-FAKE')
        # Reload to recover real rpc function after monkeypatching.
        mod = load(candidate, scratch)
        try:
            mod.rpc('query', 'fake-token', timeout=1)
        except RuntimeError as exc:
            detail = str(exc)
            require('stderr=' in detail and 'error: closed' in detail,
                    'ADB_STDERR_RETAINED')
        else:
            raise AssertionError('ADB_FAILURE_NOT_PROPAGATED')

    print('OFFLINE_TESTS_PASSED=1')
    print('NO_DEVICE_ADB_USED=1')
    print('NO_PRODUCTION_CHANGES=1')
    print('NOT_APPROVED_FOR_LIVE_DEPLOYMENT=1')


if __name__ == '__main__':
    run()
