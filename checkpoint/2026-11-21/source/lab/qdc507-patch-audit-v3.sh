#!/bin/sh
# QDC507 production-only audit, 2026-11-20
# Retains the historical audit entry path; no recovery actions.
set -eu

LAB=/volume2/docker/asterisk/qdc507-16k-lab
AUDIT="$LAB/qdc507-memoryfix-v22-audit.py"
EXPECTED="3eae922542c792ec5a9080c5d9a6aa96a96da868ed1bec6a5260ca85715b2469"

echo "PATCH_AUDIT_VERSION=20261120_CURRENT_PRODUCTION"

MODE="${1:---check}"
case "$MODE" in
    --check|--deep) ;;
    *) echo "USE_--check_OR_--deep" >&2; exit 2 ;;
esac

ACTUAL=$(sha256sum "$AUDIT" | awk '{print $1}')
if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "CURRENT_AUDIT_HASH_MISMATCH=1"
    exit 2
fi

/usr/bin/python3.8 -I -B "$AUDIT" "$MODE"

echo "===== Current V43 voice components ====="

verify_component() {
    NAME="$1"
    FILE="$2"
    EXPECTED_SHA="$3"

    ACTUAL_SHA=$(docker exec qdc507-hardware sha256sum         "/opt/qdc16k-preaudio-manual/$FILE" | awk '{print $1}')

    if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
        echo "$NAME=HASH_MISMATCH"
        exit 2
    fi
    echo "$NAME=PASS"
}

verify_component V43_INBOUND_CONTROLLER   preaudio-inbound-v43.py   13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf

verify_component V43_INBOUND_AGI   agi-wrapper-inbound-v43   472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98

verify_component V43_DEVICE_RESTORER   qdc507-v43-device-restore.py   c85fbadc6c152729dfff1ff3aabbaac0ae8698cfe9469a120754cd36302aff7d


# ADB watchdog process and heartbeat: read-only
echo "===== ADB V2.4 monitor ====="
/usr/bin/python3.8 -I -B - <<'PY_ADB_WATCH_AUDIT'
import os, stat, sys, time
from pathlib import Path

lab = Path('/volume2/docker/asterisk/qdc507-16k-lab')
watch = os.fsencode(str(lab / 'qdc507-adb-watch-alert-memoryfix-v2.py'))
pids = []

for entry in os.scandir('/proc'):
    if not entry.name.isdigit():
        continue
    try:
        argv = Path('/proc', entry.name, 'cmdline').read_bytes().split(b'\x00')
    except (OSError, ValueError):
        continue
    if watch in argv and b'--watch' in argv:
        pids.append(int(entry.name))

print('ADB_WATCH_PROCESS_COUNT=%d' % len(pids))
if len(pids) != 1:
    print('ADB_WATCH_AUDIT=FAIL_PROCESS_COUNT')
    sys.exit(2)

print('ADB_WATCH_PID=%d' % pids[0])

now = time.time()
fresh = []

for name in (
    'qdc507-adb-alert.log',
    'qdc507-adb-watch-manual-20261121.log',
):
    path = lab / name
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            continue
        age = now - st.st_mtime
        if age < -30 or age > 180:
            continue
        with path.open('rb') as f:
            f.seek(max(0, st.st_size - 8192))
            lines = f.read(8192).splitlines()
        last = lines[-1] if lines else b''
        indicators = (
            b'SHELL_OK', b'SHELL_FAIL', b'SHELL_RETRY_OK',
            b'WATCH_V2_', b'GUARDED_RESET',
            b'ADB_RECOVERED', b'ALERT_', b'RECOVERY_NOTICE'
        )
        if not any(x in last for x in indicators):
            continue
        fresh.append((age, name))
    except (OSError, ValueError):
        continue

if not fresh:
    print('ADB_WATCH_AUDIT=FAIL_HEARTBEAT_STALE_OR_MISSING')
    sys.exit(2)

age, name = min(fresh)
print('ADB_WATCH_HEARTBEAT_LOG=' + name)
print('ADB_WATCH_HEARTBEAT_AGE_SECONDS=%d' % max(0, int(age)))
print('ADB_WATCH_HEARTBEAT=PASS')
print('ADB_WATCH_AUDIT=PASS')
PY_ADB_WATCH_AUDIT

echo "CURRENT_PRODUCTION_AUDIT=PASS"
echo "AUDIT_READ_ONLY=1"
