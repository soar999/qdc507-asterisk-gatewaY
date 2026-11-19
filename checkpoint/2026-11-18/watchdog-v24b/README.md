# QDC507 Watchdog v2.4b (2026-11-18)

Source-only recovery watchdog follow-up. No binaries, runtime state, private
configuration, USB reset journal, SMS or raw Asterisk logs are included.

## What happened

On 2026-11-18 the QDC507 ADB connection became inaccessible twice while the
QDC507 Asterisk AT device continued reporting `Free`. The guarded reset helper
successfully recovered ADB and verified Guard16 v2 in the second recovery.
Watchdog v2.3 detected both failures but its second automatic attempt was
blocked by the existing 30-minute cooldown; the watchdog did not reschedule
a retry when that cooldown expired.

## Changes

- Correct the saved one-shot reset state for both known v2.3 spelling variants.
- Persist a single retry deadline only on explicit
  `RESET_COOLDOWN_ACTIVE_NO_RETRY` from the existing helper.
- Recheck ADB and the existing guarded reset helper at retry time; do not bypass
  its USB identity, active-call checks, cooldown or exclusive lock.
- Preserve one-shot behavior for every non-cooldown failure.
- Avoid changing the QDC507 driver, Guard16, cold-boot, SMS timer or SIM7600CE.

`qdc507-watchdog-v24b-stage.py` generates a separate candidate from the
on-device v2.3 source. `qdc507-watchdog-v24b-activate.sh` stages, checks,
backs up and switches only the monitor process. They are site-specific source
references, not portable installers; review before executing.

## Validation and limitations

- Offline state-machine and syntax tests passed on 2026-11-18.
- On-device activation reported `V24B_ACTIVATED=1`; new monitor PID started
  and logged `WATCH_V2_4_AUTO_RECOVERY_STARTED` with `SHELL_OK`.
- Later read-only helper check returned `ADB_HEALTHY=1` and
  `RESET_WOULD_BE_NEEDED=0`.
- **Cooldown-expiry retry has not yet been validated by a real failed ADB case.**
- A true NAS cold boot with the latest recovery chain remains untested.
- Live guarded reset helper `qdc507-adb-recover-once-memoryfix-v2.py` is a
  runtime prerequisite and is **not included in this snapshot**. Review and
  sanitize it separately before publication.
- The other QDC507 recovery/driver files here are a *sanitized* source checkpoint,
  not a verified independently installable release. See `../SANITIZATION.md`.

Potential hardware interactions with another cellular USB module have not been
proven. Both devices were on the same USB hub, but no definitive kernel fault
was recovered for the relevant time window.
