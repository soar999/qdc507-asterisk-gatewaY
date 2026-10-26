# QDC507 2026-10-26 production source checkpoint

**Important:** This is a SOURCE archive and a record of operator-confirmed
behavior. It is NOT an installer or proof of successful NAS cold boot / USB hotplug.
The GitHub repo's older fixed USB / old option.ko disaster recovery guide is
outdated for this production system.

## Production runtime

- Synology DS918+, DSM 7.1.1-42962 Update 9; Linux 4.4.180+.
- New host option.ko SHA-256: 395918cff6c00e82cd8fbdfd99b8b2420ac9089605f6df0cc8a80550fd6d98cc.
- Host USB QDC507 2ca3:4006 IF0-IF4 on option; IF5 ADB / usbfs; IF6-IF8 on snd-usb-audio.
- Asterisk image: qdc507-asterisk:dualmode-remotehangup-v4-prod.
- Deployed chan_quectel.so SHA-256: ca25e8e04a5ad9792282635a99e1d7152429800d6a51331ba18991e72436fa87.

## Remote hangup v4

Fix: far end hangs up while still ringing / never answered, but the local
Asterisk/SIP channel was not released. The v4 CLCC finalizer handles early
call state, requiring valid successive snapshots and preserving invalid-snapshot
protection. Connected-call remote hangup and ringing-stage cancellation passed
live user tests. Long-run soak and other network scenarios not yet proven.

Remote-hangup v4 source is exported from the *NAS build context*; if a v3
reference was available, additional .patch files were generated from it.
A full source snapshot is not a byte-identical reproducible binary build.

## Dynamic USB recovery and NAS boot

- qdc507-hardware is the SAME production Docker container. No Docker recreation.
- Container references /dev/qdc507/at (IF2) and /dev/qdc507/pcm16 (IF1), with
  dynamic character-device permissions and read-only /dev/qdc507, /dev/snd mounts.
- Manual SIM/USB recovery uses qdc507-usb-recover.sh ->
  qdc507-hotplug-recovery-v1.py --recover -> qdc507-dynusb-oneclick-v2.py --recover.
- Maximum enumeration wait 40s, stability window 3s.
- NAS boot job uses qdc507-coldboot-production-v2.py --boot.
- Files in recovery/ are code snapshots with hard-coded production paths and
  hashes. They must be reviewed and placed on the matching NAS before use.
- 16k voice, APR3, Guard16, Pre-audio rely on additional device-side runtime
  modules, audio helpers and private local configuration. Binary blobs, private
  credentials, SMS queues, device IDs and runtime baseline state are EXCLUDED.

Verified: new host driver is live and bound to proper interfaces, dynamic
nodes resolve, modem Free, SMS timer active, oneclick healthy no-op passed,
coldboot read-only check passed.

Unverified: physical USB unplug/replug recovery; complete NAS reboot;
real bi-directional voice/SMS acceptance after these operations.

Public repository: inspect the tar contents for any source comments, example
credentials, contact information or licensed third-party code before Git push.
Do not copy entire NAS lab/ or production config/ into a public repo.
