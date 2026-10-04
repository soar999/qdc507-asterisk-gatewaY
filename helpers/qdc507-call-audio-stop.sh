#!/bin/sh
set -eu
PER_CALL_LOG=/var/log/asterisk/qdc507-per-call.log
exec >>"$PER_CALL_LOG" 2>&1
echo "=== STOP $(date) pid=$$ ==="
set -x

A=/usr/local/bin/adb
export HOME=/tmp/qdc507-adb-client
mkdir -p "$HOME"
export ADB_SERVER_SOCKET=${ADB_SERVER_SOCKET:-tcp:172.30.50.1:5037}

PID=/run/djonehub-voice-route.pid
HELPER=/tmp/djonehub-call/mavo-pcm-bridge.armv7

# Stop only the helper instance recorded for this call (PID + start time + argv).
$A shell "if test -s $PID; then read p st < $PID || exit 20; cur=\$(cut -d ' ' -f 22 /proc/\$p/stat 2>/dev/null); exe=\$(tr '\\000' '\\n' < /proc/\$p/cmdline 2>/dev/null | sed -n '1p'); args=\$(tr '\\000' '\\n' < /proc/\$p/cmdline 2>/dev/null); if test \"\$cur\" = \"\$st\" && test \"\$exe\" = '$HELPER' && printf '%s\\n' \"\$args\" | grep -q '^--voice-route-session$'; then kill -TERM \$p; n=0; while test \"\$(cut -d ' ' -f 22 /proc/\$p/stat 2>/dev/null)\" = \"\$st\" && test \$n -lt 50; do sleep 0.1; n=\$((n+1)); done; test \"\$(cut -d ' ' -f 22 /proc/\$p/stat 2>/dev/null)\" != \"\$st\" || exit 21; fi; rm -f $PID; fi"

# Same route rollback sequence used by DJOneHub.
$A shell "echo 0 > /sys/class/android_usb/f_audio/audio_enable; if test -p /run/voc_svr; then printf 'T\\n' > /run/voc_svr; printf 'T\\n' > /run/voc_svr; printf 'B\\n' > /run/voc_svr; fi; test \"\$(cat /sys/class/android_usb/f_audio/audio_enable)\" = 0"
