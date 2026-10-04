#!/bin/sh
set -u

NEWID=/sys/bus/usb-serial/drivers/option1/new_id
LINK=/dev/qdc507-at

# option 动态 VID/PID 表在 NAS 重启后会消失。
# 即使当前模块未插入，也先登记；之后热插拔仍会自动匹配。
if [ -w "$NEWID" ]; then
    printf "2ca3 4006\n" > "$NEWID" 2>/dev/null || true
fi

find_if2()
{
    for t in /sys/class/tty/ttyUSB*; do
        [ -e "$t" ] || continue

        ttypath=$(readlink -f "$t/device" 2>/dev/null) || continue
        iface=$(dirname "$ttypath")
        usbdev=$(dirname "$iface")

        [ -f "$iface/bInterfaceNumber" ] || continue
        [ -f "$usbdev/idVendor" ] || continue
        [ -f "$usbdev/idProduct" ] || continue

        [ "$(cat "$iface/bInterfaceNumber")" = "02" ] || continue
        [ "$(cat "$usbdev/idVendor")" = "2ca3" ] || continue
        [ "$(cat "$usbdev/idProduct")" = "4006" ] || continue

        echo "/dev/$(basename "$t")"
        return 0
    done

    return 1
}

n=0
QDC_AT=""
while [ "$n" -lt 60 ]; do
    QDC_AT=$(find_if2 2>/dev/null || true)
    [ -n "$QDC_AT" ] && break
    sleep 0.5
    n=$((n+1))
done

if [ -z "$QDC_AT" ]; then
    echo "QDC507 IF2 not found" >&2
    exit 1
fi

rm -f "$LINK"
ln -s "$QDC_AT" "$LINK"

echo "$QDC_AT"
