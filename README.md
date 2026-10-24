# QDC507 Asterisk Gateway

QDC507 + Asterisk 22 voice/SMS gateway project.

Includes:
- QDC507 chan_quectel build and patches
- Asterisk configuration templates
- Voice/audio helper scripts
- USB/ADB/hot-plug/cold-start recovery
- SMS queue delivery and systemd timer
- SIP REGISTER firewall protection
- Configuration deployment and log maintenance scripts

Production passwords, private network configuration, device identifiers, logs and SMS data are intentionally excluded.

## Dual-mode source integration (2026-10-24)

Adds the accepted opt-in serial16k fast-poll/deadline source chain (`c3e9638…`),
parallel pre-audio preparation without a host periodic ADB heartbeat or a 150 s
call-duration limit, and reference integration into the original 8k/16k modes.
The existing 8k build is preserved.

**This is a source/test integration kit, not a complete independently rebuildable
or ready-to-deploy dual-mode system.** Private configuration and original mode
manager/deployment prerequisites are not included. Templates must be adapted
privately; accepted binary hashes are references, not a reproducible-build claim.
See [included material, offline tests and missing prerequisites](docs/dualmode-20261024.md).

## Current accepted single-device driver (2026-10-18)

The build now includes the accepted registration, enum-bounds, hangup/CLCC
wait-slice and ringbuffer cross-boundary fixes. Deployed module SHA-256:
`9899154c0869a7e7a62112c2cace1e3ebdf2d22a245371559fab15e912de320f`.

See [source reproduction, offline tests and build/ABI limits](docs/driver-20261018.md).
The public pipeline reproduces the accepted source, not guaranteed byte-identical
module output. Short call/SMS acceptance passed; long-term stability and complete
elimination of latency are not established. No module binary is included.
