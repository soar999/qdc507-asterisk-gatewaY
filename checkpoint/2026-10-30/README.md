# QDC507 recovery checkpoint — 2026-10-30

This source-only checkpoint records the recovery work through 2026-10-29.
It does **not** install, launch, or change any services. It deliberately
preserves `checkpoint/2026-10-26/` in the existing repository.

## Included updates

- **USB unplug/SIM swap**: `qdc507-usb-recover-v2.sh` and
  `qdc507-usb-wrapper-install-v2.py` define the guarded manual entrypoint and
  the root-only, atomic replacement/rollback of the installed shell wrapper.
  The installed NAS entrypoint is
  `/volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh`.
  It accepts `--check` (read-only) and `--recover` (one guarded attempt).
- **Guard16 v2**: `qdc507-guard16-v2-runtime-restore-v2.py` checks and
  restores the pinned v2 A/B audio bridge while retaining the v1 fallback;
  `qdc507-guard16-v2-promotion-prep.py` is the staging/audit helper.
- **Production coldboot**: `qdc507-coldboot-production-v3-guard16-v2.py`
  is the 2026-10-28 v3 source with its post-boot Guard16 v2 hook.
  `qdc507-coldboot-production-v3.original.py` is the prior exact input,
  retained for reproducibility; installer/patcher sources are included.
- **Compatibility fixes**: `qdc507-preaudio-guard16-v2-allowlist-v1.py`
  permits the two explicitly pinned Guard16 hashes, while
  `qdc507-guard16-v2-recovery-pin-fix-v2.py` updates the recovery guard
  checker. These are source snapshots, not auto-install commands.

## Operational scope and verification

- The 2026-10-29 USB/SIM swap has been observed to restore voice, SMS and
  Guard16 v2, but a complete unattended end-to-end success-marker audit of
  the revised top-level USB wrapper has **not** been established here.
- A **new unattended NAS coldboot** after all fixes has **not** been
  retested; do not claim coldboot fully validated.
- Scripts contain hardcoded installation paths, source SHA256 pins,
  the original Docker container/image identifiers, and recovery assumptions.
  They are **not** generic recovery software and must not be deployed to
  another device or after recreating the production container without an
  explicit, independent pin review.
- USB manual recovery and NAS coldboot are different entrypoints.
  Do not run either simply to test GitHub publishing.
- The new source manifest records exact source bytes and hashes.
  No runtime state JSON, SIM details, passwords, SMS logs, ADB keys, or
  private backup binaries are included.
- `qdc507-preaudio-guard16-v2-allowlist-v1.py` patches the running
  container's writable layer, so recreating that container loses the fix.

## Recovery entrypoints (NAS; only when actually needed)

USB/SIM manual recovery:

```sh
sudo /bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh --recover
```

Read-only USB state check:

```sh
sudo /bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh --check
```

NAS startup task (separate root task, **not** USB manual entrypoint):

```sh
/usr/bin/python3.8 -I -B /volume2/docker/asterisk/qdc507-current/recovery/qdc507-coldboot-production-v3.py --boot
```

Always stop and inspect pending journals on any ambiguous failure. Never
blindly delete journal files or repeat writes.
