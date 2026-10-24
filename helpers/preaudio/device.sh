#!/bin/sh
# Per-call experimental controller; no daemon; private root, never shared with v7.
set -eu
umask 077
D=$1; B=$2; A=$3; T=$4
case "$T" in ''|*[!a-zA-Z0-9_-]*) exit 64;; esac
[ ${#T} -le 80 ] || exit 64
if [ "$A" != query ]; then mkdir -p "$D/tokens/$T" || exit 70; fi
C=$D/tokens/$T
SELF=$0
# Test redirection is explicit; normal invocation uses real proc/sys.
P=${QDC_TEST_PCM_ROOT:-/proc/asound/card0}
E=${QDC_TEST_ENABLE:-/sys/class/android_usb/f_audio/audio_enable}
executable() { [ -x "$1" ] || { [ "${QDC_TEST_INTERPRETERS:-0}" = 1 ] && [ -r "$1" ]; }; }
bridge() {
 if [ "${QDC_TEST_INTERPRETERS:-0}" = 1 ]; then exec /bin/sh "$@"; else exec "$@"; fi
}
clock() {
 if [ "${QDC_TEST_INTERPRETERS:-0}" = 1 ]; then
  if [ -n "${QDC_TEST_CLOCK:-}" ]; then cat "$QDC_TEST_CLOCK"; else date +%s; fi
 else cut -d. -f1 /proc/uptime; fi
}
put() { printf '%s\n' "$2" > "$1.$$" && mv "$1.$$" "$1"; }
lock() {
 i=0
 while ! mkdir "$D/lock" 2>/dev/null; do
  i=$((i+1)); [ "$i" -lt 40 ] || { echo LOCK_TIMEOUT; exit 75; }
  sleep 0.1
 done
 # Deliberately no trap removing lock: interruption preserves quarantine.
}
unlock() { rmdir "$D/lock"; }
own() { [ "$(cat "$D/owner" 2>/dev/null)" = "$T" ]; }
allowed() { own && [ ! -d "$C/cancel" ]; }
identity() {
 [ -r "/proc/$1/stat" ] || return 1
 x=$(cat "/proc/$1/stat") || return 1
 x=${x##*) }; set -- $x
 [ "$1" != Z ] || return 1
 shift 19; printf '%s\n' "$1"
}
alive() {
 [ -r "$C/$1.pid" ] || return 1
 read pid birth < "$C/$1.pid"
 [ -n "$birth" ] && [ "$(identity "$pid")" = "$birth" ]
}
record() {
 birth=$(identity "$2") || return 1
 put "$C/$1.pid" "$2 $birth"
}
closed() {
 [ "$(cat "$E")" = 0 ] || return 1
 for n in 4p 4c 5p 6c; do
  [ "$(cat "$P/pcm$n/sub0/hw_params")" = closed ] || return 1
 done
}
ready() {
 alive d4 && alive d5 && [ "$(cat "$E")" = 0 ] || return 1
 for n in 4p 4c 5p 6c; do
  # Device busybox grep, not a host dependency.
  grep -Eq '^rate: 16000( |$)' "$P/pcm$n/sub0/hw_params" || return 1
  grep -Eq '^state: (RUNNING|PREPARED)$' "$P/pcm$n/sub0/status" || return 1
 done
}
failed() {
 put "$C/fault" "$1"
 mkdir "$C/cancel" 2>/dev/null || :
 [ -d "$C/cancel" ] || exit 70
 put "$C/supervisor.done" 1
 /bin/sh "$SELF" "$D" "$B" cancel "$T" </dev/null >>"$C/cleanup.log" 2>&1 &
 exit 1
}
case "$A" in
launch)
 lock
 if ! [ -d "$C/cancel" ] && ! [ -e "$D/owner" ] && closed && executable "$B/bridge-d4" && executable "$B/bridge-d5"; then
  put "$D/owner" "$T"
  setsid /bin/sh "$SELF" "$D" "$B" supervise "$T" </dev/null >"$C/supervisor.log" 2>&1 &
  p=$!
  if ! record supervisor "$p"; then put "$C/fault" SUPERVISOR_ID_UNKNOWN; unlock; exit 71; fi
  unlock
  # Original remote helper keepalive remains; receipt is queried separately.
  sleep 1
  [ -f "$C/accepted" ] && [ ! -d "$C/cancel" ] || exit 72
  echo "LAUNCH_HELD $T"
 else
  unlock; echo REJECTED; exit 73
 fi
 ;;
supervise)
 lock
 if ! allowed; then put "$C/supervisor.done" 1; unlock; exit 0; fi
 put "$C/accepted" "$T"
 bridge "$B/bridge-d4" --voice-route-session --verbose </dev/null >"$C/d4.log" 2>&1 &
 p=$!
 if ! record d4 "$p"; then put "$C/fault" D4_ID_UNKNOWN; exit 71; fi
 unlock
 sleep 1
 lock
 if ! allowed; then put "$C/supervisor.done" 1; unlock; exit 0; fi
 if ! alive d4; then unlock; failed D4_START_FAILED; fi
 bridge "$B/bridge-d5" --tty /dev/ttyGS0 --playback-device hw:0,5 --capture-device hw:0,6 --no-mixers --verbose </dev/null >"$C/d5.log" 2>&1 &
 p=$!
 if ! record d5 "$p"; then put "$C/fault" D5_ID_UNKNOWN; exit 71; fi
 unlock
 sleep 1
 # Deadline applies only until initial readiness; no call-duration limit.
 start=$(clock)
 while :; do
  if [ -d "$C/cancel" ]; then put "$C/supervisor.done" 1; exit 0; fi
  now=$(clock)
  alive d4 && alive d5 || failed BRIDGE_EXIT
  if ready; then put "$C/ready" "$T"; elif [ ! -f "$C/ready" ]; then
   [ $((now-start)) -lt 10 ] || failed READY_TIMEOUT
  fi
  sleep 0.2
 done
 ;;
query)
 # Read only, no lock acquisition; recheck irreversible cancellation last.
 if own && alive supervisor && [ -f "$C/accepted" ] && [ ! -f "$C/fault" ]; then
  s=ACCEPTED
  if [ -f "$C/ready" ] && ready; then s=READY; fi
  if allowed; then echo "$s $T"; exit 0; fi
 fi
 echo "NOT_READY $T"; exit 74
 ;;
