#!/bin/sh
# QDC507 stable USB hot-recovery entry: MemoryFix V2.2
# Preserve old scheduler path and default --recover semantics.
set -eu

[ "$#" -le 1 ] || {
    echo "USB_SIM_SWAP_INVALID_ARGUMENT_COUNT" >&2
    exit 2
}

MODE="${1:---recover}"

case "$MODE" in
    --check|--recover) ;;
    *)
        echo "USE_--check_OR_--recover" >&2
        exit 2
        ;;
esac

exec /usr/bin/python3.8 -I -B /volume2/docker/asterisk/qdc507-16k-lab/qdc507-v44-runtime.py usb "$MODE"
