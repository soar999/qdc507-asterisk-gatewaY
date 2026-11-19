# QDC507 checkpoint — 2026-11-18

## Scope

* QDC507 driver memoryfix v2: monitor_thread.c / chan_quectel.c; cpvt.c returned to baseline. Verify driver source provenance and hashes before publishing.
* USB dynamic recovery memoryfix v2.2, ADB Watchdog memoryfix v2, coldboot memoryfix v2.2, staged v2.2.1 coldboot entry.
* Existing production hot-recovery entry remains `/bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh --recover`.
* Existing production audit entry remains unchanged. No production config has been exported.
* SIM7600CE-T is an independent experiment, not part of the QDC507 production chain. Original upstream baseline was `AlchemillaHQ/asterisk-chan-quectel` commit `e1c027750bdf878bfe552341de457a9245358ce1`. The optional upstream Dockerfile is illustrative only.

## Evidence and limits

* Staged v2.2.1: `V221_OFFLINE_ENTRY_SELFTEST=PASS`, `V221_BOOT_ENTRY_READ_ONLY_CHECK=PASS`, `V22_DEEP_AUDIT_PASSED=1`.
* Production watchdog seen running: `qdc507-adb-watch-alert-memoryfix-v2.py --watch`, emitting `WATCH_V2_3_AUTO_RECOVERY_STARTED`.
* Coldboot and real USB recovery have NOT been live tested after v2.2.1 staging.
* DSM Boot-up task is not proven installed or verified by this snapshot.
* SIM7600CE-T experimentation observed `EXIT=139` segfault with chan_quectel.so in one phase; 16kHz PCM capture exists but listening/bidirectional validation was pending. This is not a production-ready SIM7600 release.
* Memoryfix v2 production module was reported with SHA-256 `c5b940d5505fc40d9c2815674b1107586b39a0fba5fe0e0dce060c111a680bf0`; collect independent hash evidence before treating this as verified.
* This archive is a source review checkpoint, NOT an installable recovery package or verified release.

## Security

Only explicitly allowlisted source files are collected. Never include SIP passwords,
real pjsip/extensions config, private keys, IMEI/IMSI/ICCID, SMS bodies/queues,
packet captures, raw PCM, production device-state files, logs, or Docker volumes.
The automated scanner is NOT a substitute for manual inspection before push.

## Reproduce

Record exact upstream base commit, check the actual candidate driver source,
compare SHA-256 and read the diff, then rerun isolated tests and a controlled
coldboot before claiming end-to-end startup functionality.
