# QDC507 V4.4 Event — 2026-11-26 public review checkpoint

**Source archive for review, not deployable.** Incremental checkpoint based on the [2026-11-21 DSM 7.2.2 Update 10 checkpoint](../2026-11-21/README.md). The unchanged MemoryFix/Guard16/driver sources remain in earlier checkpoints. The original private NAS export is not published.

## Production changes

- V4.4 preaudio: shared/exclusive ADB coordination, USB generation and epoch admission, one fenced cancellation, and bounded recovery transactions.
- Event-driven device supervisor: after PCM READY, wait for bridge-child termination instead of periodic shell polling. Startup readiness polling remains; D4/D5 audio binaries and Guard16 V2 are unchanged.
- Epoch permissions: atomic replacement, root:1000 mode 0640, with Asterisk-user readback before allowing READY.
- Stable USB and coldboot entrypoints and the ADB watchdog dispatch through the V44 runtime. The original recovery cores remain pinned.
- V22 inner and V3 aggregate audit updated with V44/Event source, activation, epoch, container, device hashes, and existing business checks; original 2026-11-21 release preserved.

## Acceptance (user-provided live NAS logs)

- Production deployment and 179-second call: pass; bridge entry on answer and cleanup=1 after hangup.
- Controlled QDC507 ADB reboot: USB device generation 20 -> 21; host boot unchanged. V22 OneClick, Guard16 V2, V43, V44 and Event restaged; original container ID preserved, SMS timer active, new READY published. One 18-second outbound call after recovery bridged and cleaned up successfully.
- ADB reset static preflight: pass; ADB healthy, no reset needed. Watchdog skips during recovery/calls, resumes afterwards.
- Full production deep audit: pass; one ADB watchdog, heartbeat fresh.
- Pending: physical disconnect/power cycle, real automatic ADB reset, NAS coldboot and enabled startup trigger. Audio quality is not independently measured; long-term memory leakage is not established.

## Archive layout and integrity

`source-manifest.json` records all 35 source files, their original NAS SHA256 and public SHA256, and the dialplan hashes only. `source/v44-prestage/` contains the historical candidate and test evidence, not the active release. `source/v44-coordination/` contains the active coordination source snapshot. `source/lab/` contains the active runtime, Event script, entry wrappers, audit and release evidence. `source/production-recovery/` contains the stable USB entrypoint.

The production SHA256 pins and prestage checksums remain unchanged as historical evidence. Redacted files will intentionally fail those pins. Private prestage contents, full dialplan, SIM data, credentials, active epoch, USB reset journal, Docker images and runtime baselines are not included. Do not run these public files on a production NAS or attempt to bypass their integrity gates. Use an independently secured private backup for restoration.
