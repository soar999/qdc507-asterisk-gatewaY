#!/usr/bin/env python3
"""QDC507 watchdog v2.4b: build a separate, reviewed candidate from v2.3.

--check: no file writes; --stage: creates *only* a new candidate in the LAB root.
Neither action invokes ADB, Docker, USB reset, or restarts any process.
"""
from pathlib import Path
import ast
import json
import math
import re
import stat
import sys
import time

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
SRC = LAB / 'qdc507-adb-watch-alert-memoryfix-v2.py'
DST = LAB / 'qdc507-adb-watch-alert-memoryfix-v24.py'

HELPERS = '''\ndef qdc_retry_due(state, ts=None):
    """A failed cooldown deferral is eligible exactly after its safe deadline."""
    import math
    import time
    if ts is None:
        ts = time.time()
    if not state.get('reset_attempted'):
        return True
    deadline = state.get('next_reset_retry_epoch')
    return (type(deadline) in (int, float) and
            math.isfinite(deadline) and deadline > 0 and ts >= deadline)


def qdc_cooldown_deadline():
    """Read helper's existing reset journal; never change or bypass it."""
    from pathlib import Path
    import json
    import math
    import time
    journal = Path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-adb-usbreset-v1.json')
    now_epoch = time.time()
    try:
        with journal.open('r', encoding='utf-8') as f:
            previous = float(json.load(f)['epoch'])
        if not math.isfinite(previous) or previous <= 0:
            raise ValueError('BAD_EPOCH')
        deadline = previous + 1800 + 5  # existing 30-min guard, plus margin
        if now_epoch < deadline <= now_epoch + 1805:
            return deadline
    except (OSError, ValueError, TypeError, KeyError):
        pass
    # Malformed or ambiguous journal? Fail closed with a fresh 30-min delay.
    return now_epoch + 1805

'''

REPLACE = '''                    if state['fail_cycles'] >= 2 and qdc_retry_due(state):
                        retrying_cooldown = bool(state.get('next_reset_retry_epoch'))
                        # Save the one-shot flag BEFORE invoking the protected helper.
                        state['reset_attempted'] = True
                        state.pop('reset_attempte', None)
                        state.pop('next_reset_retry_epoch', None)
                        state['last_reset_attempt'] = now()
                        save_state(state)
                        log('GUARDED_RESET_INVOCATION_ONCE=1 cooldown_retry=%s' %
                            int(retrying_cooldown))
                        p = run(RECOVER, timeout=180)
                        result = p.stdout + ' ' + p.stderr
                        summary = ' '.join(result.split())[-900:]
                        log('GUARDED_RESET_EXIT=%s %s' % (p.returncode, summary))
                        if (p.returncode != 0 and
                                'RESET_COOLDOWN_ACTIVE_NO_RETRY' in result):
                            state['next_reset_retry_epoch'] = qdc_cooldown_deadline()
                            log('COOLDOWN_RETRY_SCHEDULED_AT=%s' %
                                time.strftime('%Y-%m-%d %H:%M:%S %z',
                                              time.localtime(state['next_reset_retry_epoch'])))
                            save_state(state)
                        # Any other result remains one-shot; no reset storm.
'''

OK_OLD = """                if state['ok_streak'] >= 2:
                    state['reset_attempted'] = False"""
OK_NEW = """                if state['ok_streak'] >= 2:
                    state['reset_attempted'] = False
                    state.pop('reset_attempte', None)
                    state.pop('next_reset_retry_epoch', None)"""


