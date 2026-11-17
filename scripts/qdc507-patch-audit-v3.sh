#!/bin/sh

PATH=/usr/local/bin:/usr/syno/bin:/usr/bin:/bin:/sbin
LAB=/volume2/docker/asterisk/qdc507-16k-lab
PROD=/volume2/docker/asterisk/qdc507-current
REC=$PROD/recovery
C=qdc507-hardware

echo
echo "=============================================="
echo " QDC507 / ASTERISK PRODUCTION PATCH AUDIT V3 (V43-AWARE)"
echo "=============================================="
date '+AUDIT_TIME=%Y-%m-%d %H:%M:%S %Z'
echo "PATCH_AUDIT_VERSION=V3_V43_INBOUND"

hash_file() {
    LABEL="$1"
    FILE="$2"
    EXPECTED="${3:-}"

    if [ ! -f "$FILE" ]; then
        echo "[MISSING] $LABEL"
        echo "  $FILE"
        return
    fi

    ACTUAL=$(sha256sum "$FILE" | awk '{print $1}')

    if [ -n "$EXPECTED" ]; then
        if [ "$ACTUAL" = "$EXPECTED" ]; then
            STATUS="OK"
        else
            STATUS="CHANGED"
        fi
    else
        STATUS="HASH"
    fi

    echo "[$STATUS] $LABEL"
    echo "  SHA256=$ACTUAL"

    if [ "$STATUS" = "CHANGED" ]; then
        echo "  EXPECTED=$EXPECTED"
    fi
}

section() {
    echo
    echo "========== $1 =========="
}

section "1. DSM / Python / System"

grep -E 'productversion|buildnumber|smallfixnumber' \
    /etc.defaults/VERSION 2>/dev/null

uname -r

/usr/bin/python3.8 -V 2>&1

df -h / | tail -1

section "2. Docker / Asterisk"

if docker inspect "$C" >/dev/null 2>&1; then
    docker inspect "$C" --format \
      'CONTAINER={{.Name}}
STATE={{.State.Status}}
IMAGE_NAME={{.Config.Image}}
IMAGE_ID={{.Image}}
CONTAINER_ID={{.Id}}'

    docker exec "$C" \
      asterisk -rx "core show version" 2>&1
else
    echo "[WARN] ASTERISK_CONTAINER_NOT_FOUND"
fi

section "3. chan_quectel / STK"

if docker inspect "$C" >/dev/null 2>&1; then

    docker exec "$C" \
      asterisk -rx "module show like chan_quectel" 2>&1

    docker exec "$C" sh -c \
      'sha256sum /usr/lib/asterisk/modules/chan_quectel.so 2>/dev/null' \
      || echo "[WARN] MODULE_HASH_UNAVAILABLE"

    docker exec "$C" sh -c '
      if grep -a -q QSTKURC \
        /usr/lib/asterisk/modules/chan_quectel.so 2>/dev/null
      then
        echo "STK_QSTKURC_BINARY_SIGNATURE=present"
      else
        echo "STK_QSTKURC_BINARY_SIGNATURE=not_detected"
      fi
    '

    docker exec "$C" \
      asterisk -rx "module show like res_pjsip_messaging" 2>&1
fi

section "4. USB Driver / Host Boot"

if [ -d /sys/module/option ]; then
    echo "OPTION_DRIVER_LOADED=1"
else
    echo "OPTION_DRIVER_LOADED=0"
fi

hash_file "USB_OPTION_KO" \
    /usr/local/libexec/qdc507/boot/option.ko

hash_file "BOOT_PREP" \
    /usr/local/sbin/qdc507-boot-prep.py

hash_file "BOOT_ADB" \
    /usr/local/sbin/qdc507-boot-adb.py

hash_file "USB_MANUAL_RECOVERY_V2" \
    "$REC/qdc507-usb-manual-recovery-v2.py"

section "5. Guard16 V2 / 16k Audio"

hash_file "GUARD16_V2_RESTORE" \
    "$LAB/qdc507-guard16-v2-runtime-restore-v2.py" \
    077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55

if [ -f "$LAB/qdc507-guard16-v2-runtime-restore-v2.py" ]; then
    echo "--- Guard16 runtime check ---"

    /usr/bin/python3.8 -I -B \
      "$LAB/qdc507-guard16-v2-runtime-restore-v2.py" \
      --check 2>&1 | grep -E \
      'DEVICE_CURRENT_MODE|DEVICE_V2_STAGE|DEVICE_V1_BACKUP|READ_ONLY_RUNTIME_CHECK_OK|ERROR|FAILED' \
      || echo "GUARD16_CHECK_OUTPUT_NOT_MATCHED"
