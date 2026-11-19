#!/bin/sh
# QDC507 Watchdog v2.4b: deliberately narrow, rollback-capable monitor swap.
# Keeps QDC507 Asterisk/Guard16, USB reset helper, SMS timer and SIM7600 untouched.
set -eu
umask 077
L=/volume2/docker/asterisk/qdc507-16k-lab
S="$L/qdc507-watchdog-v24b-stage.py"
F="$L/qdc507-adb-watch-alert-memoryfix-v2.py"
NEW="$L/qdc507-adb-watch-alert-memoryfix-v24.py"
BACK="$L/qdc507-adb-watch-alert-memoryfix-v2.py.bak-before-v24b-20261118"
TMP="$L/qdc507-adb-watch-alert-memoryfix-v2.py.v24b-install-tmp"
LOG="$L/qdc507-adb-watch-v24b-launch-20261118.log"
OLDLOG="$L/qdc507-adb-watch-v2-launch-20261118-201846.log"
PY=/usr/bin/python3.8

if [ "$(id -u)" != 0 ]; then echo 'ABORT_REQUIRES_ROOT=1'; exit 1; fi
if [ -e "$BACK" ] || [ -e "$TMP" ]; then
    echo 'ABORT_BACKUP_OR_TEMP_ALREADY_EXISTS=1'
    exit 1
fi
[ -f "$S" ] && [ -f "$F" ] || { echo 'ABORT_MISSING_SOURCE_OR_STAGE=1'; exit 1; }

# Stage and test without touching the live monitor.
"$PY" -I -B "$S" --check
"$PY" -I -B "$S" --stage
"$PY" -I -B - "$NEW" <<'PYTEST'
import sys
from pathlib import Path
s = Path(sys.argv[1]).read_text(encoding='utf-8')
compile(s, sys.argv[1], 'exec')
assert "log('WATCH_V2_4_AUTO_RECOVERY_STARTED')" in s
assert "state['reset_attempted'] = True" in s
assert 'next_reset_retry_epoch' in s
print('V24B_READY_FOR_ACTIVATION=1')
PYTEST

# Do not interrupt a call or install while the monitored ADB is unhealthy.
echo '===== QDC507 monitor-only preflight ====='
STATE=$(docker exec qdc507-hardware adb -H 127.0.0.1 -P 5037 get-state 2>&1) || {
    echo "ABORT_ADB_NOT_HEALTHY: $STATE"; exit 1; }
[ "$STATE" = device ] || { echo "ABORT_ADB_STATE=$STATE"; exit 1; }
CH=$(docker exec qdc507-hardware asterisk -rx 'core show channels count') || {
    echo 'ABORT_ASTERISK_UNAVAILABLE=1'; exit 1; }
printf '%s\n' "$CH"
printf '%s\n' "$CH" | grep -Eq '^[[:space:]]*0 active channels' || {
    echo 'ABORT_ACTIVE_CHANNELS=1'; exit 1; }
printf '%s\n' "$CH" | grep -Eq '^[[:space:]]*0 active calls' || {
    echo 'ABORT_ACTIVE_CALLS=1'; exit 1; }
PID=$(ps -ef | awk '$0 ~ /[q]dc507-adb-watch-alert-memoryfix-v2\.py --watch$/ {print $2}')
case "$PID" in ''|*' '*) echo "ABORT_EXPECT_EXACTLY_ONE_WATCHDOG: $PID"; exit 1;; esac
printf 'OLD_WATCHDOG_PID=%s\n' "$PID"

# Save original and install an exact copy of the already-verified stage.
cp -p "$F" "$BACK"
cp -p "$NEW" "$TMP"
mv -f "$TMP" "$F"
RESTORED=0
SPAWNED_PID=''
rollback() {
    code=$1
    trap - EXIT HUP INT TERM
    echo 'V24B_ACTIVATION_FAILED_ROLLING_BACK=1'
    if [ -n "$SPAWNED_PID" ]; then
        kill -TERM "$SPAWNED_PID" 2>/dev/null || true
        sleep 2
    fi
    if [ "$RESTORED" -eq 0 ] && [ -f "$BACK" ]; then
        cp -p "$BACK" "$TMP" && mv -f "$TMP" "$F" || \
            echo 'ERROR_ORIGINAL_SOURCE_RESTORE_FAILED=1'
        RESTORED=1
    fi
    CURRENT=$(ps -ef | awk '$0 ~ /[q]dc507-adb-watch-alert-memoryfix-v2\.py --watch$/ {print $2}')
    if [ -z "$CURRENT" ]; then
        nohup "$PY" -u -I -B "$F" --watch >> "$LOG" 2>&1 < /dev/null &
        echo 'ROLLBACK_OLD_WATCHDOG_RESTART_ATTEMPTED=1'
    else
        printf 'ROLLBACK_MONITOR_STILL_RUNNING_PIDS=%s\n' "$CURRENT"
    fi
    exit "$code"
}
trap 'rollback $?' EXIT

kill -TERM "$PID"
i=0
while kill -0 "$PID" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -ge 20 ]; then
        echo 'ABORT_OLD_WATCHDOG_DID_NOT_EXIT=1'
        exit 1
    fi
    sleep 1
done

nohup "$PY" -u -I -B "$F" --watch >> "$LOG" 2>&1 < /dev/null &
SPAWNED_PID=$!
sleep 4
kill -0 "$SPAWNED_PID" 2>/dev/null || {
    echo 'ABORT_NEW_WATCHDOG_PROCESS_EXITED=1'; exit 1; }
CURRENT=$(ps -ef | awk '$0 ~ /[q]dc507-adb-watch-alert-memoryfix-v2\.py --watch$/ {print $2}')
[ "$CURRENT" = "$SPAWNED_PID" ] || {
    echo "ABORT_UNEXPECTED_WATCHDOG_PIDS: $CURRENT"; exit 1; }
if ! grep -q 'WATCH_V2_4_AUTO_RECOVERY_STARTED' "$LOG" "$OLDLOG" 2>/dev/null; then
    echo 'ABORT_START_MARKER_MISSING=1'
    exit 1
fi
trap - EXIT
printf 'WATCHDOG_V24B_ACTIVE_PID=%s\n' "$SPAWNED_PID"
echo 'V24B_ACTIVATED=1'
tail -8 "$LOG"
