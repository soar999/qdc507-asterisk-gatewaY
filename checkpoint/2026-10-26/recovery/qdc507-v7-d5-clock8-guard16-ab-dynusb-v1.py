#!/usr/bin/env python3
"""Guarded Clock8 Guard16 device-side trial for the already built QDC507 V7 image.

Synology NAS, Python 3.8. All scripts and new files remain under
/volume2/docker/asterisk/qdc507-16k-lab/ on the NAS.

    --check     Read-only verification, no device writes
    --stage     Upload Clock8 Guard16 binary to an UNUSED V7 device /tmp filename only
    --activate  Switch the STOPPED V7 bridge-d5 to Clock8 Guard16 atomically
    --revert    Restore original V7 bridge-d5 (after guarded V7 RESTORE)

This tool NEVER runs the V7 test switch, starts a call, edits firmware, or
restarts/stops the production container, SMS timer, or any service. All mutating
steps require V7 stopped, original smoke state/lock absent, production healthy
and free, SMS timer active, all four device PCMs closed and f_audio disabled.
The only modified device file at --activate/--revert is the experimental
/tmp/qdc507-serial16k-v7/bridge-d5, with a one-use protected backup.

Clock8 Guard16 is UNPROVEN hardware behavior; this only orchestrates a safe A/B trial.
An interrupted or failed switch requires inspection before any further action.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
DEV = '/tmp/qdc507-serial16k-v7'
BASE_SHA = '93d576f3a32c168e9c0c63ddb2d12f4dab929daaf3229a962e552d570f9189c3'
PERIOD_SHA = 'a0f284c968bea4b81eee28dd1521cff561ab3c1e062b553af6eeb1a848ddd54c'
CLOCK_SHA = '7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71'
BASE_BIN = LAB / 'qdc507-16k-pacing-build-v1/output/mavo-pcm-bridge-d5d6-16k-paced-LAB.armv7'
PERIOD_BIN = LAB / 'qdc507-16k-d5-period-build-v1/output/mavo-pcm-bridge-d5d6-16k-period-LAB.armv7'
CLOCK_BIN = LAB / 'qdc507-16k-d5-clock8-guard16-build-v1/output/mavo-pcm-bridge-d5d6-16k-clock8-guard16-LAB.armv7'
SMOKE = LAB / 'qdc507-v7-swap-smoke-v1'
RUNNER = LAB / 'qdc507-serial16k-v7-stage/runtime/device/run.sh'
ADB = '/usr/local/libexec/qdc507/adb'
SERIAL = 'ffffffd3ffffffbdffffffdcffffffab'
ADB_BASE = [ADB, '-H', '127.0.0.1', '-P', '5037', '-s', SERIAL]
PROD = 'qdc507-hardware'
TEST = 'qdc507-serial16k-v7-smoke'
TIMER = 'qdc507-sms-tick.timer'
STAGED = DEV + '/bridge-d5-clock8-guard16'
UPLOADING = DEV + '/bridge-d5-clock8-guard16.upload'
BACKUP = DEV + '/bridge-d5-clock8-guard16-base.keep'
LINKTEMP = DEV + '/bridge-d5.link-clock8-guard16'


def emit(key, value=1):
    print('{}={}'.format(key, value), flush=True)


def need(condition, message):
    if not condition:
        raise RuntimeError(message)


def command(argv, timeout=18, strict=True):
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout,
                       encoding='utf-8', errors='replace')
    output = p.stdout.replace('\r', '').strip()
    err = p.stderr.replace('\r', '').strip()
    if strict and p.returncode:
        raise RuntimeError('COMMAND_FAILED rc={} command={} stderr={} stdout={}'.format(
            p.returncode, ' '.join(argv[:4]), err[-400:], output[-400:]))
    return p.returncode, output, err


def shell(code, timeout=20):
    return command(ADB_BASE + ['shell', code], timeout)[1]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local_integrity():
    for path, expected, label in (
        (BASE_BIN, BASE_SHA, 'BASE'),
        (PERIOD_BIN, PERIOD_SHA, 'PERIOD256'),
        (CLOCK_BIN, CLOCK_SHA, 'GUARD16')):
        need(path.is_file() and not path.is_symlink(), 'NAS_{}_BINARY_MISSING_OR_LINK'.format(label))
        actual = sha256(path)
        need(actual == expected, 'NAS_{}_HASH_MISMATCH expected={} found={}'.format(label, expected, actual))
        emit('NAS_{}_HASH_VERIFIED'.format(label))
    need(RUNNER.is_file() and not RUNNER.is_symlink(), 'STAGED_V7_RUNNER_MISSING')
    need(Path(ADB).is_file(), 'ADB_CLIENT_MISSING')


def inspect_running(name):
    rc, out, err = command(['docker', 'inspect', '-f', '{{.State.Running}}', name], 10, False)
    if rc == 0 and out in ('true', 'false'):
        return out == 'true'
    if rc != 0 and ('No such object' in err or 'No such container' in err):
        return False
    rc2, names, _ = command(['docker', 'ps', '-a', '--format', '{{.Names}}'], 10, False)
    if rc2 == 0 and name not in names.splitlines():
        return False
    raise RuntimeError('DOCKER_CONTAINER_STATE_INCONCLUSIVE: ' + name)


def safe_idle():
    need(os.geteuid() == 0, 'SUDO_REQUIRED')
    need(not inspect_running(TEST), 'V7_RUNNING_REFUSE_DEVICE_CHANGE')
    need(not (SMOKE / 'smoke-state.json').exists() and
         not (SMOKE / '.smoke.lock').exists(),
         'V7_SMOKE_STATE_OR_LOCK_EXISTS_USE_ORIGINAL_RESTORE')
    need(inspect_running(PROD), 'PRODUCTION_NOT_RUNNING')
    detail = command(['docker', 'exec', PROD, 'asterisk', '-rx',
                      'quectel show device state quectel0'], 12)[1]
    for key, value in (('State', 'Free'), ('Voice', 'Yes'), ('SMS', 'Yes'),
                       ('Calls/Channels', '0')):
        need(bool(re.search(r'^\s*{}\s*:\s*{}\s*$'.format(re.escape(key),re.escape(value)),
                            detail, re.M)),
             'PRODUCTION_STATE_NOT_SAFE: '+key)
    channels = command(['docker', 'exec', PROD, 'asterisk', '-rx',
                        'core show channels concise'], 10)[1]
    summary = re.compile(r'^\d+\s+(?:active\s+channels?|active\s+calls?|calls?\s+processed)\.?$',re.I)
    # Ignore only Asterisk's internal persistent text-message channel.
    # All real or unknown channels still block audio operations.
    for sample in range(2):
        unexpected = [
            line for line in channels.splitlines()
            if line.strip()
            and not summary.fullmatch(line.strip())
            and line.split('!', 1)[0].strip() != 'Message/ast_msg_queue'
        ]
        need(not unexpected, 'PRODUCTION_HAS_ACTIVE_CHANNELS')

        counts = command(['docker', 'exec', PROD, 'asterisk', '-rx',
                          'core show channels count'], 10)[1]
        need(re.search(r'^\s*0 active channels\s*$', counts, re.M)
             and re.search(r'^\s*0 active calls\s*$', counts, re.M),
             'PRODUCTION_VOICE_CHANNELS_NOT_IDLE')

        if sample == 0:
            channels = command(['docker', 'exec', PROD, 'asterisk', '-rx',
                                'core show channels concise'], 10)[1]
    need(command(['systemctl', 'is-active', TIMER], 8)[1] == 'active',
         'SMS_TIMER_NOT_ACTIVE')
    emit('PRODUCTION_HEALTHY_IDLE')
    emit('V7_STOPPED')
    emit('SMS_TIMER_ACTIVE')


def parse_kv(output):
    result = {}
    for line in output.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            result[key] = value
    return result


def remote_snapshot():
    return parse_kv(shell(r'''set -eu
D=/tmp/qdc507-serial16k-v7
# An owned preaudio lifecycle must never be inspected as safely stopped.
for p in /tmp/qdc16k-preaudio-manual/state/owner /tmp/qdc16k-preaudio-manual/state/lock; do
 [ ! -e "$p" ] && [ ! -L "$p" ] || exit 91
done
printf 'STATUS='; cat "$D/status" 2>/dev/null || echo MISSING
printf 'CURRENT_HASH='; sha256sum "$D/bridge-d5" | cut -d ' ' -f1
printf 'CURRENT_IS_LINK='; if [ -L "$D/bridge-d5" ]; then echo 1; else echo 0; fi
printf 'CURRENT_TARGET='; if [ -L "$D/bridge-d5" ]; then readlink "$D/bridge-d5"; else echo NONE; fi
printf 'STAGED_HASH='; if [ -e "$D/bridge-d5-clock8-guard16" ]; then sha256sum "$D/bridge-d5-clock8-guard16" | cut -d ' ' -f1; else echo ABSENT; fi
printf 'STAGED_EXEC='; if [ -x "$D/bridge-d5-clock8-guard16" ]; then echo 1; else echo 0; fi
printf 'STAGED_IS_LINK='; if [ -L "$D/bridge-d5-clock8-guard16" ]; then echo 1; else echo 0; fi
printf 'UPLOAD_TEMP='; if [ -e "$D/bridge-d5-clock8-guard16.upload" ] || [ -L "$D/bridge-d5-clock8-guard16.upload" ]; then echo PRESENT; else echo ABSENT; fi
printf 'LINK_TEMP='; if [ -e "$D/bridge-d5.link-clock8-guard16" ] || [ -L "$D/bridge-d5.link-clock8-guard16" ]; then echo PRESENT; else echo ABSENT; fi
printf 'BACKUP_HASH='; if [ -e "$D/bridge-d5-clock8-guard16-base.keep" ]; then sha256sum "$D/bridge-d5-clock8-guard16-base.keep" | cut -d ' ' -f1; else echo ABSENT; fi
printf 'OLD_AB_BACKUP='; if [ -e "$D/bridge-d5-paced.keep" ] || [ -L "$D/bridge-d5-paced.keep" ]; then echo PRESENT; else echo ABSENT; fi
printf 'V1_AB_BACKUP='; if [ -e "$D/bridge-d5-clock8-base.keep" ] || [ -L "$D/bridge-d5-clock8-base.keep" ]; then echo PRESENT; else echo ABSENT; fi
printf 'RUNNER_HASH='; sha256sum "$D/run.sh" | cut -d ' ' -f1
printf 'AUDIO_ENABLE='; cat /sys/class/android_usb/f_audio/audio_enable
for n in 4p 4c 5p 6c; do printf 'PCM_%s=' "$n"; cat /proc/asound/card0/pcm${n}/sub0/hw_params; done'''))


def verified_snapshot():
    state = remote_snapshot()
    for key in ('STATUS', 'CURRENT_HASH', 'CURRENT_IS_LINK', 'CURRENT_TARGET',
                'STAGED_HASH', 'STAGED_EXEC', 'STAGED_IS_LINK', 'UPLOAD_TEMP', 'LINK_TEMP',
                'BACKUP_HASH', 'OLD_AB_BACKUP', 'V1_AB_BACKUP', 'RUNNER_HASH', 'AUDIO_ENABLE',
                'PCM_4p', 'PCM_4c', 'PCM_5p', 'PCM_6c'):
        need(key in state, 'DEVICE_SNAPSHOT_MISSING_' + key)
    need(state['STATUS'] == 'DONE d4=0 d5=0' or
         (state['STATUS'] == 'MISSING' and is_runtime_active(state)),
         'DEVICE_V7_SUPERVISOR_NOT_CLEAN')
    need(state['RUNNER_HASH'] in (sha256(RUNNER),
         '38070547ef168629f32be3fe349b2a517c2fff6177cfe2b6cd15e22c8c6fa778'),
         'DEVICE_V7_RUNNER_HASH_CHANGED')
    need(state['AUDIO_ENABLE'] == '0', 'DEVICE_AUDIO_ENABLE_NOT_ZERO')
    for key in ('PCM_4p', 'PCM_4c', 'PCM_5p', 'PCM_6c'):
        need(state[key] == 'closed', 'DEVICE_PCM_NOT_CLOSED_' + key)
    for key in ('CURRENT_HASH','CURRENT_IS_LINK','CURRENT_TARGET','STAGED_HASH',
                'STAGED_EXEC','STAGED_IS_LINK','BACKUP_HASH','OLD_AB_BACKUP','V1_AB_BACKUP'):
        emit('DEVICE_'+key, state[key])
    return state


def is_baseline(s):
    return (s['CURRENT_HASH'] == BASE_SHA and s['CURRENT_IS_LINK'] == '0'
            and s['CURRENT_TARGET'] == 'NONE' and s['BACKUP_HASH'] == 'ABSENT'
            and s['OLD_AB_BACKUP'] == 'ABSENT' and s['V1_AB_BACKUP'] == 'ABSENT')


def is_active(s):
    return (s['CURRENT_HASH'] == CLOCK_SHA and s['CURRENT_IS_LINK'] == '1'
            and s['CURRENT_TARGET'] == STAGED and s['BACKUP_HASH'] == BASE_SHA
            and s['STAGED_HASH'] == CLOCK_SHA and s['STAGED_EXEC'] == '1'
            and s['STAGED_IS_LINK'] == '0'
            and s['OLD_AB_BACKUP'] == 'ABSENT' and s['V1_AB_BACKUP'] == 'ABSENT')


def is_runtime_active(s):
    # Accepted runtime can carry the pinned Guard16 as a regular binary.
    # This is NOT an A/B activation and has no invented backup history.
    return (s['CURRENT_HASH'] == CLOCK_SHA and s['CURRENT_IS_LINK'] == '0'
            and s['CURRENT_TARGET'] == 'NONE' and s['BACKUP_HASH'] == 'ABSENT'
            and s['OLD_AB_BACKUP'] == 'ABSENT' and s['V1_AB_BACKUP'] == 'ABSENT'
            and s['RUNNER_HASH'] == '38070547ef168629f32be3fe349b2a517c2fff6177cfe2b6cd15e22c8c6fa778')


def ensure_no_temps(s):
    need(s['UPLOAD_TEMP'] == 'ABSENT', 'GUARD16_UPLOAD_TEMP_LEFTOVER_HALT')
    need(s['LINK_TEMP'] == 'ABSENT', 'GUARD16_LINK_TEMP_LEFTOVER_HALT')


def check():
    safe_idle()
    state = verified_snapshot()
    if is_baseline(state):
        emit('GUARD16_DEVICE_STATE', 'BASELINE')
    elif is_active(state):
        emit('GUARD16_DEVICE_STATE', 'ACTIVE_READY_TO_REVERT')
    elif is_runtime_active(state):
        emit('GUARD16_DEVICE_STATE', 'ACCEPTED_RUNTIME_PINNED')
    elif state['CURRENT_HASH'] == PERIOD_SHA:
        raise RuntimeError('PERIOD256_ACTIVE_REVERT_PERIOD_AB_FIRST')
    elif state['CURRENT_HASH'] == '55f7221fe5de7264537014c13218b421393ab689c5e2366fa0b478580a34cfb6':
        raise RuntimeError('CLOCK8_V1_ACTIVE_RESTORE_V7_THEN_V1_AB_REVERT_FIRST')
    else:
        raise RuntimeError('UNRECOGNIZED_DEVICE_SWITCH_STATE_STOP_AND_INSPECT')
    if state['STAGED_HASH'] == 'ABSENT':
        emit('GUARD16_STAGE_STATE', 'NOT_STAGED')
    elif (state['STAGED_HASH'] == CLOCK_SHA and state['STAGED_EXEC'] == '1'
          and state['STAGED_IS_LINK'] == '0'):
        emit('GUARD16_STAGE_STATE', 'VERIFIED')
    else:
        raise RuntimeError('UNRECOGNIZED_GUARD16_STAGED_HASH_OR_PERMISSIONS')
    ensure_no_temps(state)
    emit('READ_ONLY_CHECK_PASSED')


def prepare_preaudio():
    import importlib.util
    root = Path(__file__).resolve().parent / 'qdc507-preaudio-runtime'
    spec = importlib.util.spec_from_file_location('qdc_preaudio_prepare', str(root / 'prepare.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.prepare(ADB, root / 'device.sh', active=False)

def stage():
    safe_idle()
    state = verified_snapshot()
    need(is_baseline(state), 'GUARD16_STAGE_REQUIRES_ORIGINAL_BASELINE_RUN_PERIOD_AB_REVERT_IF_NEEDED')
    ensure_no_temps(state)
    if (state['STAGED_HASH'] == CLOCK_SHA and state['STAGED_EXEC'] == '1'
            and state['STAGED_IS_LINK'] == '0'):
        prepare_preaudio()
        emit('GUARD16_ALREADY_STAGED_AND_VERIFIED')
        return
    need(state['STAGED_HASH'] == 'ABSENT' and state['STAGED_IS_LINK'] == '0',
         'GUARD16_DEVICE_STAGE_EXISTS_WITH_UNKNOWN_HASH_OR_LINK')
    # Guard against an unrecognised symlink at the target.
    out = shell('''set -eu
D=/tmp/qdc507-serial16k-v7
[ ! -e "$D/bridge-d5-clock8-guard16" ] && [ ! -L "$D/bridge-d5-clock8-guard16" ]
[ ! -e "$D/bridge-d5-clock8-guard16.upload" ] && [ ! -L "$D/bridge-d5-clock8-guard16.upload" ]
[ "$(sha256sum "$D/bridge-d5" | cut -d ' ' -f1)" = "{}" ]
[ ! -L "$D/bridge-d5" ]
[ "$(cat "$D/status")" = 'DONE d4=0 d5=0' ]
echo STAGING_GUARD_OK=1'''.format(BASE_SHA))
    need('STAGING_GUARD_OK=1' in out, 'STAGING_GUARD_NOT_CONFIRMED')
    rc, output, err = command(ADB_BASE+['push', str(CLOCK_BIN), UPLOADING], 80)
    emit('GUARD16_ADB_PUSH_COMPLETED', rc == 0)
    out = shell('''set -eu
D=/tmp/qdc507-serial16k-v7
[ -f "$D/bridge-d5-clock8-guard16.upload" ] && [ ! -L "$D/bridge-d5-clock8-guard16.upload" ]
[ "$(sha256sum "$D/bridge-d5-clock8-guard16.upload" | cut -d ' ' -f1)" = "{}" ]
[ ! -e "$D/bridge-d5-clock8-guard16" ] && [ ! -L "$D/bridge-d5-clock8-guard16" ]
chmod 700 "$D/bridge-d5-clock8-guard16.upload"
mv "$D/bridge-d5-clock8-guard16.upload" "$D/bridge-d5-clock8-guard16"
printf 'STAGED_HASH='; sha256sum "$D/bridge-d5-clock8-guard16" | cut -d ' ' -f1
'''.format(CLOCK_SHA))
    need(parse_kv(out).get('STAGED_HASH') == CLOCK_SHA,
         'GUARD16_REMOTE_STAGE_HASH_POSTCHECK_FAILED')
    prepare_preaudio()
    emit('GUARD16_STAGED_ON_DEVICE_UNUSED')
    emit('NO_AUDIO_SWITCH_OR_SERVICE_CHANGE')


def activate():
    safe_idle()
    state = verified_snapshot()
    need(is_baseline(state), 'GUARD16_ACTIVATE_REQUIRES_ORIGINAL_BASELINE')
    ensure_no_temps(state)
    need(state['STAGED_HASH'] == CLOCK_SHA and state['STAGED_EXEC'] == '1'
         and state['STAGED_IS_LINK'] == '0',
         'GUARD16_BINARY_NOT_STAGED_OR_UNVERIFIED')
    out = shell('''set -eu
D=/tmp/qdc507-serial16k-v7
[ "$(sha256sum "$D/bridge-d5" | cut -d ' ' -f1)" = "{}" ]
[ ! -L "$D/bridge-d5" ]
[ "$(sha256sum "$D/bridge-d5-clock8-guard16" | cut -d ' ' -f1)" = "{}" ]
[ -x "$D/bridge-d5-clock8-guard16" ] && [ ! -L "$D/bridge-d5-clock8-guard16" ]
[ ! -e "$D/bridge-d5-clock8-guard16-base.keep" ] && [ ! -L "$D/bridge-d5-clock8-guard16-base.keep" ]
[ ! -e "$D/bridge-d5-paced.keep" ] && [ ! -L "$D/bridge-d5-paced.keep" ]
[ ! -e "$D/bridge-d5-clock8-base.keep" ] && [ ! -L "$D/bridge-d5-clock8-base.keep" ]
[ ! -e "$D/bridge-d5.link-clock8-guard16" ] && [ ! -L "$D/bridge-d5.link-clock8-guard16" ]
[ "$(cat "$D/status")" = 'DONE d4=0 d5=0' ]
cp -p "$D/bridge-d5" "$D/bridge-d5-clock8-guard16-base.keep"
[ "$(sha256sum "$D/bridge-d5-clock8-guard16-base.keep" | cut -d ' ' -f1)" = "{}" ]
ln -s "$D/bridge-d5-clock8-guard16" "$D/bridge-d5.link-clock8-guard16"
mv -f "$D/bridge-d5.link-clock8-guard16" "$D/bridge-d5"
printf 'ACTUAL_HASH='; sha256sum "$D/bridge-d5" | cut -d ' ' -f1
printf 'ACTUAL_TARGET='; readlink "$D/bridge-d5"
'''.format(BASE_SHA, CLOCK_SHA, BASE_SHA))
    d = parse_kv(out)
    need(d.get('ACTUAL_HASH') == CLOCK_SHA and d.get('ACTUAL_TARGET') == STAGED,
         'GUARD16_ACTIVATED_BUT_POSTCHECK_FAILED_USE_REVERT_AFTER_INSPECTING')
    emit('GUARD16_ACTIVE_FOR_NEXT_V7_TEST')
    emit('GUARD16_BACKUP_SAVED', BACKUP)
    emit('PRODUCTION_CONTAINER_UNCHANGED')


def revert():
    safe_idle()
    state = verified_snapshot()
    ensure_no_temps(state)
    if is_baseline(state):
        emit('GUARD16_ALREADY_ORIGINAL_BASELINE')
        return
    need(is_active(state), 'GUARD16_REMOTE_STATE_NOT_EXPECTED_ACTIVE_DO_NOT_FORCE_RESTORE')
    out = shell('''set -eu
D=/tmp/qdc507-serial16k-v7
[ -L "$D/bridge-d5" ]
[ "$(readlink "$D/bridge-d5")" = "$D/bridge-d5-clock8-guard16" ]
[ "$(sha256sum "$D/bridge-d5" | cut -d ' ' -f1)" = "{}" ]
[ "$(sha256sum "$D/bridge-d5-clock8-guard16-base.keep" | cut -d ' ' -f1)" = "{}" ]
[ "$(cat "$D/status")" = 'DONE d4=0 d5=0' ]
mv -f "$D/bridge-d5-clock8-guard16-base.keep" "$D/bridge-d5"
printf 'ACTUAL_HASH='; sha256sum "$D/bridge-d5" | cut -d ' ' -f1
printf 'ACTUAL_IS_LINK='; if [ -L "$D/bridge-d5" ]; then echo 1; else echo 0; fi
'''.format(CLOCK_SHA, BASE_SHA))
    d = parse_kv(out)
    need(d.get('ACTUAL_HASH') == BASE_SHA and d.get('ACTUAL_IS_LINK') == '0',
         'GUARD16_REVERT_POSTCHECK_FAILED')
    emit('GUARD16_ORIGINAL_BASELINE_RESTORED')
    emit('GUARD16_STAGED_BINARY_RETAINED_UNUSED')
    emit('PRODUCTION_CONTAINER_UNCHANGED')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--stage', action='store_true')
    group.add_argument('--activate', action='store_true')
    group.add_argument('--revert', action='store_true')
    args = p.parse_args()
    local_integrity()
    if args.check:
        check()
    elif args.stage:
        stage()
    elif args.activate:
        activate()
    else:
        revert()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit('GUARD16_AB_STOPPED', repr(exc))
        emit('DO_NOT_MANUALLY_DELETE_EXPERIMENT_BACKUPS_OR_STATE')
        sys.exit(2)