fi

hash_file "AUDIO_START" \
    "$LAB/qdc507-mode-upgrade-c3e-preaudio-v2/dispatch/start.sh"

hash_file "AUDIO_STOP" \
    "$LAB/qdc507-mode-upgrade-c3e-preaudio-v2/dispatch/stop.sh"

section "6. PJSIP / Dialplan"

hash_file "EXTENSIONS_PRODUCTION" \
    "$PROD/v0/extensions.conf"

hash_file "EXTENSIONS_EDIT" \
    "$PROD/v0-edit/extensions.conf"

hash_file "PJSIP_CONFIG" \
    "$PROD/v0/pjsip.conf"

if [ -f "$PROD/v0/extensions.conf" ] &&
   [ -f "$PROD/v0-edit/extensions.conf" ]; then
    if cmp -s \
      "$PROD/v0/extensions.conf" \
      "$PROD/v0-edit/extensions.conf"; then
        echo "EXTENSIONS_V0_V0EDIT_SYNC=YES"
    else
        echo "EXTENSIONS_V0_V0EDIT_SYNC=NO"
    fi
fi

section "7. SMS Multi-Contact V2"

hash_file "SMS_CONSUMER_V2" \
    /usr/local/sbin/qdc507-sms-queue-deliver-pinned.py \
    e36a9e27d9361c32978a72b19cb09a8e6551ce9b1226628305cacd431a1976ae

hash_file "SMS_TICK_V2" \
    /usr/local/sbin/qdc507-sms-tick.py \
    3057d48056c820874624740a1fb77700bac83b947e96876597ea2fe75c82f694

hash_file "SMS_V2_BACKUP" \
    "$LAB/qdc507-sms-consumer-v2-recovery.py" \
    e36a9e27d9361c32978a72b19cb09a8e6551ce9b1226628305cacd431a1976ae

hash_file "SMS_TICK_BACKUP" \
    "$LAB/qdc507-sms-tick-v2-recovery.py" \
    3057d48056c820874624740a1fb77700bac83b947e96876597ea2fe75c82f694

echo "--- Pinned SHA256 ---"

grep -E '^PIN=|^DOCKER_PIN=' \
    /usr/local/sbin/qdc507-sms-tick.py 2>/dev/null

echo "--- SMS timer ---"

systemctl is-active qdc507-sms-tick.timer 2>&1

echo "--- SMS queue ---"

Q="$PROD/v2/sms-queue"

for D in pending delivered; do
    if [ -d "$Q/$D" ]; then
        if [ "$D" = pending ]; then
            N=$(find "$Q/$D" -maxdepth 1 -name '*.body' | wc -l)
        else
            N=$(find "$Q/$D" -mindepth 1 -maxdepth 1 -type d | wc -l)
        fi
        echo "SMS_${D}_COUNT=$N"
    fi
done

echo "--- SMS last tick ---"

if [ -f /var/lib/qdc507-sms-queue-tick-last.txt ]; then
    grep -E '^(TIME|STATUS|REASON|PENDING_BEFORE|PENDING_AFTER|CONSUMER_EXIT)=' \
      /var/lib/qdc507-sms-queue-tick-last.txt
fi

section "8. Recovery / Boot"

hash_file "COLDBOOT_V3" \
    "$REC/qdc507-coldboot-production-v3.py"

hash_file "USB_RECOVER" \
    "$REC/qdc507-usb-recover.sh"

hash_file "USB_RECOVERY_V2" \
    "$REC/qdc507-usb-manual-recovery-v2.py"

hash_file "DYNUSB_ONECLICK_V2" \
    "$LAB/qdc507-dynusb-oneclick-v2.py"


section "8.1. Official Recovery Entry Baseline"

recovery_issues=0

recovery_pin() {
    label="$1"
    file="$2"
    expected="$3"

    if [ ! -f "$file" ]; then
        echo "[MISSING] $label"
        recovery_issues=$((recovery_issues + 1))
        return
    fi

    actual=$(sha256sum "$file" 2>/dev/null)
    actual=${actual%% *}

    if [ "$actual" = "$expected" ]; then
        echo "[OK] $label"
    else
        echo "[CHANGED] $label"
        echo "  ACTUAL=$actual"
        echo "  EXPECTED=$expected"
        recovery_issues=$((recovery_issues + 1))
    fi
}

