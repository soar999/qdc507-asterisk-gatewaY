#!/bin/sh
set -eu

[ "$(id -u)" = "0" ] || {
    echo "ROOT_REQUIRED"
    exit 2
}

HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd "$HERE/.." && pwd)

KERNEL="$(uname -r)"
[ "$KERNEL" = "4.4.180+" ] || {
    echo "UNSUPPORTED_KERNEL=$KERNEL"
    echo "The bundled Synology kernel modules are pinned to DS918+ kernel 4.4.180+."
    exit 3
}

echo "=== verify runtime hashes ==="

(
    cd "$ROOT/runtime/host"
    /usr/bin/sha256sum -c SHA256SUMS
)

(
    cd "$ROOT/runtime/device"
    /usr/bin/sha256sum -c SHA256SUMS
)

echo "=== create trusted runtime directories ==="

/usr/bin/install -d -o root -g root -m 700 \
    /usr/local/libexec/qdc507/boot \
    /usr/local/libexec/qdc507/runtime

echo "=== install host dependencies ==="

/usr/bin/install -o root -g root -m 755 \
    "$ROOT/runtime/host/adb" \
    /usr/local/libexec/qdc507/adb

/usr/bin/install -o root -g root -m 600 \
    "$ROOT/runtime/host/option.ko" \
    /usr/local/libexec/qdc507/boot/option.ko

/usr/bin/install -o root -g root -m 600 \
    "$ROOT/runtime/host/usb_wwan.ko" \
    /usr/local/libexec/qdc507/boot/usb_wwan.ko

/usr/bin/install -o root -g root -m 700 \
    "$ROOT/install/qdc507-bind-if2.sh" \
    /usr/local/libexec/qdc507/boot/qdc507-bind-if2.sh

echo "=== install QDC507 voice runtime ==="

/usr/bin/install -o root -g root -m 600 \
    "$ROOT/runtime/device/qdc507_aprv3.ko" \
    /usr/local/libexec/qdc507/runtime/qdc507_aprv3.ko

/usr/bin/install -o root -g root -m 600 \
    "$ROOT/runtime/device/qdc507_voice.ko" \
    /usr/local/libexec/qdc507/runtime/qdc507_voice.ko

/usr/bin/install -o root -g root -m 700 \
    "$ROOT/runtime/device/mavo-pcm-bridge.armv7" \
    /usr/local/libexec/qdc507/runtime/mavo-pcm-bridge.armv7

echo "=== install recovery scripts ==="

for f in \
    qdc507-adb-refresh.py \
    qdc507-boot-adb.py \
    qdc507-boot-prep.py \
    qdc507-coldstart.py \
    qdc507-coldstart-start-qtest.py \
    qdc507-host-audio-repair.py \
    qdc507-manual-recover.py \
    qdc507-one-task.py
do
    /usr/bin/install -o root -g root -m 700 \
        "$ROOT/host-recovery/$f" \
        "/usr/local/sbin/$f"
done

echo "=== install SMS host components ==="

/usr/bin/install -o root -g root -m 700 \
    "$ROOT/host-sms/qdc507-sms-queue-deliver-pinned.py" \
    /usr/local/sbin/qdc507-sms-queue-deliver-pinned.py

/usr/bin/install -o root -g root -m 700 \
    "$ROOT/host-sms/qdc507-sms-tick.py" \
    /usr/local/sbin/qdc507-sms-tick.py

/usr/bin/install -o root -g root -m 644 \
    "$ROOT/host-sms/qdc507-sms-tick.service" \
    /etc/systemd/system/qdc507-sms-tick.service

/usr/bin/install -o root -g root -m 644 \
    "$ROOT/host-sms/qdc507-sms-tick.timer" \
    /etc/systemd/system/qdc507-sms-tick.timer

echo "=== install maintenance scripts ==="

/usr/bin/install -o root -g root -m 755 \
    "$ROOT/scripts/qdc507-config-deploy.sh" \
    /usr/local/sbin/qdc507-config-deploy.sh

/usr/bin/install -o root -g root -m 755 \
    "$ROOT/scripts/qdc507-asterisk-log-sizecheck.sh" \
    /usr/local/sbin/qdc507-asterisk-log-sizecheck.sh

echo "=== install container helper copies ==="

BASE=/volume2/docker/asterisk/qdc507-current

/usr/bin/install -d -o root -g root -m 755 "$BASE/helpers"

/usr/bin/install -o root -g root -m 555 \
    "$ROOT/runtime/host/adb" \
    "$BASE/helpers/adb"

/usr/bin/install -o root -g root -m 755 \
    "$ROOT/helpers/qdc507-call-audio-start.sh" \
    "$BASE/helpers/qdc507-call-audio-start.sh"

/usr/bin/install -o root -g root -m 755 \
    "$ROOT/helpers/qdc507-call-audio-stop.sh" \
    "$BASE/helpers/qdc507-call-audio-stop.sh"

/bin/systemctl daemon-reload 2>/dev/null || true

echo
echo "QDC507_HOST_RUNTIME_INSTALLED=1"
echo "No container was started."
echo "No USB/device action was performed."
echo "SMS timer was installed but NOT enabled."
echo "Continue with docs/RESTORE-FROM-SCRATCH.md."
