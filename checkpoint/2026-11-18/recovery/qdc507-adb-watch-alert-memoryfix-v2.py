#!/usr/bin/env python3
"""QDC507 ADB watcher v2.3 (memoryfix v2 runtime pin): original SMS alert + one guarded recovery per outage.

Compatible with old --watch / --test-alert scheduler.
Requires qdc507-adb-recover-once-memoryfix-v2.py --enroll and --check success first.
Do not run alongside v2.1/v2.2; all share the advisory lock and alert state.  Do not run alongside v2.1:
uses the same advisory lock and state file. Reset requires separate guarded helper.
"""
import sys
import os
import time
import json
import fcntl
import secrets
import subprocess
from pathlib import Path
from datetime import datetime

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
QUEUE = Path('/volume2/docker/asterisk/qdc507-current/v2/sms-queue/pending')
STATE = LAB / 'qdc507-adb-alert-state.json'
LOG = LAB / 'qdc507-adb-alert.log'
LOCK = LAB / 'qdc507-adb-alert.lock'
RECOVER = [
    '/usr/bin/python3.8', '-I', '-B',
    str(LAB / 'qdc507-adb-recover-once-memoryfix-v2.py'), '--reset'
]

ADB = [
    'docker', 'exec', 'qdc507-hardware',
    '/usr/local/bin/adb',
    '-H', '127.0.0.1', '-P', '5037',
    '-s', 'QDC507_ADB_ID_REDACTED'
]

def now():
    return datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')

def log(msg):
    line = now() + ' ' + msg
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line, flush=True)

def run(cmd, timeout=8):
    try:
        return subprocess.run(
            cmd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, '', 'TIMEOUT')
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 125, '', type(e).__name__)

def probe():
    command = '''
echo QDC_SHELL_PROBE_OK=1
P=$(pidof adbd)
echo ADBD_PID=$P
grep -E "VmRSS:|Threads:" /proc/$P/status
printf "ADBD_FD="
ls /proc/$P/fd | wc -l
grep -E "MemAvailable:|SUnreclaim:" /proc/meminfo
grep "^processes " /proc/stat
'''
    result = run(ADB + ['shell', command])
    output = (result.stdout + ' ' + result.stderr).replace('\r', '').strip()
    ok = result.returncode == 0 and 'QDC_SHELL_PROBE_OK=1' in output
    return ok, result.returncode, ' '.join(output.split())[:350]

def check_queue():
    if not QUEUE.is_dir():
        raise RuntimeError('SMS_QUEUE_NOT_FOUND')
    timer = run(['systemctl', 'is-active', 'qdc507-sms-tick.timer'], timeout=5)
    if timer.stdout.strip() != 'active':
        raise RuntimeError('SMS_TIMER_NOT_ACTIVE')
    route = run(['docker', 'exec', 'qdc507-hardware',
                 'asterisk', '-rx', 'dialplan show sms-deliver-queued'], timeout=8)
    if route.returncode != 0 or "Context 'sms-deliver-queued'" not in route.stdout:
        raise RuntimeError('SMS_DELIVERY_ROUTE_NOT_FOUND')

def queue_message(body):
    check_queue()
    qid = '%d-%06d' % (int(time.time()), secrets.randbelow(1000000))
    files = [('.gw', 'QDC507'), ('.src', '900001'),
             ('.conv', '7900001'), ('.body', body)]
    for ext, content in files:
        target = QUEUE / (qid + ext)
        temp = QUEUE / ('.' + qid + ext + '.stage')
        with temp.open('x', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(str(temp), 0o600)
        os.chown(str(temp), 1000, 1000)
        os.replace(str(temp), str(target))
    return qid

def save_state(data):
    temp = STATE.with_name(STATE.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(temp), str(STATE))

def load_state():
    if STATE.exists():
        data = json.loads(STATE.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    return {'alarm_active': False, 'ok_streak': 0}

def watch():
    # Same lock as v2.1 to prevent two monitors running in parallel.
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('MONITOR_ALREADY_RUNNING')
        state = load_state()
        log('WATCH_V2_3_AUTO_RECOVERY_STARTED')
        while True:
            ok, rc, info = probe()
            if ok:
                state['ok_streak'] = state.get('ok_streak', 0) + 1
                state['fail_cycles'] = 0
                if state.get('alarm_active') and state['ok_streak'] >= 2:
                    state['alarm_active'] = False
                    log('ADB_RECOVERED')
                    try:
                        qid = queue_message('【NAS内部通知】QDC507 ADB已恢复。时间：%s。' % now())
                        log('RECOVERY_NOTICE_QUEUED_TO_100 qid=' + qid)
                    except Exception as e:
                        log('RECOVERY_NOTICE_QUEUE_FAILED ' + str(e))
                if state['ok_streak'] >= 2:
                    state['reset_attempted'] = False
                log('SHELL_OK ' + info)
            else:
                state['ok_streak'] = 0
                log('SHELL_FAIL_FIRST rc=%s %s' % (rc, info))
                time.sleep(3)
                ok2, rc2, info2 = probe()
                if ok2:
                    log('SHELL_RETRY_OK ' + info2)
                    state['fail_cycles'] = 0
                else:
                    state['fail_cycles'] = state.get('fail_cycles', 0) + 1
                    log('SHELL_FAIL_CONFIRMED cycle=%s rc=%s %s' %
                        (state['fail_cycles'], rc2, info2))
                    if not state.get('alarm_active'):
                        gs = run(ADB + ['get-state'], timeout=5)
                        status = (gs.stdout + ' ' + gs.stderr).strip().replace('\r', ' ')[:80]
                        body = ('【NAS内部告警】QDC507 ADB Shell连续失败。'
                                '时间：%s。ADB状态：%s。请检查电话网关。') % (now(), status)
                        try:
                            qid = queue_message(body)
                            state['alarm_active'] = True
                            state['last_alert'] = now()
                            log('ALERT_QUEUED_TO_100 qid=' + qid)
                        except Exception as e:
                            log('ALERT_QUEUE_FAILED ' + str(e))
                    # Two *separate* 60-second cycles, each internally reconfirmed.
                    # Persist the attempted flag before any potentially disruptive action.
                    if state['fail_cycles'] >= 2 and not state.get('reset_attempted'):
                        state['reset_attempted'] = True
                        state['last_reset_attempt'] = now()
                        save_state(state)
                        log('GUARDED_RESET_INVOCATION_ONCE=1')
                        p = run(RECOVER, timeout=180)
                        summary = ' '.join((p.stdout + ' ' + p.stderr).split())[-900:]
                        log('GUARDED_RESET_EXIT=%s %s' % (p.returncode, summary))
            save_state(state)
            time.sleep(60)

def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: --test-alert | --check-recovery | --watch')
    if sys.argv[1] == '--test-alert':
        print('TEST_ALERT_QUEUED=' + queue_message(
            '【NAS监控测试】QDC507故障告警通道测试。这是内部模拟通知，不代表设备发生故障。时间：' + now()))
    elif sys.argv[1] == '--check-recovery':
        p = run(RECOVER[:-1] + ['--check'], timeout=45)
        print(p.stdout, end='')
        if p.stderr:
            print(p.stderr, file=sys.stderr, end='')
        sys.exit(p.returncode)
    elif sys.argv[1] == '--watch':
        watch()
    else:
        raise SystemExit('UNKNOWN_COMMAND')

if __name__ == '__main__':
    main()
