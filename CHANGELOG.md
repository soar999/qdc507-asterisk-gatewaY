# Changelog

## 2026-10-24 — dual-mode public source integration

- Add pinned accepted 8k → opt-in serial16k → timeout/deadline → fast-poll source reconstruction and manifests; preserve the original 8k build.
- Add per-call parallel pre-audio runtime, dynamic call identity, stale-owner fencing and fail-closed cleanup; no host periodic ADB heartbeat or 150 s call limit. Device-local supervision and preparation timeouts remain.
- Add original dual-mode image/upgrade reference changes, immutable 8k-slot preservation, runtime-only ADB checks, deployment templates and offline regressions.
- Reuse saved offline checks; recheck source manifest and image/ADB regression on resumption. No new hardware, Docker build or deployment acceptance is claimed.
- Document missing original mode-manager inputs, device bridges, matching binaries/ABI and private configuration: this is NOT a complete reproducible dual-mode deployment.

## 2026-10-18 — accepted single-device baseline

- Existing accepted registration, enum-bounds, hangup/CLCC wait-slice and ringbuffer fixes, retained unchanged by the 2026-10-24 integration.
