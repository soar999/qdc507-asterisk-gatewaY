#!/bin/sh
# QDC507 16k-only memoryfix v2 controlled cutover. Synology/BusyBox POSIX sh.
# No writes outside Docker and the existing LAB root. Never modifies 8k mode in live container.
set -eu

LAB=/volume2/docker/asterisk/qdc507-16k-lab
PROD=qdc507-hardware
BACKUP=qdc507-hardware-v4-pre-memoryfix-20261118
FAILED=qdc507-hardware-v2-failed-20261118
CAND=qdc507-asterisk:16k-memoryfix-v2-candidate-20261118
OLD_SHA=ca25e8e04a5ad9792282635a99e1d7152429800d6a51331ba18991e72436fa87
NEW_SHA=c5b940d5505fc40d9c2815674b1107586b39a0fba5fe0e0dce060c111a680bf0
MODULE=/usr/lib/asterisk/modules/chan_quectel.so

msg() { printf '%s\n' "$*"; }
die() { msg "ERROR=$*" >&2; exit 1; }
exists() { docker inspect "$1" >/dev/null 2>&1; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
sha() { docker exec "$1" sha256sum "$MODULE" 2>/dev/null | awk '{print $1}'; }

idle() {
    [ "$(docker exec "$1" asterisk -rx 'core show channels count' 2>/dev/null | grep -xc '0 active channels')" -eq 1 ] &&
    [ "$(docker exec "$1" asterisk -rx 'core show channels count' 2>/dev/null | grep -xc '0 active calls')" -eq 1 ]
}

module_running() {
    docker exec "$1" asterisk -rx 'module show like chan_quectel' 2>/dev/null |
        grep -E '^[[:space:]]*chan_quectel\.so[[:space:]]' | grep -q 'Running'
}

wait_ready() {
    NAME=$1
    WANT_SHA=$2
    N=0
    while [ "$N" -lt 45 ]; do
        if running "$NAME" && [ "$(sha "$NAME" || true)" = "$WANT_SHA" ] && module_running "$NAME"; then
            msg "ASTERISK_DRIVER_READY=1"
            return 0
        fi
        N=$((N+1))
        sleep 1
    done
    msg '===== Startup diagnostics ====='
    docker logs --tail 65 "$NAME" 2>&1 || true
    return 1
}

watcher_listing() {
    msg '===== ADB watchdog processes (read-only) ====='
    FOUND=0
    for P in /proc/[0-9]*/cmdline; do
        [ -r "$P" ] || continue
        CMD=$(tr '\000' ' ' < "$P" 2>/dev/null || true)
        case "$CMD" in
            *qdc507-adb-watch-alert-v2.1.py*)
                PID=${P%/cmdline}; PID=${PID##*/}
                STATE=$(sed -n '/^State:/p' "/proc/$PID/status" 2>/dev/null || true)
                msg "WATCHDOG_PID=$PID $STATE $CMD"
                FOUND=1
                ;;
        esac
    done
    [ "$FOUND" -eq 1 ] || msg 'WATCHDOG_PROCESS_NOT_FOUND=1'
    msg 'WATCHDOG_SCHEDULER_STATUS=NOT_VERIFIED'
}

watcher_gate() {
    [ "${QDC507_WATCHDOG_PAUSED:-}" = YES ] || die 'Set QDC507_WATCHDOG_PAUSED=YES only AFTER disabling its scheduler and pausing the watchdog. Refusing cutover.'
    for P in /proc/[0-9]*/cmdline; do
        [ -r "$P" ] || continue
        CMD=$(tr '\000' ' ' < "$P" 2>/dev/null || true)
        case "$CMD" in
            *qdc507-adb-watch-alert-v2.1.py*)
                PID=${P%/cmdline}; PID=${PID##*/}
                STATE=$(sed -n '/^State:/p' "/proc/$PID/status" 2>/dev/null || true)
                case "$STATE" in
                    'State:'*'T ('*|'State:'*'t ('*) msg "WATCHDOG_PID_${PID}_PAUSED=1" ;;
                    *) die "ADB watcher PID=$PID is running, not paused: $STATE" ;;
                esac
                ;;
        esac
    done
}

preflight() {
    msg '===== Cutover preflight (no production changes) ====='
    exists "$PROD" || die 'Original production container missing'
    running "$PROD" || die 'Original production container is not running'
    exists "$BACKUP" && die 'Backup container name already exists'
    exists "$FAILED" && die 'Failed-candidate container name already exists'
    docker image inspect "$CAND" >/dev/null 2>&1 || die 'Candidate image missing'
    [ "$(sha "$PROD")" = "$OLD_SHA" ] || die 'Production driver SHA mismatch'
    [ "$(docker exec "$PROD" cat /etc/qdc507-mode/current)" = 16k ] || die 'Production not in 16k mode'
    [ "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$PROD")" = host ] || die 'Production is not host-networked'
    [ "$(docker inspect -f '{{len .Mounts}}' "$PROD")" -eq 14 ] || die 'Production mount count no longer 14'
    [ "$(docker inspect -f '{{.Config.Image}}' "$PROD")" = 'qdc507-asterisk:dualmode-remotehangup-v4-prod' ] || die 'Production image tag unexpected'
    idle "$PROD" || die 'Production has active calls or core CLI unavailable'
    msg 'PRODUCTION_IDLE_AND_PINNED=1'
    watcher_listing
    msg 'WATCHDOG_MUST_BE_PAUSED_AND_SCHEDULER_DISABLED_BEFORE_SWITCH=1'
    msg 'CUTOVER_PREFLIGHT_PASSED=1'
}

