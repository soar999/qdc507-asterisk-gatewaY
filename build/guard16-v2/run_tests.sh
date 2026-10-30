#!/bin/sh
set -eu
cd "$(dirname "$0")"
ORIGINAL=${ORIGINAL:-"$PWD/original.c"}
export ORIGINAL
CC=${CC:-cc}
BUILD=$(mktemp -d /tmp/guard16-regression.XXXXXX)
trap 'rm -rf "$BUILD"' EXIT HUP INT TERM
FLAGS="-std=gnu11 -Wall -Wextra -Werror -Wno-error=discarded-qualifiers -pthread ${EXTRA_CFLAGS:-}"
$CC $FLAGS -fsyntax-only mavo_pcm_bridge_16k_clock8_guard16.c
$CC $FLAGS test_guard16.c -ldl -o "$BUILD/test_guard16"
$CC $FLAGS -DBRIDGE_SOURCE=\"$ORIGINAL\" test_guard16.c -ldl -o "$BUILD/test_original"
for ms in 0 7 8 9 16; do
    "$BUILD/test_original" "$ms" steady baseline
    for mode in steady burst nodata odd frag1 frag127 frag255 flood eof; do
        "$BUILD/test_guard16" "$ms" "$mode"
    done
done
for mode in initial_empty poll_eintr read_eintr eagain zero_once poll_error read_error hup pollerr nval write_error stop_initial stop_write stop_poll stop_read; do
    "$BUILD/test_guard16" 8 "$mode"
done
for fragment in 1 127 255; do
    for ms in 0 7 8 9 16; do
        for mode in burst stop_read stop_poll stop_write; do
            echo "CASE fragment=$fragment ms=$ms mode=$mode"
            FRAGMENT=$fragment "$BUILD/test_guard16" "$ms" "$mode"
        done
    done
done
python3 check_unchanged.py
$CC $FLAGS test_d6_unchanged.c -ldl -o "$BUILD/test_d6"
$CC $FLAGS -DBRIDGE_SOURCE=\"$ORIGINAL\" test_d6_unchanged.c -ldl -o "$BUILD/test_d6_original"
for ms in 0 7 8 9 16; do
    for mode in full partial drop error capture_error stop_partial; do
        "$BUILD/test_d6" "$ms" "$mode" > "$BUILD/d6-new"
        "$BUILD/test_d6_original" "$ms" "$mode" > "$BUILD/d6-old"
        cmp "$BUILD/d6-new" "$BUILD/d6-old"
        cat "$BUILD/d6-new"
    done
done
