# QDC507 inbound Pre-audio v4.2 (2026-11-06)

This commit adds a **separate inbound-only** Pre-audio controller and a checked release mechanism. It intentionally leaves the original `helpers/preaudio/preaudio.py` (used by production outbound `_7X.`), `device.sh`, Guard16 v2, SMS, and driver source unchanged.

## What was validated

- Original production controller SHA-256: `2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690`.
- Reconstructed v4.1 candidate SHA-256: `887719e6a544e8887a587cc28dd386cfd3dcb9b604a8c0c9093bd79d3704fbe4`.
- Inbound v4.2 controller SHA-256: `7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7`.
- Device helper hash observed: `3682a8f0b4160f43778624e8551f39f8390163c92850f6661980ca64038d5f76`.
- v4.2 offline lifecycle tests (10 PASS): `submit`, pre-ring `READY`, post-answer `READY`, `cancel`, parallel launch/query, expected negative `NOT_READY`, ADB stderr, first-error preservation, concurrent cancellation, definitely-not-sent retry and unknown-outcome no-repeat.
- First real inbound call reported **faster answering**. After that call, ADB shell responded (`id -u=0`), the Guard16 runtime was idle (`DONE d4=0 d5=0`) and device-side Pre-audio owner and lock were absent.

**Not yet established:** long-term ADB stability, sustained consecutive-call behavior, two-way audio / remote-hangup acceptance, or the root cause of the previous ADB `error: closed`. A single successful call does not prove full resolution.

## Files

- `helpers/preaudio/preaudio-inbound-v42.py`: exact generated v4.2 controller source used in the staged tests, not a replacement for the outbound controller.
- `scripts/qdc507-preaudio-v41-stage.py`: stages v4.1 from the verified original source.
- `tests/preaudio/qdc507-preaudio-v41-offline-tests.py`: v4.1 offline regression checks.
- `scripts/qdc507-preaudio-v42-stage-test.py`: builds and tests v4.2 from v4.1.
- `scripts/qdc507-inbound-v42-release-verified.py`: isolated inbound rollout and rollback, with precise AGI path and argument checks.

## NAS deployment constraints

These scripts are for a specific Synology NAS layout and are **not** a universal one-click installer. Place deployment/test scripts in `/volume2/docker/asterisk/qdc507-16k-lab/` **root**, not its subdirectories. The checked release script requires verified current production versions, a separate v4.2 candidate at `LAB/qdc507-preaudio-v42-candidate.py`, and a pristine base dialplan for its `--stage` operation. `--apply` requires zero active Asterisk calls and validates the compiled inbound dialplan; `--rollback` restores the saved original inbound dialplan while idle. Do not reuse a staged manifest after unrelated dialplan modifications.

The release script copies new inbound controller/wrapper paths and changes **only** the two inbound routes and new inbound wait/cancel contexts. It does **not** update the original outbound Pre-audio controller or its wrapper. The inbound and outbound controllers still use the same remote device-side owner/lock, so simultaneous testing requires separate safety analysis.

If ADB shell becomes unavailable, a dialplan rollback alone will not restore it. Follow the established host ADB / USB recovery procedure; never automatically delete device `owner` or `lock` after unconfirmed cleanup.

Do not include private credentials, device serials, SIM or SMS contents, call logs, or host backups in this repository.