# V43 changes the manual USB/SIM recovery entrypoint by adding an isolated
# device-v43.sh restage hook.  The legacy v42 SHA is no longer the live SHA.
# Do NOT simply disable its pin. Verify the exact pinned v43 release code,
# the original v42 USB backup, the derived v43 wrapper, the release receipt,
# and the production file, without calling ADB or changing any file.
if /usr/bin/python3.8 -I -B - "$LAB" "$REC/qdc507-usb-recover.sh" <<'PY_AUDIT_USB_V43'
import hashlib, importlib.util, json, pathlib, sys
lab=pathlib.Path(sys.argv[1]); active=pathlib.Path(sys.argv[2])
release=lab/'qdc507_v43_release_final.py'
back=lab/'qdc507-v43-v42-usb-recover.private.bak'
stage=lab/'qdc507-v43-usb-recover.staged.sh'
receipt=lab/'qdc507-v43-release.applied.json'
pending=lab/'qdc507-v43-release.pending.json'
PIN_RELEASE='e59f754afa628cd050a8c85be15c936f9f69a07020550fc4ce1c6b49df49c486'
PIN_USB_V42='f819ee16242ff58ce8ab975584e184a7f1587237c7a396c9c82b93e9df41d7d5'
PIN_DP_V43='0f232e3c356922392169a099a966b39b93c659136bdc424c27f963cae1f386e3'
def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):
    if not p.is_file() or p.is_symlink():raise ValueError('MISSING_OR_SYMLINK_'+p.name)
    return p.read_bytes()
try:
    assert not pending.exists() and not pending.is_symlink(), 'V43_PENDING_REQUIRES_REVIEW'
    assert sha(read(release))==PIN_RELEASE, 'V43_RELEASE_SCRIPT_DRIFT'
    old=read(back)
    assert sha(old)==PIN_USB_V42, 'V42_USB_BACKUP_DRIFT'
    j=json.loads(read(receipt).decode('utf-8'))
    assert j.get('phase')=='applied' and j.get('container')=='qdc507-hardware', 'RELEASE_NOT_APPLIED'
    assert j.get('new_dialplan_sha256')==PIN_DP_V43, 'RECEIPT_DIALPLAN_DRIFT'
    assert j.get('old_usb_sha256')==PIN_USB_V42, 'RECEIPT_OLD_USB_DRIFT'
    spec=importlib.util.spec_from_file_location('pinned_qdc507_v43_release',str(release))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # module definitions only; main() is not invoked
    predicted=module.usb_derivative(old)  # validates exact change + shell/Python syntax, read-only
    expected=sha(predicted)
    assert j.get('new_usb_sha256')==expected, 'RECEIPT_USB_HASH_DRIFT'
    assert read(stage)==predicted, 'USB_STAGED_NOT_EXACT_DERIVATIVE'
    assert read(active)==predicted, 'USB_ACTIVE_NOT_EXACT_DERIVATIVE'
    print('[OK] HOT_RECOVERY_ENTRY_V43')
    print('  SHA256='+expected)
    print('V43_USB_RECOVERY_PIN_AND_DERIVATION_OK=1')
except Exception as e:
    print('[CHANGED] HOT_RECOVERY_ENTRY_V43')
    print('  REASON='+str(e)[:200])
    sys.exit(2)
PY_AUDIT_USB_V43
then
    :
else
    recovery_issues=$((recovery_issues + 1))
fi

recovery_pin "COLDBOOT_V3_ENTRY" \
    "$REC/qdc507-coldboot-production-v3.py" \
    "a6509d867269e401ec3c17c2fcb609189c7c8f356bdd5259225eb69eac70cbc0"

recovery_pin "DYNUSB_ONECLICK_V2" \
    "$LAB/qdc507-dynusb-oneclick-v2.py" \
    "d3817c52f19e6f50d03605ee4e478c07e022b442091f0b90d64f52d6a2a4160c"

recovery_pin "GUARD16_V2_RESTORE" \
    "$LAB/qdc507-guard16-v2-runtime-restore-v2.py" \
    "077056a8680329c998ed48a29a0a873a3c83b4e1de8e1f7becd55e0084afce55"

