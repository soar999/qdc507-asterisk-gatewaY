# QDC507 DSM 7.2.2 Update 10 — public review checkpoint (2026-11-21)

**Status: source archive for review — not deployable.** Captured from a NAS review ZIP on 2026-11-21 after the successful ACL600 patch. The source files are *publicly redacted copies*, **not** a replacement for the matching pinned production versions. Do not copy them onto a live NAS without restoring private, locally trusted values and rebuilding all interlocking SHA256 pins.

## Runtime setting

- Synology DS918+ upgraded to DSM **7.2.2-72806 Update 10**, kernel **4.4.302+**.
- The existing `qdc507-hardware` production container is preserved. The working entry point for USB/SIM swap is `qdc507-current/recovery/qdc507-usb-recover.sh`, which dispatches to MemoryFix V22 in `qdc507-16k-lab`.
- DSM722 adaptation involves QDC507 USB serial kernel modules, device-node/ADB/USB-audio restoration, boot-prep, SMS tick and a bounded V221 coldboot workflow. Driver build provenance is not fully archived here.
- Option USB interface guard source/patch for vendor/product `2ca3:4006` binds serial IF0–IF4 only and rejects non-serial IF5–IF8. Device vendor/product ID is hardware-type metadata, not a SIM identifier.

## Accepted live source chain (user-provided NAS audit)

| Component | Repo archive file | Original NAS SHA256 (prefix) |
|---|---|---|
| USB/SIM swap | `source/lab/qdc507-usb-recover-memoryfix-v22.sh` | `a86b87398220` |
| V221 coldboot core | `source/lab/qdc507-coldboot-memoryfix-v221.py` | `cb12e806767e` |
| V221 startup | `source/lab/qdc507-coldboot-startup-v221.py` | `4c88ca59502c` |
| V22 runtime audit | `source/lab/qdc507-memoryfix-v22-audit.py` | `3eae922542c7` |
| Aggregate audit | `source/lab/qdc507-patch-audit-v3.sh` | `70513e059ef2` |
| Production recovery shell | `source/production-recovery/qdc507-usb-recover.sh` | `fb4025303fa5` |

`source-manifest.json` gives the full original NAS, NAS-export and publication SHA256 digest for each of the 52 source files. The public copies have distinct SHA256 values when instance IDs or other identifiers were replaced.

## Nov 20–21 changes recorded

1. DSM 7.2.2 kernel upgrade compatibility; staging/guarded boot preparation and production host-sbin scripts.
2. QDC507 option driver C source and IF0–IF4 interface guard patch (`source/lab/qdc507-option-dsm722-if0to4-guard-v1.{c,patch}`). The C source contains an upstream GPLv2 notice. No compiled `.ko` is published.
3. MemoryFix V22 OneClick, USB/SIM-swap recovery; retirement of the old hotplug coordinator while preserving the stable recovery entrypoint.
4. Guard16 V2 USB-generation synchronization on hot recovery and V221 coldboot.
5. DSM inherited ACL mitigation: explicit `fchmod(fd, 0o600)` on newly created private baseline and backup files; pin updates propagated through current release manifest, V22 audit, V221 startup and aggregate audit.
6. ADB V2.4 watchdog, V43 voice component checks, SMS worker idle gate, DSM722 SMS tick/boot-prep changes.

## Reported 2026-11-21 acceptance

```
ACL600_SINGLE_DEEP_AUDIT=PASS
V221_STARTUP_STATIC_SOURCE_CHECK=PASS
ACL600_PATCH_AND_READONLY_AUDITS=PASS
NO_RECOVER_NO_BOOT_NO_SERVICE_RESTART=1
ACL600_INSTALLATION_SUCCESS=1
CURRENT_PRODUCTION_AUDIT=PASS
ADB_WATCH_AUDIT=PASS
```

The audit still reported `USB_SIM_SWAP_AFTER_COORDINATOR_RETIREMENT_NOT_LIVE_TESTED=1` and `COLDBOOT_AFTER_COORDINATOR_RETIREMENT_NOT_LIVE_TESTED=1`. These should not be interpreted as successful live-cycle tests of the final patched sources.

## Production vs candidates

- **V221** is the accepted deployed coldboot version based on supplied hashes/audit.
- **V222** and `qdc507-dsm74-boot-v1/v2.py` are experimental/staged artifacts, not evidence that production uses them.
- Files with `.candidate`, `.before-`, `-staged`, `-stage` and `-installer` in their names are intermediate development/rollback evidence. They must not supersede active sources.
- See `publication-review.md` for incomplete capture and security limits.

## Restore / deployment warning

This checkpoint is a historical and diagnostic resource only. It is **not an executable drop-in backup**: it lacks live configuration, encrypted/private state, source dependencies, built modules, task scheduler configuration, and secrets, and its redacted copies deliberately fail exact source hash pins. Build and deployment work need a separate controlled checklist on a matching NAS.
