# QDC507 SMS: multi-contact SIP MESSAGE delivery (2026-11-11)

Source snapshot for **soar999/qdc507-asterisk-gatewaY**. This directory is
for code review/version control, not a drop-in restore bundle.

## Changes

- For AOR 100, enumerate every unexpired registrar Contact and send one SIP
  MESSAGE per distinct URI (without relying on UA or IP prefixes).
- Use one temporary Asterisk dialplan context and isolated per-recipient stage
  files for each attempt; read MESSAGE_SEND_STATUS per recipient.
- Record per-URI protocol-handoff results for a queued SMS. Keep pending on
  failures; archive body/src/gw/conv and receipt metadata under delivered/ on
  protocol-handler success. Asterisk SUCCESS **does not prove phone receipt**.
- Preserve the original systemd tick service and SHA256 consumer pin concept;
  the installer is a *historical, NAS-specific* deployment script.

## What was verified on the NAS

- 2026-11-11: Groundwire foreground / DIRECT received a test message.
- 2026-11-11: Groundwire background / SIPIS received a test message.
- Multi-contact test: Asterisk protocol-handler SUCCESS for both DIRECT and
  SIPIS; one DIRECT message was visible in Groundwire foreground.
- One real operator SMS arrived after the V2 production switch. Both SHA256
  and the 10-second timer were checked; delivered/ had a new archive.

## Public-repository sanitization

The reviewed source replaces the NAS-specific SIP hostname with the
`QDC507_SMS_DOMAIN` environment setting, defaulting to `example.invalid`.
**That makes this GitHub copy different from the production-tested code and
its SHA256.** The production-tested consumer SHA256 was:

`e36a9e27d9361c32978a72b19cb09a8e6551ce9b1226628305cacd431a1976ae`

The installed tick wrapper SHA256 was:

`3057d48056c820874624740a1fb77700bac83b947e96876597ea2fe75c82f694`

The included installer is a historical reference: it assumes the old
consumer PIN, the original private LAB path and systemd unit names. **Do not
run this public-snapshot version as a replacement for the current NAS V2.**
Keep the tested private recovery copies under the NAS `/volume2` lab tree.

## Security and limitations

- Do not commit real `.body`, `.src`, `.conv`, `.gw`, `delivered/`, `pending/`,
  logs, SIP passwords, tokens, registration database dumps or live configuration.
- Registrations are validated by expiration, not by UA. Protect extension
  100 with SIP authentication. Unexpired is not equal to reachable.
- MESSAGE_SEND_STATUS reports local protocol handoff, not delivery to the
  device or the user; retry after timeout may create duplicates.
- Coldboot recovery integration was **not** changed as part of this snapshot.