recovery_pin "USB_OPTION_DRIVER_FILE" \
    /usr/local/libexec/qdc507/boot/option.ko \
    "395918cff6c00e82cd8fbdfd99b8b2420ac9089605f6df0cc8a80550fd6d98cc"

if [ "$recovery_issues" -eq 0 ]; then
    echo "RECOVERY_FILES_BASELINE=PASS"
else
    echo "RECOVERY_FILES_BASELINE=REVIEW_REQUIRED"
    echo "RECOVERY_FILE_ISSUES=$recovery_issues"
fi

echo "HOT_RECOVERY_ENTRY=USB_RECOVER_SH"
echo "COLD_BOOT_ENTRY=COLDBOOT_PRODUCTION_V3"
echo "HOT_RECOVERY_FUNCTIONAL_TEST=NOT_RUN"
echo "COLD_BOOT_FUNCTIONAL_TEST=NOT_RUN"
echo "NO_RECOVERY_ACTION_EXECUTED=1"

section "8.5. Inbound Answer Optimization V43 (V42 Fallback Retained)"

v43_fail=0

v43_host_hash() {
    label="$1"
    file="$2"
    expected="$3"

    result=$(docker exec "$C" sha256sum \
        "/opt/qdc16k-preaudio-manual/$file" 2>/dev/null)

    if [ $? -ne 0 ]; then
        echo "[MISSING] $label"
        v43_fail=$((v43_fail + 1))
        return
    fi

    actual=${result%% *}
    if [ "$actual" = "$expected" ]; then
        echo "[OK] $label"
    else
        echo "[CHANGED] $label"
        echo "  EXPECTED=$expected"
        v43_fail=$((v43_fail + 1))
    fi
    echo "  SHA256=$actual"
}

v43_local_hash() {
    label="$1"
    file="$2"
    expected="$3"
    if [ ! -f "$file" ] || [ -L "$file" ]; then
        echo "[MISSING] $label"
        v43_fail=$((v43_fail + 1))
        return
    fi
    actual=$(sha256sum "$file" 2>/dev/null)
    actual=${actual%% *}
    if [ "$actual" = "$expected" ]; then
        echo "[OK] $label"
    else
        echo "[CHANGED] $label"
        echo "  ACTUAL=$actual"
        echo "  EXPECTED=$expected"
        v43_fail=$((v43_fail + 1))
    fi
}

# Production v43; the original v42 controller and wrapper remain pinned
# for rollback. Outbound routes continue using preaudio.py and agi-wrapper.
v43_host_hash "INBOUND_V43_CONTROLLER" \
    "preaudio-inbound-v43.py" \
    "13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf"

v43_host_hash "INBOUND_V42_FALLBACK_CONTROLLER" \
    "preaudio-inbound-v42.py" \
    "7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7"

v43_host_hash "ORIGINAL_OUTBOUND_CONTROLLER" \
    "preaudio.py" \
    "2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690"

v43_host_hash "ORIGINAL_DEVICE_V42_FALLBACK" \
    "device.sh" \
    "3682a8f0b4160f43778624e8551f39f8390163c92850f6661980ca64038d5f76"

v43_host_hash "PREAUDIO_PREPARE_UNCHANGED" \
    "prepare.py" \
    "1332d6fcf5a4b3f4f7a70180e8632992e30325256b415a4f8fc6417cf7bc0665"

v43_host_hash "INBOUND_V43_AGI_WRAPPER" \
    "agi-wrapper-inbound-v43" \
    "472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98"

v43_host_hash "INBOUND_V42_FALLBACK_AGI_WRAPPER" \
    "agi-wrapper-inbound-v42" \
    "3304ad984d48f67d91e473891c33e70f126c231351fe7d9f0a61e4e798e56b09"

v43_host_hash "ORIGINAL_OUTBOUND_AGI_WRAPPER" \
    "agi-wrapper" \
    "592d3fa1f3a030bd9c928469acbbe70e8904563639534dc97cdc97d68eab7680"

v43_host_hash "V43_DEVICE_RESTORER" \
    "qdc507-v43-device-restore.py" \
    "c85fbadc6c152729dfff1ff3aabbaac0ae8698cfe9469a120754cd36302aff7d"

# Container entrypoint is outside /opt/qdc16k-preaudio-manual.
entry_result=$(docker exec "$C" sha256sum /usr/local/bin/entrypoint.sh 2>/dev/null)
entry_actual=${entry_result%% *}
if [ "$entry_actual" = "53b2c8a678f7b5b61a2eb25d268810ef5d6a73947f60451db81b336d3138ae18" ]; then
    echo "[OK] V43_BOOT_ENTRYPOINT"