def replace_legacy(src):
    if "WATCH_V2_3_AUTO_RECOVERY_STARTED" not in src:
        raise ValueError('LEGACY_VERSION_MARKER_MISSING')
    if 'def qdc_retry_due(' in src or 'WATCH_V2_4_AUTO_RECOVERY_STARTED' in src:
        raise ValueError('SOURCE_ALREADY_PATCHED')
    tree = ast.parse(src)
    watch_nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'watch']
    if len(watch_nodes) != 1:
        raise ValueError('WATCH_FUNCTION_MISMATCH')
    watch = watch_nodes[0]
    lines = src.splitlines(keepends=True)
    hits = [n for n in ast.walk(watch) if isinstance(n, ast.If) and
            lines[n.lineno-1].strip() == "if state['fail_cycles'] >= 2 and not state.get('reset_attempted'):" ]
    if len(hits) != 1:
        raise ValueError('LEGACY_RESET_CONDITION_MISMATCH')
    n = hits[0]
    if n.orelse:
        raise ValueError('UNEXPECTED_RESET_ELSE')
    # Validate the original expected actions, without requiring a typo.
    original = ''.join(lines[n.lineno-1:n.end_lineno])
    validations = (
        r"state\['reset_attempte(?:d)?'\]\s*=\s*True",
        r"state\['last_reset_attempt'\]\s*=\s*now\(\)",
        r"save_state\(state\)",
        r"log\('GUARDED_RESET_INVOCATION_ONCE=1'\)",
        r"p\s*=\s*run\(RECOVER,\s*timeout=180\)",
        r"log\('GUARDED_RESET_EXIT=%s %s'\s*%\s*\(p.returncode,\s*summary\)\)",
    )
    if any(re.search(p, original) is None for p in validations) or len(n.body) != 7:
        raise ValueError('LEGACY_RESET_ACTIONS_MISMATCH')
    if src.count(OK_OLD) != 1:
        raise ValueError('LEGACY_OK_STREAK_BLOCK_MISMATCH')
    if src.count('def watch():') != 1:
        raise ValueError('LEGACY_WATCH_HEADER_MISMATCH')
    if src.count("log('WATCH_V2_3_AUTO_RECOVERY_STARTED')") != 1:
        raise ValueError('LEGACY_MARKER_COUNT_MISMATCH')
    # AST line ranges ensure the entire legacy if block, and nothing else, is replaced.
    lines[n.lineno-1:n.end_lineno] = [REPLACE]
    patched = ''.join(lines)
    patched = patched.replace(OK_OLD, OK_NEW, 1)
    patched = patched.replace('def watch():', HELPERS + 'def watch():', 1)
    patched = patched.replace("log('WATCH_V2_3_AUTO_RECOVERY_STARTED')",
                              "log('WATCH_V2_4_AUTO_RECOVERY_STARTED')", 1)
    compile(patched, str(DST), 'exec')
    return patched


def test_helpers(candidate):
    t = ast.parse(candidate)
    h = {n.name:n for n in t.body if isinstance(n,ast.FunctionDef) and n.name in ('qdc_retry_due', 'qdc_cooldown_deadline')}
    if len(h)!=2:
        raise ValueError('HELPER_FUNCTIONS_MISSING')
    ns = {'time':time, 'math':math, 'Path':Path, 'json':json}
    exec(compile(ast.Module(body=[h['qdc_retry_due']],type_ignores=[]), '<offline>', 'exec'), ns)
    due=ns['qdc_retry_due']
    assert due({'reset_attempted':False}, 100)
    assert not due({'reset_attempted':True}, 100)
    assert not due({'reset_attempted':True,'next_reset_retry_epoch':180}, 179)
    assert due({'reset_attempted':True,'next_reset_retry_epoch':180}, 180)
    assert not due({'reset_attempted':True,'next_reset_retry_epoch':'180'}, 181)
    assert not due({'reset_attempted':True,'next_reset_retry_epoch':float('nan')}, 181)
    print('V24B_OFFLINE_STATE_TEST_OK=1')


def main():
    if sys.argv[1:] not in (['--check'], ['--stage']):
        raise SystemExit('Usage: qdc507-watchdog-v24b-stage.py --check|--stage')
    if SRC.is_symlink() or not SRC.is_file():
        raise SystemExit('ABORT_LEGACY_SOURCE_NOT_REGULAR=1')
    source = SRC.read_text(encoding='utf-8')
    try:
        candidate=replace_legacy(source)
        test_helpers(candidate)
    except Exception as e:
        raise SystemExit('ABORT_V24B_PATCH_PRECHECK: '+str(e))
    print('V24B_SYNTAX_OK=1')
    print('ORIGINAL_SOURCE_UNCHANGED=1')
    if sys.argv[1] == '--check':
        print('V24B_PATCH_CAN_BE_STAGED=1')
        return
    if DST.exists():
        if not DST.is_symlink() and DST.read_text(encoding='utf-8') == candidate:
            print('V24B_STAGE_ALREADY_MATCHES=1')
            return
        raise SystemExit('ABORT_V24_TARGET_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT')
    with DST.open('x',encoding='utf-8') as f:
        f.write(candidate)
        f.flush()
    DST.chmod(stat.S_IMODE(SRC.stat().st_mode))
    print('V24B_STAGED='+str(DST))
    print('V24B_NOT_ACTIVATED=1')

if __name__ == '__main__':
    main()
