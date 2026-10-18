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

## Current accepted single-device driver (2026-10-18)

The build now includes the accepted registration, enum-bounds, hangup/CLCC
wait-slice and ringbuffer cross-boundary fixes. Deployed module SHA-256:
`9899154c0869a7e7a62112c2cace1e3ebdf2d22a245371559fab15e912de320f`.

See [source reproduction, offline tests and build/ABI limits](docs/driver-20261018.md).
The public pipeline reproduces the accepted source, not guaranteed byte-identical
module output. Short call/SMS acceptance passed; long-term stability and complete
elimination of latency are not established. No module binary is included.