else
    echo "[CHANGED] V43_BOOT_ENTRYPOINT"
    echo "  SHA256=$entry_actual"
    v43_fail=$((v43_fail + 1))
fi

v43_local_hash "V43_DIALPLAN_STAGED" \
    "$LAB/qdc507-v43-extensions.staged.conf" \
    "0f232e3c356922392169a099a966b39b93c659136bdc424c27f963cae1f386e3"

v43_local_hash "V43_RELEASE_SCRIPT" \
    "$LAB/qdc507_v43_release_final.py" \
    "e59f754afa628cd050a8c85be15c936f9f69a07020550fc4ce1c6b49df49c486"

v43_local_hash "V43_STAGED_DEVICE_SOURCE" \
    "$LAB/qdc507-device-v43-candidate.sh" \
    "ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1"

v43_local_hash "V43_STAGED_HOST_CONTROLLER" \
    "$LAB/qdc507-inbound-v43-candidate.py" \
    "13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf"

v43_check() {
    label="$1"
    body="$2"
    pattern="$3"
    if printf '%s\n' "$body" | grep -Fq "$pattern"; then
        echo "[OK] $label"
    else
        echo "[MISSING] $label"
        v43_fail=$((v43_fail + 1))
    fi
}

v43_check_count() {
    label="$1"
    body="$2"
    pattern="$3"
    expected="$4"
    n=$(printf '%s\n' "$body" | grep -Fo "$pattern" | wc -l)
    n=$(echo "$n" | tr -d ' ')
    if [ "$n" = "$expected" ]; then
        echo "[OK] $label COUNT=$n"
    else
        echo "[CHANGED] $label COUNT=$n EXPECTED=$expected"
        v43_fail=$((v43_fail + 1))
    fi
}

v43_plan=$(docker exec "$C" \
    asterisk -rx "dialplan show incoming-mobile" 2>/dev/null)

# Compatibility: context names remain *v42* but their AGI actions use v43.
v43_wait=$(docker exec "$C" \
    asterisk -rx "dialplan show qdc16k-inbound-v42-wait" 2>/dev/null)

v43_cancel=$(docker exec "$C" \
    asterisk -rx "dialplan show qdc16k-inbound-v42-cancel" 2>/dev/null)

v43_check_count "INBOUND_V43_SUBMIT" "$v43_plan" \
    'agi-wrapper-inbound-v43,submit,${QDC_PA_ID}' 2

v43_check_count "INBOUND_V43_WAIT" "$v43_plan" \
    'agi-wrapper-inbound-v43,wait,${QDC_PA_ID}' 2

# The original inbound v42 generator (preserved by the v43 wrapper-only
# dialplan patch) installs TWO independent cleanup paths in EACH inbound
# route: a hangup handler and a post-Dial explicit Gosub. Do not count the
# bare context name: that appears 4 times across the two routes.
v43_check_count "INBOUND_V43_HANGUP_CANCEL_HANDLER" "$v43_plan" \
    'Set(CHANNEL(hangup_handler_push)=qdc16k-inbound-v42-cancel,s,1(${QDC_PA_ID}))' 2
v43_check_count "INBOUND_V43_POST_DIAL_CANCEL_GOSUB" "$v43_plan" \
    'Gosub(qdc16k-inbound-v42-cancel,s,1(${QDC_PA_ID}))' 2

v43_check_count "LOCAL_CLOUD_ROUTE_SELECTION" "$v43_plan" \
    '100@cloud-trunk:100' 4

v43_check "V43_WAIT_CONTEXT" "$v43_wait" \
    'agi-wrapper-inbound-v43,wait,${ARG1}'

v43_check "V43_CANCEL_CONTEXT" "$v43_cancel" \
    'agi-wrapper-inbound-v43,cancel,${ARG1}'

all_routes=$(docker exec "$C" asterisk -rx "dialplan show" 2>/dev/null)
v43_check "ORIGINAL_OUTBOUND_7_PREFIX" "$all_routes" "'_7X.' =>"
v43_check "ORIGINAL_OUTBOUND_QUECTEL_DIAL" "$all_routes" 'Dial(Quectel/quectel0/'
v43_check "ORIGINAL_OUTBOUND_AGI" "$all_routes" 'agi-wrapper,submit,'

