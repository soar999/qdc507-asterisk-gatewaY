# Publication review and gap report — 2026-11-21

## Integrity

- Verified source ZIP SHA256: `5f697c086d5426941665d1caff7f2bcdf749d002bc02913f1522f3c1268d3033`.
- Verified **52 of 52** NAS-export source file hashes. `missing_required=[]`, `excluded=[]` are facts about the NAS export's **limited allowlist**, not proof of a complete NAS export.
- Publication applies additional redaction for original Docker container/image instance IDs and a USB serial-like identifier that the first exporter did not catch. The untouched original ZIP is **not** included and must **not** be committed to a public GitHub repository.
- No SIM contents, SIP credentials, call/SMS messages, Docker image layers or driver binaries intentionally included. Further human review is still required for public release.
- Public source files are deliberately **not executable drop-in recovery sources** because of replaced identifiers; their SHA256 values differ from the production pins.

## Known missing / unresolved for a complete disaster recovery backup

1. Original NAS USB/ADB/USB-audio driver **build inputs**, modules for the upgraded kernel and `vermagic`: exported driver metadata reports `vermagic=unavailable`. The `option.c` source and interface patch are present, but source for every kernel module and a reproducible DSM722 toolchain/Makefile were not exported.
2. Working `r8152` source and package build metadata: only the running package status and a `.ko` SHA256 from a specific candidate path were captured. No third-party compiled `.ko` is redistributed here.
3. DSM Task Scheduler definitions, boot-trigger task enabled state, `systemd` unit contents, udev rules, and actual module-install entrypoints under `/usr/local/libexec/qdc507/boot/` and nested directories were not captured by the **nonrecursive** exporter.
4. Runtime hard dependencies referenced by these sources, e.g. `qdc507-if-role-manager-v1.py`, `qdc507-v7-d5-clock8-guard16-ab-dynusb-v1.py`, `qdc507-memoryfix-v22-release.json`, and the private baseline JSON files, were not all included; private baseline and credentials should remain excluded from public GitHub.
5. No complete source diff against the November 18 or November 20 release has been computed for every live dependency; the archive is a time-bounded snapshot, not a fully reproducible build.
6. First-end-to-end physical USB swap/replug and cold-boot tests for the final **post-ACL600** sources remain unverified. Audit output says both post-coordinator-retirement live tests were not performed.

## Source grouping

- `source/lab/`: current source, intermediate candidates, pre-patch backups and installers. Use file names and `file_records[].role` for coarse classification.
- `source/host-sbin/`: host-side Python scripts captured from `/usr/local/sbin`.
- `source/production-recovery/`: stable USB recovery scheduler entrypoint.
- `driver-metadata.json`: SHA256 and observed Synology r8152 package status, no binaries.

## Security specifics

A previous automated NAS sanitizer did **not** redact hard-coded image/container identifiers in several Python files. The final reviewed bundle replaces all observed copies of the exact values and the serial-like literal. This does not establish that every possible sensitive value is absent. Do **not** publish the original NAS export ZIP.

Keep the historical 2026-11-18 and 2026-11-20 checkpoints unchanged. Publish this as a separate 2026-11-21 checkpoint on a review branch and inspect diff before merging to `main`.
