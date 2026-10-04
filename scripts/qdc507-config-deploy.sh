#!/bin/sh
set -eu

BASE=/volume2/docker/asterisk/qdc507-current
EDIT="$BASE/v0-edit"
RUN="$BASE/v0"
BACKUP="$BASE/config-backups"

FILES="
asterisk.conf
extensions.conf
logger.conf
pjsip.conf
quectel.conf
rtp.conf
dnsmgr.conf
"

STAMP="$(date +%Y%m%d-%H%M%S)"

echo "=== QDC507 config deploy ==="

# 1. 检查编辑区文件完整
for f in $FILES; do
    if [ ! -s "$EDIT/$f" ]; then
        echo "ERROR: missing or empty: $EDIT/$f"
        exit 1
    fi

    if [ ! -f "$RUN/$f" ]; then
        echo "ERROR: production file missing: $RUN/$f"
        exit 1
    fi
done

if [ ! -s "$EDIT/modules.hardware.runtime.conf" ]; then
    echo "ERROR: missing or empty: $EDIT/modules.hardware.runtime.conf"
    exit 1
fi

if [ ! -f "$BASE/modules.hardware.runtime.conf" ]; then
    echo "ERROR: production modules.hardware.runtime.conf missing"
    exit 1
fi

# 2. 查找发生变化的文件
CHANGED=""

for f in $FILES; do
    if ! cmp -s "$EDIT/$f" "$RUN/$f"; then
        CHANGED="$CHANGED $f"
    fi
done

if ! cmp -s \
    "$EDIT/modules.hardware.runtime.conf" \
    "$BASE/modules.hardware.runtime.conf"; then
    CHANGED="$CHANGED modules.hardware.runtime.conf"
fi

if [ -z "$CHANGED" ]; then
    echo "NO_CHANGES"
    exit 0
fi

echo "Changed:$CHANGED"

# 3. 部署前备份
mkdir -p "$BACKUP"

tar czf "$BACKUP/qdc507-config-$STAMP.tar.gz" \
    -C "$BASE" \
    v0 \
    modules.hardware.runtime.conf

echo "Backup: $BACKUP/qdc507-config-$STAMP.tar.gz"

# 4. 只覆盖文件内容
# 不复制 File Station / DSM ACL
for f in $FILES; do
    if ! cmp -s "$EDIT/$f" "$RUN/$f"; then
        cat "$EDIT/$f" > "$RUN/$f"
        chmod 644 "$RUN/$f"
        echo "DEPLOYED: $f"
    fi
done

if ! cmp -s \
    "$EDIT/modules.hardware.runtime.conf" \
    "$BASE/modules.hardware.runtime.conf"; then

    cat "$EDIT/modules.hardware.runtime.conf" \
        > "$BASE/modules.hardware.runtime.conf"

    chmod 644 "$BASE/modules.hardware.runtime.conf"
    echo "DEPLOYED: modules.hardware.runtime.conf"
fi

# 5. 最后校验内容
for f in $FILES; do
    if ! cmp -s "$EDIT/$f" "$RUN/$f"; then
        echo "ERROR: verification failed: $f"
        exit 1
    fi
done

if ! cmp -s \
    "$EDIT/modules.hardware.runtime.conf" \
    "$BASE/modules.hardware.runtime.conf"; then
    echo "ERROR: verification failed: modules.hardware.runtime.conf"
    exit 1
fi

echo "DEPLOY_OK"
echo "NOTE: Asterisk was NOT reloaded or restarted."
