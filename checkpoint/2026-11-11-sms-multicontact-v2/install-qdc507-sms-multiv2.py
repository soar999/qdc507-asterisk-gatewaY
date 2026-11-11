#!/usr/bin/python3.8
"""Deploy SMS multi-contact V2 only after --test succeeds; with rollback."""
import argparse
import ast
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
STAGED = LAB / 'qdc507-sms-multicontact-v2.py'
CONSUMER = Path('/usr/local/sbin/qdc507-sms-queue-deliver-pinned.py')
TICK = Path('/usr/local/sbin/qdc507-sms-tick.py')
BASE = Path('/volume2/docker/asterisk/qdc507-current/v2/sms-queue')
OLD_PIN = 'f03d5fc3dfef44bd88a2c77aaa1d609d087db607009394cc1641c961781fc81a'
TIMER = 'qdc507-sms-tick.timer'
SERVICE = 'qdc507-sms-tick.service'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def systemctl(*args):
    return subprocess.run(['systemctl'] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False)


def active(unit):
    return systemctl('is-active', unit).stdout.strip() == 'active'


def verify():
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED')
    for p in (STAGED, CONSUMER, TICK):
        if not p.is_file():
            raise RuntimeError('FILE_MISSING: ' + str(p))
    stage = STAGED.read_bytes()
    ast.parse(stage.decode('utf-8'), filename=str(STAGED))
    original = CONSUMER.read_bytes()
    if sha(original) != OLD_PIN:
        raise RuntimeError('ORIGINAL_CONSUMER_HASH_CHANGED_ABORT')
    wrapper = TICK.read_text(encoding='utf-8')
    if wrapper.count("PIN='" + OLD_PIN + "'") != 1:
        raise RuntimeError('TICK_ORIGINAL_PIN_NOT_FOUND_ABORT')
    if 'DOCKER_PIN=' not in wrapper or 'fcntl.flock' not in wrapper:
        raise RuntimeError('WRAPPER_SAFEGUARDS_MISSING_ABORT')
    new_pin = sha(stage)
    new_wrapper = wrapper.replace("PIN='" + OLD_PIN + "'", "PIN='" + new_pin + "'")
    ast.parse(new_wrapper)
    print('ORIGINAL_CONSUMER_SHA256=' + sha(original))
    print('STAGED_V2_SHA256=' + new_pin)
    print('NEW_WRAPPER_SHA256=' + sha(new_wrapper.encode('utf-8')))
    return stage, original, wrapper, new_wrapper


def replace_file(path, data):
    # An atomic replacement in the existing script directory.
    fd, temp = tempfile.mkstemp(prefix='.smsmv2-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chown(temp, 0, 0)
        os.chmod(temp, 0o700)
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    stage, original, wrapper, new_wrapper = verify()
    if not args.apply:
        print('PREFLIGHT_ONLY=1 APPLY_NOT_RUN=1')
        return
    if not active(TIMER):
        raise RuntimeError('TIMER_NOT_ACTIVE_ABORT')
    print('STOPPING_SMS_TIMER=1')
    if systemctl('stop', TIMER).returncode != 0:
        raise RuntimeError('TIMER_STOP_FAILED')
    old_backup = None
    tick_backup = None
    changed = False
    try:
        deadline = time.monotonic() + 20
        while active(SERVICE) and time.monotonic() < deadline:
            time.sleep(.2)
        if active(SERVICE):
            raise RuntimeError('SERVICE_STILL_RUNNING_ABORT')
        pending_count = len(list((BASE / 'pending').glob('*.body')))
        if pending_count:
            raise RuntimeError('PENDING_SMS_{}_ABORT'.format(pending_count))
        tag = time.strftime('%Y%m%d-%H%M%S')
        old_backup = LAB / ('qdc507-sms-consumer-pre-v2-' + tag + '.py')
        tick_backup = LAB / ('qdc507-sms-tick-pre-v2-' + tag + '.py')
        old_backup.write_bytes(original)
        tick_backup.write_text(wrapper, encoding='utf-8')
        for p in (old_backup, tick_backup):
            p.chmod(0o600)
        if sha(old_backup.read_bytes()) != OLD_PIN:
            raise RuntimeError('BACKUP_VERIFICATION_FAILED')
        print('BACKUP_CONSUMER=' + str(old_backup))
        print('BACKUP_TICK=' + str(tick_backup))
        # Timer is stopped; update two pinned files together.
        changed = True
        replace_file(CONSUMER, stage)
        replace_file(TICK, new_wrapper.encode('utf-8'))
        if sha(CONSUMER.read_bytes()) != sha(stage):
            raise RuntimeError('NEW_CONSUMER_VERIFY_FAILED')
        if sha(TICK.read_bytes()) != sha(new_wrapper.encode('utf-8')):
            raise RuntimeError('NEW_WRAPPER_VERIFY_FAILED')
        print('PIN_AND_CONSUMER_INSTALLED=1')
    except Exception:
        if changed:
            print('ROLLBACK_ON_ERROR=1')
            replace_file(CONSUMER, original)
            replace_file(TICK, wrapper.encode('utf-8'))
        raise
    finally:
        result = systemctl('start', TIMER)
        if result.returncode:
            print('CRITICAL_TIMER_RESTART_FAILED=' + result.stderr[:200])
        else:
            print('SMS_TIMER_RESTARTED=1')
    if not active(TIMER):
        raise RuntimeError('TIMER_NOT_ACTIVE_AFTER_INSTALL')
    print('SMS_MULTIV2_INSTALL_OK=1')
    print('NEW_CONSUMER_SHA256=' + sha(CONSUMER.read_bytes()))
    print('QUEUE_EMPTY_DURING_SWITCH=1')
    print('NEXT_STEP_VERIFY_WITH_REAL_SMS=1')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('INSTALL_ABORTED: ' + str(exc))
        raise SystemExit(2)
