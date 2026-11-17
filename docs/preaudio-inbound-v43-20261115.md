# QDC507 inbound Pre-audio v4.3 — 2026-11-15

This source-only update to [qdc507-asterisk-gatewaY](https://github.com/soar999/qdc507-asterisk-gatewaY) contains the **v4.3 inbound lock-wait coordination fix, deterministic staging/regression tests, deployment and rollback source, production read-only audits, and a device debug-log archive/cleanup example**. It does **not** replace the existing outbound controller or Guard16 v2.

## Changes

- Device: `launch` and `cancel` may wait for an existing cleanup lock for up to about **7 seconds**, instead of about 4 seconds. There is no new unconditional sleep. Ownership, cancellation tombstone and stale-cancel fencing remain in effect.
- Inbound host controller: `launch` RPC timeout 6 -> 10 s, `cancel` RPC 11 -> 15 s, `submit` AGI wait 5 -> 10 s. Actual delay during contention may be longer, while the uncontended path should not gain a fixed delay.
- Asterisk: an isolated v43 inbound AGI wrapper and configuration; retain the original `_7X.` outbound route and the user's local/`100@cloud-trunk` inbound choice.
- Recovery: the final instance-pinned release script prepares separate container startup and manual USB recovery hooks, with `--apply`, `--verify`, `--abort` and `--rollback` support. Do not alter original `prepare.py`, oneclick, Guard16 or their pinned dependencies.
- Audits: scheduled `qdc507-patch-audit-v3.sh` is now v43-aware; the historical filename `qdc507-inbound-v42-recovery-audit-v1.py` is deliberately retained for DSM scheduled-task compatibility while **auditing v43**. The scheduled shell audit separately counts **2 hangup-handler registrations** and **2 post-Dial cancellation Gosubs**, fixing the misleading `4 EXPECTED=2` report.
- Device debug-log archive/cleanup: the included `.example` is **not** an installed timer. It requires provisioning the real ADB serial on the target NAS and should not be run without checking live configuration and safety conditions.

## Reproducible source staging (no NAS needed)

The preceding v4.2 inputs are already in the repository at `helpers/preaudio/device.sh` and `helpers/preaudio/preaudio-inbound-v42.py`. Run from a repository checkout:

```sh
mkdir -p /tmp/qdc507-v43-stage
python3 scripts/qdc507_v43_stage.py \
  --device-source helpers/preaudio/device.sh \
  --host-source helpers/preaudio/preaudio-inbound-v42.py \
  --output-dir /tmp/qdc507-v43-stage
python3 tests/preaudio/qdc507_race_regression.py \
  --expect-admitted /tmp/qdc507-v43-stage/qdc507-device-v43-candidate.sh
```

Important SHA256 values:

| File | SHA256 |
|---|---|
| Original device helper | `3682a8f0b4160f43778624e8551f39f8390163c92850f6661980ca64038d5f76` |
| v43 device helper | `ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1` |
| Original v42 host controller | `7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7` |
| v43 host controller | `13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf` |
| Production v43 release script | `e59f754afa628cd050a8c85be15c936f9f69a07020550fc4ce1c6b49df49c486` |
| Deployed v43 inbound dialplan candidate | `0f232e3c356922392169a099a966b39b93c659136bdc424c27f963cae1f386e3` |

The generated v43 **host controller** is intentionally derived with a source-hash-checked patch from the public v42 controller; do not substitute an unverified copy from another deployment. The v43 device helper is also included as a hash-verified exact candidate.

## Observed validation, not generalized guarantees

- Offline concurrency regression: A cleanup about 4.55 s; overlapping B launch succeeds about 5.57 s later; A's late cancellation returns `STALE_FENCED`, B later cleans normally.
- Target NAS 2026-11-15: `V43_APPLIED=1` then `V43_PRODUCTION_READ_ONLY_VERIFIED=1`, compiled inbound routes, device script SHA, idle owner/lock, and Guard16 v2 check passed. The updated deep audit reported `QDC507_INBOUND_V43_RECOVERY_AUDIT_PASSED=1`.
- Production call count increased, but that by itself **does not prove** every call connected or had working bidirectional audio.
- Manual USB hook was installed and audited, but **actual new USB replug/recovery and unattended full NAS coldboot have not been validated after this change**.
- First scheduled shell audit showed `INBOUND_V43_CANCEL_HANDLER COUNT=4 EXPECTED=2`, a logic error in its audit counter, corrected here. The final corrected shell audit still needs confirmation on the target NAS.
- Device token logs are **not automatically rotated by the v43 release**. Only archived, already-CLEAN debug logs may be pruned. The `.example` deliberately replaces a private device serial with `__QDC_SERIAL__`.

## Deployment safety and privacy

The release and audit scripts are **original-DS918+/specific-instance pinned** and should not be run on a different server or on a recreated container. `--prepare` and audit checks are intended to be non-disruptive; `--apply` / `--abort` / `--rollback` mutate live routing and/or the runtime. Read code and inspect current recovery journals before any new action. Do not run recovery merely for demonstration.

No device serial, SIM/IMSI/IMEI, telephone number, private runtime backup, dialplan configuration, archived logs, ADB keys or credentials is included in this source contribution.