# The pinned v43 recovery audit checks the device v43 SHA, owner/lock,
# actual Guard16 v2, the USB/boot hooks and exact source provenance.
# Those checks require an IDLE modem. Skipping during a live call is not PASS.
audit_py="$LAB/qdc507-inbound-v42-recovery-audit-v1.py"
audit_py_pin="9d3fecd1670aeba893f65bbce860b23b9fcc8d76a87a446929e73206083e74e4"
can_deep_check=0
calls=$(docker exec "$C" asterisk -rx "core show channels count" 2>/dev/null)
if printf '%s\n' "$calls" | grep -Eq '^0 active channels[[:space:]]*$' && \
   printf '%s\n' "$calls" | grep -Eq '^0 active calls[[:space:]]*$'; then
    can_deep_check=1
fi

if [ ! -f "$audit_py" ] || [ -L "$audit_py" ]; then
    echo "[MISSING] V43_DEEP_AUDIT_SCRIPT"
    v43_fail=$((v43_fail + 1))
else
    digest=$(sha256sum "$audit_py" 2>/dev/null)
    digest=${digest%% *}
    if [ "$digest" != "$audit_py_pin" ]; then
        echo "[CHANGED] V43_DEEP_AUDIT_SCRIPT"
        echo "  EXPECTED=$audit_py_pin"
        echo "  ACTUAL=$digest"
        v43_fail=$((v43_fail + 1))
    elif [ "$can_deep_check" -eq 0 ]; then
        echo "V43_DEEP_AUDIT=DEFERRED_ACTIVE_OR_UNKNOWN_CALLS"
    else
        result=$(/usr/bin/python3.8 -I -B "$audit_py" --check 2>&1)
        status=$?
        printf '%s\n' "$result" | grep -E \
          '^(V43_|V42_FALLBACK_|CLOUD_LOCAL_|DEVICE_|CLEAN_TOKEN_|LOGS_|QDC507_INBOUND_|READ_ONLY_|.*AUDIT_STOP=)' \
          || :
        if [ "$status" -ne 0 ] || \
           ! printf '%s\n' "$result" | grep -Fq 'QDC507_INBOUND_V43_RECOVERY_AUDIT_PASSED=1'; then
            echo "[CHANGED] V43_DEEP_AUDIT_FAILED exit=$status"
            printf '%s\n' "$result" | tail -5
            v43_fail=$((v43_fail + 1))
        else
            echo "V43_DEEP_AUDIT=PASS"
        fi
    fi
fi

echo "STK_DEPLOYMENT=NOT_DEPLOYED_EXPECTED"

if [ "$v43_fail" -ne 0 ]; then
    echo "INBOUND_V43_AUDIT=REVIEW_REQUIRED"
    echo "INBOUND_V43_ISSUES=$v43_fail"
elif [ "$can_deep_check" -eq 0 ]; then
    echo "INBOUND_V43_STATIC_AUDIT=PASS"
    echo "INBOUND_V43_AUDIT=PARTIAL_DEVICE_BUSY_OR_UNKNOWN"
else
    echo "INBOUND_V43_AUDIT=PASS"
fi

# This aggregate concerns the strict versioned recovery + inbound baseline.
# Existing informational sections 1-8 continue to report their own statuses.
if [ "$recovery_issues" -ne 0 ] || [ "$v43_fail" -ne 0 ]; then
    echo "V43_UPGRADE_AUDIT=REVIEW_REQUIRED"
elif [ "$can_deep_check" -eq 0 ]; then
    echo "V43_UPGRADE_AUDIT=PARTIAL_DEVICE_BUSY_OR_UNKNOWN"
else
    echo "V43_UPGRADE_AUDIT=PASS"
fi

section "9. Version Audit Complete"

echo "AUDIT_READ_ONLY=1"
echo "AUDIT_COMPLETE=1"
echo "NOTE=UNKNOWN_HASH_MEANS_VERSION_FINGERPRINT_ONLY"
echo "NOTE=STK_SIGNATURE_DOES_NOT_PROVE_RUNTIME_FUNCTION"
echo "NOTE=SMS_DELIVERED_DOES_NOT_PROVE_PHONE_RECEIPT"
echo "NOTE=V43_DEEP_AUDIT_REQUIRES_IDLE_DEVICE"
echo "NOTE=HOT_RECOVERY_ENTRY_V43_PIN_FROM_EXACT_RELEASE_DERIVATION"
