#!/bin/sh
# QDC507 production-only audit, 2026-11-20
# Retains the historical audit entry path; no recovery actions.
set -eu

LAB=/volume2/docker/asterisk/qdc507-16k-lab
AUDIT="$LAB/qdc507-memoryfix-v22-audit.py"
EXPECTED="fac707fda70b67ec9a769a30edf54836b253f45be7359a5b8ab7f9b9bb9e34fb"

echo "PATCH_AUDIT_VERSION=20261120_CURRENT_PRODUCTION"

MODE="${1:---check}"
case "$MODE" in
    --check|--deep) ;;
    *) echo "USE_--check_OR_--deep" >&2; exit 2 ;;
esac

ACTUAL=$(sha256sum "$AUDIT" | awk '{print $1}')
if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "CURRENT_AUDIT_HASH_MISMATCH=1"
    exit 2
fi

/usr/bin/python3.8 -I -B "$AUDIT" "$MODE"

echo "===== Current V43 voice components ====="

verify_component() {
    NAME="$1"
    FILE="$2"
    EXPECTED_SHA="$3"

    ACTUAL_SHA=$(docker exec qdc507-hardware sha256sum         "/opt/qdc16k-preaudio-manual/$FILE" | awk '{print $1}')

    if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
        echo "$NAME=HASH_MISMATCH"
        exit 2
    fi
    echo "$NAME=PASS"
}

verify_component V43_INBOUND_CONTROLLER   preaudio-inbound-v43.py   13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf

verify_component V43_INBOUND_AGI   agi-wrapper-inbound-v43   472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98

verify_component V43_DEVICE_RESTORER   qdc507-v43-device-restore.py   c85fbadc6c152729dfff1ff3aabbaac0ae8698cfe9469a120754cd36302aff7d

echo "CURRENT_PRODUCTION_AUDIT=PASS"
echo "AUDIT_READ_ONLY=1"