cancel)
 # Tombstone precedes lock, including cancel-before-launch and lock timeout.
 mkdir "$C/cancel" 2>/dev/null || :
 [ -d "$C/cancel" ] || exit 70
 lock
 if ! own; then
  if [ -e "$D/owner" ]; then echo "STALE_FENCED $T"; else echo "FENCED $T"; fi
  unlock; exit 0
 fi
 # Lock remains held over termination/verification; supervisor never needs it
 # once tombstoned, except a previously waiting stage (which cannot spawn).
 for name in d5 d4; do
  if alive "$name"; then kill -TERM "$pid" 2>/dev/null || :; fi
 done
 i=0
 while [ "$i" -lt 30 ]; do
  alive d4 || alive d5 || break
  sleep 0.1; i=$((i+1))
 done
 for name in d5 d4; do
  if alive "$name"; then kill -KILL "$pid" 2>/dev/null || :; fi
 done
 i=0
 while [ "$i" -lt 20 ]; do
  if ! alive d4 && ! alive d5 && closed; then
   # Supervisor may still be waiting on lock, but tombstone blocks all spawn.
   put "$C/clean" "$T"
   rm "$D/owner"
   unlock; echo "CLEAN $T"; exit 0
  fi
  sleep 0.1; i=$((i+1))
 done
 put "$C/fault" CLEANUP_UNCONFIRMED
 echo "QUARANTINED $T"
 # Retain lock AND owner. No automatic stale lock recovery.
 exit 76
 ;;
*) exit 64;;
esac
