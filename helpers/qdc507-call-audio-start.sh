#!/bin/sh
set -eu
PER_CALL_LOG=/var/log/asterisk/qdc507-per-call.log
exec >>"$PER_CALL_LOG" 2>&1
echo "=== START $(date) pid=$$ ==="
set -x

A=/usr/local/bin/adb
export HOME=/tmp/qdc507-adb-client
mkdir -p "$HOME"
export ADB_SERVER_SOCKET=${ADB_SERVER_SOCKET:-tcp:172.30.50.1:5037}

R=/tmp/djonehub-call
PID=/run/djonehub-voice-route.pid
ROUTE_LOG=/run/djonehub-voice-route.log

# 陈旧状态自愈：上一通异常（容器重启/崩溃）可能遗留 helper 或 audio_enable=1。
# 发现即先执行标准停止序列，再启动本通新 helper，避免泄漏后所有呼叫被拒。
STALE=$($A shell "stale=0; [ -s $PID ] && stale=1; [ \"\$(cat /sys/class/android_usb/f_audio/audio_enable)\" != 0 ] && stale=1; ps | grep -q '[m]avo-pcm-bridge' && stale=1; echo \$stale" | tr -d '\r')
if [ "$STALE" = 1 ]; then
    echo "stale helper/audio state detected; running standard stop sequence first"
    /usr/local/sbin/qdc507-call-audio-stop.sh
fi

# One fresh helper per active call. After self-heal, state must be clean.
$A shell "test -x $R/mavo-pcm-bridge.armv7; test \"\$(cat /sys/class/android_usb/f_audio/audio_enable)\" = 0; ! ps | grep -q '[m]avo-pcm-bridge'"
$A shell "if test -s $PID; then read p st < $PID || exit 20; cur=\$(cut -d ' ' -f 22 /proc/\$p/stat 2>/dev/null); args=\$(tr '\\000' '\\n' < /proc/\$p/cmdline 2>/dev/null); test \"\$cur\" != \"\$st\" || ! printf '%s\\n' \"\$args\" | grep -q '^--voice-route-session$' || exit 21; rm -f $PID; fi"
$A shell "rm -f $ROUTE_LOG; nohup $R/mavo-pcm-bridge.armv7 --voice-route-session --verbose </dev/null >$ROUTE_LOG 2>&1 & p=\$!; st=\$(cut -d ' ' -f 22 /proc/\$p/stat); printf '%s %s\\n' \"\$p\" \"\$st\" >$PID"

# Poll immediately; audio_enable may briefly re-enumerate USB, so transient ADB
# failures are expected and retried. Avoid a fixed 2-second post-answer delay.
n=0
while [ "$n" -lt 100 ]; do
    if $A shell "test -s $PID && read p st < $PID && test \"\$(cut -d ' ' -f 22 /proc/\$p/stat 2>/dev/null)\" = \"\$st\" && tr '\\000' '\\n' < /proc/\$p/cmdline | grep -q '^--voice-route-session$' && grep -q 'VoLTE route session active on hw:0,4' $ROUTE_LOG && test \"\$(cat /sys/class/android_usb/f_audio/audio_enable)\" = 1 && grep -q '^state: RUNNING' /proc/asound/card0/pcm4p/sub0/status && grep -q '^state: RUNNING' /proc/asound/card0/pcm4c/sub0/status"; then
        exit 0
    fi
    sleep 0.2
    n=$((n+1))
done

$A shell "cat $ROUTE_LOG" >&2 || true
exit 1