cleanup_on_error() {
    RC=$?
    trap - EXIT HUP INT TERM
    [ "$RC" -ne 0 ] || exit 0
    msg "CUTOVER_FAILED_EXIT_CODE=$RC"
    msg 'AUTOMATIC_ROLLBACK_BEGIN=1'
    if exists "$PROD"; then
        ACTIVE_ID=$(docker inspect -f '{{.Id}}' "$PROD" 2>/dev/null || true)
        if [ "$ACTIVE_ID" = "$ORIGINAL_ID" ]; then
            docker start "$PROD" >/dev/null 2>&1 || true
        else
            docker stop -t 15 "$PROD" >/dev/null 2>&1 || true
            if ! exists "$FAILED"; then
                docker rename "$PROD" "$FAILED" || true
            else
                msg 'FAILED_NAME_OCCUPIED_MANUAL_RECOVERY_NEEDED=1'
            fi
        fi
    fi
    if exists "$BACKUP"; then
        if ! exists "$PROD"; then
            docker rename "$BACKUP" "$PROD" || true
        fi
    fi
    if exists "$PROD"; then
        docker start "$PROD" >/dev/null 2>&1 || true
        if wait_ready "$PROD" "$OLD_SHA"; then
            msg 'AUTOMATIC_ROLLBACK_OLD_SERVICE_RESTORED=1'
        else
            msg 'AUTOMATIC_ROLLBACK_REQUIRES_MANUAL_ATTENTION=1'
        fi
    else
        msg 'AUTOMATIC_ROLLBACK_REQUIRES_MANUAL_ATTENTION=1'
    fi
    exit "$RC"
}

switch_now() {
    preflight
    watcher_gate
    msg '===== Production switch: expected service interruption ====='
    ORIGINAL_ID=$(docker inspect -f '{{.Id}}' "$PROD")
    trap 'cleanup_on_error' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    
    docker stop -t 35 "$PROD" >/dev/null
    docker rename "$PROD" "$BACKUP"
    msg 'ORIGINAL_CONTAINER_RETAINED=1'
    
    docker create \
      --name "$PROD" \
      --network host \
      --ipc private \
      --user root \
      --restart no \
      --cap-add CHOWN \
      --cap-add DAC_OVERRIDE \
      --cap-add FOWNER \
      --cap-add SETUID \
      --cap-add SETGID \
      --cap-add SYS_NICE \
      --device-cgroup-rule 'c 188:* rwm' \
      --device-cgroup-rule 'c 116:* rwm' \
      --volumes-from "$BACKUP" \
      "$CAND" >/dev/null
    
    [ "$(docker inspect -f '{{len .Mounts}}' "$PROD")" -eq 14 ] || die 'New container has unexpected mount count'
    [ "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$PROD")" = host ] || die 'New container network mismatch'
    docker start "$PROD" >/dev/null
    wait_ready "$PROD" "$NEW_SHA" || die 'New Asterisk/driver not ready'
    docker exec "$PROD" asterisk -rx 'core show channels count' || die 'New Asterisk core CLI failed'
    msg 'CUTOVER_IMAGE_AND_MODULE_VERIFIED=1'
    msg 'OLD_CONTAINER_NAME='"$BACKUP"
    msg 'CANDIDATE_LIVE=1'
    msg 'FOLLOWUP_REQUIRED=Real call in/out, remote-hangup, SMS, CLCC/ATD error regression and memory monitoring'
    trap - EXIT HUP INT TERM
}

rollback_now() {
    [ "${QDC507_WATCHDOG_PAUSED:-}" = YES ] || die 'Pause watcher and disable scheduler before rollback; set QDC507_WATCHDOG_PAUSED=YES after doing so.'
    watcher_gate
    exists "$BACKUP" || die 'Old backup container missing'
    exists "$PROD" || die 'Candidate container missing'
    exists "$FAILED" && die 'Failed-candidate archive already exists'
    [ "$(docker inspect -f '{{.Config.Image}}' "$PROD")" = "$CAND" ] || die 'Current production container is not expected candidate image'
    if running "$PROD"; then
        if ! idle "$PROD"; then
            [ "${QDC507_FORCE_ROLLBACK:-}" = YES ] || die 'Candidate not idle or Asterisk unavailable; to force only after confirming safe, set QDC507_FORCE_ROLLBACK=YES'
        fi
        docker stop -t 35 "$PROD" >/dev/null
    fi
    docker rename "$PROD" "$FAILED"
    docker rename "$BACKUP" "$PROD"
    docker start "$PROD" >/dev/null
    wait_ready "$PROD" "$OLD_SHA" || die 'Original container failed to become ready; candidate remains preserved under failed name'
    msg 'ROLLBACK_OLD_SERVICE_RESTORED=1'
    msg 'NEW_CANDIDATE_PRESERVED_FOR_INSPECTION='"$FAILED"
}

case "${1:-}" in
    --preflight) preflight ;;
    --switch) switch_now ;;
    --rollback) rollback_now ;;
    --status)
        for N in "$PROD" "$BACKUP" "$FAILED"; do
            if exists "$N"; then
                msg "CONTAINER=$N IMAGE=$(docker inspect -f '{{.Config.Image}}' "$N") RUNNING=$(docker inspect -f '{{.State.Running}}' "$N")"
            fi
        done
        watcher_listing
        ;;
    *)
        msg 'Usage: sh qdc507-memoryfix-v2-cutover.sh --preflight|--switch|--rollback|--status'
        msg 'Switch and rollback require watchdog scheduler paused, watchdog process stopped, and explicit QDC507_WATCHDOG_PAUSED=YES.'
        exit 2
        ;;
esac
