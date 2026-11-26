# Acceptance evidence — 2026-11-26

The following markers are transcribed from user-supplied NAS terminal output, not independently executed by the archive reviewer.

## Device reboot and USB re-enumeration

```text
USB_GENERATION_CHANGED=1
QDC507_REBOOT_TEST_PASS=1
ADB_REDISCOVERED_AFTER_USB_SWAP=1
DYNUSB_ONECLICK_RECOVERY_OK=1
CONTAINER_ID_UNCHANGED=1
SMS_TIMER=active
GUARD16_V2_RUNTIME_RESTORED=1
V43_POST_USB_RESTAGE_OK=1
USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1
V44_RESTAGE=RESTAGED_VERIFIED
V44_EVENT_RESTAGE=PASS
READ_ONLY_RUNTIME_CHECK_OK=1
V44_RECOVERY_READY=1
```

13:13:32–13:13:50 local: outbound call bridged on answer, both channels left the bridge at hangup, cleanup=1. The device returned Free. READY epoch mode 0640 owner root:1000. ADB monitoring resumed; the SMS timer was active (not a separate send/receive test).

## ADB reset preflight

```text
ADB_RESET_CORE_PIN=PASS
V2_RUNTIME_PIN_AND_IDLE_GUARDS_OK=1
ADB_HEALTHY=1
RESET_STATIC_GUARDS_OK=1
RESET_WOULD_BE_NEEDED=0
ADB_RECOVERY_PRECHECK=PASS
```

## Production audit

```text
PATCH_AUDIT_VERSION=20261126_V44_EVENT_PRODUCTION
V44_EVENT_STATIC_PINS=PASS
V44_ACTIVE_ENTRIES=PASS
V44_COORDINATION_AND_EPOCH=PASS
V44_CONTAINER_COMPONENTS=PASS
V44_EVENT_DEVICE_HASH=PASS
V44_ACTIVATION_JOURNAL=PASS
V22_DEEP_AUDIT_PASSED=1
V44_EVENT_PRODUCTION_AUDIT=PASS
ADB_WATCH_PROCESS_COUNT=1
ADB_WATCH_AUDIT=PASS
CURRENT_PRODUCTION_AUDIT=PASS
AUDIT_READ_ONLY=1
V44_EVENT_AUDIT_INSTALL_COMPLETE=1
NO_RUNTIME_CHANGE=1
NO_CONTAINER_RESTART=1
NO_USB_RESET=1
```

Pending: physical USB power cycle, live automatic ADB Reset, actual NAS coldboot and boot task configuration. Do not interpret read-only checks as live recovery tests. No raw call/SMS logs or device identifiers published.
