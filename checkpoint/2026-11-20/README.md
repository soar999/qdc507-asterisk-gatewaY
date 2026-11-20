# QDC507 — production recovery checkpoint (2026-11-20)

## Scope

- A stable hot-recovery scheduler entry now forwards to MemoryFix V2.2; the scheduled command path is unchanged.
- V2.2 USB/SIM recovery, the 40-second bounded SMS-worker idle check, and guarded synchronization of the Guard16 V2 USB-generation baseline are captured.
- The current production-only V2.2 audit replaces the retired V4/V2.1 hot-recovery checks in the summary audit.
- Existing V43 voice-component hash checks are retained in the summary audit.

## Verification evidence

User-supplied NAS output on 2026-11-20:
- `V22_READ_ONLY_AUDIT_PASS=1`
- `ONECLICK_AND_GUARD16_SEALED_PINS=PASS`
- `OFFICIAL_MEMORYFIX_V22_ENTRY=PASS`
- `GUARD16_V2_AND_USB_BASELINES=PASS`
- `PRODUCTION_CONTAINER_AND_SMS=PASS`
- `V43_INBOUND_CONTROLLER=PASS`
- `V43_INBOUND_AGI=PASS`
- `V43_DEVICE_RESTORER=PASS`
- `CURRENT_PRODUCTION_AUDIT=PASS`

The recovery code passed independent syntax checks. The new Guard16 baseline-sync helper passed isolated tests for generation changes, idempotence, mismatched context, and Guard16 pin mismatch. These are **synthetic tests**, not a further physical hotplug trial.

## Limits

A manual recovery after the powered USB-hub change was observed. The newly added *automatic Guard16 baseline-sync path* has **not** been exercised in a second full physical USB hotplug/cold-boot cycle. Future hardware recovery is not guaranteed.

**This public snapshot is not an installable or independently runnable package.** The NAS audits reference the original production layout, private local baseline and release JSON files, and other local helper scripts not exported here. The recovered source is intended for review/history only.

## Publication safety

- Public copies of the recovery script substitute placeholders for the literal production Docker container ID and image ID.
- `source-manifest.json` records the **original NAS SHA-256** separately from the **published sanitized SHA-256**.
- The original production files on the NAS have **not** been changed by sanitization.
- Do not commit private baselines, SIM identifiers, SIP secrets, messages, call recordings, logs, packet captures, or Docker volumes.

The original 2026-11-18 checkpoint remains a separate historical snapshot.
