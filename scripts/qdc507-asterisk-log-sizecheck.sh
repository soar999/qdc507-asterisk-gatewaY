#!/bin/sh
set -eu

CONTAINER="qdc507-hardware"
LOGDIR="/volume2/docker/asterisk/qdc507-current/v1"
LIMIT=$((100 * 1024 * 1024))

for f in debug messages security queue_log; do
    FILE="$LOGDIR/$f"

    [ -f "$FILE" ] || continue

    SIZE="$(wc -c < "$FILE" | tr -d ' ')"

    if [ "$SIZE" -ge "$LIMIT" ]; then
        docker exec "$CONTAINER" \
            asterisk -rx 'logger rotate' >/dev/null

        echo "ROTATED: $f reached $SIZE bytes"
        exit 0
    fi
done

echo "NO_ROTATION_NEEDED"
