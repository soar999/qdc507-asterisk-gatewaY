QDC507 V4.4 R7 — NAS private prestage, 2026-11-26
Based on locally verified R7 archive. No production activation code.
Run as root from a trusted private directory on the NAS, Python 3.8 -B.
  sha256sum -c SHA256SUMS
  /usr/bin/python3.8 -B nas-prestage.py --check
  /usr/bin/python3.8 -B nas-prestage.py --stage
--check reads only. --stage performs the same checks and saves exact rollback
source bytes and candidate bytes in a fresh 0700 directory under LAB; individual
files are 0600. Do NOT upload that private directory to any public repository.
Neither mode invokes ADB, edits production files, restarts any service, runs a
recovery helper, or installs a scheduled task. IDLE_AT_PREFLIGHT is a snapshot,
not a call admission barrier. DSM_TASK_REFERENCE counts are references only;
they do not establish which tasks are enabled. No automatic apply or rollback.
The final PRESTAGE-ONLY.json is a private staging receipt, not installation
approval. The live profile inventory and old hashes are mandatory; any drift
stops. The present production watcher must have a fresh SHELL_OK sample.
New tests: 13 unit tests for private stage, readback, interruption, liveness,
symlink/hardlink and exact idle/loaded route parsers. R7 integration and wrapper
regressions rerun. No actual NAS/ADB/hardware test done here. Actual coldboot,
USB recovery, shared mount lock and V43 admission barrier remain to be verified.
