# Guard16 v2: build artifacts, verification and NAS recovery

This document supplements, but does not edit, the QDC507 recovery checkpoint
from 2026-10-30. It does not install or change any runtime service.

## Public artifacts and provenance

- [Build inputs and exact ARMv7 binary](../build/guard16-v2/README.md)
- [v1/v2 sources and patch](../build/guard16-v2/)
- [Recovery implementation checkpoint](../checkpoint/2026-10-30/recovery/)
- [USB/coldboot integration notes](../checkpoint/2026-10-30/README.md)

The **D5 bridge binary** is one component of the voice pathway. This GitHub
publication includes the v2 reference executable with pinned SHA256 but does
not include the private v1 binary, platform-specific SIM information, optional
vendor audio runtime `libql_lib_audio.so.1`, module firmware, private boot
policies, production Docker writable layer, or runtime restoration manifests.

## Guard16 A/B layout and checksums

Reference NAS path for helper scripts (no new files deployed by this document):

```
/volume2/docker/asterisk/qdc507-16k-lab/
```

| Component | Path | Expected SHA256 |
| --- | --- | --- |
| v1 retained NAS binary (private) | `qdc507-guard16-v1-clock8-backup.armv7` | `7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71` |
| v2 retained NAS binary (private copy) | `qdc507-guard16-v2-candidate.armv7` | `0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913` |
| v2 public **reference** executable | `build/guard16-v2/reference/bridge-d5-v2.armv7` | `0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913` |

The device-side paths (volatile across USB reset/reboot) are:

```
/tmp/qdc507-serial16k-v7/bridge-d5-clock8-guard16       # v1 staged executable
/tmp/qdc507-guard16-v2-lab/bridge-d5-v2               # v2 staged executable
/tmp/qdc507-serial16k-v7/bridge-d5                    # selected D5 symlink
/tmp/qdc507-serial16k-v7/bridge-d5.guard16-v1.keep    # retained A/B rollback link
```

`qdc507-guard16-v2-runtime-restore-v2.py` is the **single audited mode-switch
interface** for this production snapshot. It validates the pinned NAS v1/v2
copies and promotion manifest, exact USB generation, idle state, no pending
recovery journals, and the existing production container before any write.
Do **not** substitute arbitrary binaries at these paths and do not recreate
the Docker container to test the code. A published v2 reference binary alone
cannot replace the source- and device-pinned promotion state.

## Safe read-only status checks on matching NAS

Only for the known original DS918+/DSM 7.1.1 environment with the existing
container and pinned recovery scripts; do not transfer these commands unchanged
to a different system.

```sh
P=/volume2/docker/asterisk/qdc507-16k-lab
sudo /usr/bin/python3.8 -I -B "$P/qdc507-guard16-v2-runtime-restore-v2.py" --check
sudo /bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh --check
```

Expected read-only Guard16 outputs include:

```
NAS_V1_V2_HASH_AND_MANIFEST_OK=1
DEVICE_CURRENT_MODE=v2
READ_ONLY_RUNTIME_CHECK_OK=1
```

If hashes, USB generation, or pending journals fail checks, **stop and investigate**;
do not erase pending files, rewrite baseline state, or blindly retry.

## Recovery / rollback entrypoints (not publication tests)

The following actions change runtime state and are only appropriate when the
specific fault exists, no call/SMS worker is active, and all independent
preflight checks succeed. **Do not invoke solely to validate GitHub files.**

- **After real USB unplug/replug or SIM swap:** the NAS entrypoint
  `/volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh --recover`
  executes a guarded hotplug → oneclick → ensure-v2 flow. It is not a coldboot
  entrypoint. The installed wrapper requires explicit `--recover`.
- **After genuine NAS reboot:** the separate root DSM startup task is
  `/usr/bin/python3.8 -I -B /volume2/docker/asterisk/qdc507-current/recovery/qdc507-coldboot-production-v3.py --boot`.
- **When verified v1 is active but v2 should be restored:** the already
  installed recovery helper supports `--ensure-v2`, idempotent if already v2.
- **Rollback to a preserved v1:** the same helper supports `--restore-v1`,
  requiring intact v1 backup and an idle, pinned environment. Never delete
  rollback state before confirming v1 operation.

PreAudio's strict v1/v2 allowlist change currently lives in the production
container's writable layer; **recreating the container loses it**. The guard
recovery baseline checker also has a separate compatibility fix. Relevant
source is committed in `checkpoint/2026-10-30/recovery/`, but reinstalling
these patches requires reviewing the exact existing container/image and
source hashes rather than blindly reapplying them.

## Verification evidence and limits

- One observed real call with Guard16 v2 had audible bidirectional voice,
  perceived better quality, automatic far-end hangup and a clean `DONE d4=0 d5=0`
  supervisor state. This is not a long-run soak or a reproducible production test.
- Offline source comparison: v2 patch applies byte-for-byte to `original.c`;
  code outside the D5 uplink loop, including D6, is unchanged.
- Offline deterministic tests: 157 PASS regular, 157 PASS UBSan trap
  (rechecked 2026-10-30).
- ARMv7 reference ELF: EABI5 soft-float, `ld-linux.so.3`, max recorded
  GLIBC dependency 2.17; SHA256 matches the v2 hash referenced by the runtime
  restoration source.
- USB/SIM recovery was observed to restore Free/Voice/SMS and v2 after the
  second swap, but complete top-level success-marker coverage was not audited.
- A fresh **unattended coldboot** after all fixes has **not** been retested.
- Full reproduction from GitHub without the original target firmware, private
  voice kernel modules/runtime state, NAS configuration, and v1 backup is **not
  yet possible**. No result here is an automatic deployment authorization.
