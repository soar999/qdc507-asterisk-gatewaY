#!/usr/bin/env python3
"""Offline integrity / byte-for-byte patch verification. No hardware or network calls."""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
manifest = json.loads((HERE / "source-manifest.json").read_text(encoding="utf-8"))
for item in manifest["files"]:
    path = HERE / item["path"]
    if path.is_symlink() or not path.is_file():
        raise SystemExit("UNTRUSTED_OR_MISSING_FILE=" + item["path"])
    b = path.read_bytes()
    if len(b) != item["bytes"] or hashlib.sha256(b).hexdigest() != item["sha256"]:
        raise SystemExit("SOURCE_MANIFEST_MISMATCH=" + item["path"])
print("GUARD16_V2_PUBLIC_SOURCE_HASHES_OK=1")

if not shutil.which("patch"):
    raise SystemExit("PATCH_TOOL_UNAVAILABLE_NO_SOURCE_RECONSTRUCTION")
with tempfile.TemporaryDirectory(prefix="guard16-v2-patch-check-") as d:
    t = pathlib.Path(d)
    candidate = t / "mavo_pcm_bridge_16k_clock8_guard16.c"
    candidate.write_bytes((HERE / "original.c").read_bytes())
    proc = subprocess.run(
        ["patch", "-p1", "-F", "0", "-i", str(HERE / "guard16-v2.patch")],
        cwd=str(t), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=20,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout.decode(errors="replace")[-1200:])
        sys.stderr.write(proc.stderr.decode(errors="replace")[-1200:])
        raise SystemExit("GUARD16_V2_PATCH_APPLY_FAILED")
    if candidate.read_bytes() != (HERE / "mavo_pcm_bridge_16k_clock8_guard16.c").read_bytes():
        raise SystemExit("GUARD16_V2_PATCH_OUTPUT_MISMATCH")
print("GUARD16_V2_PATCH_RECONSTRUCTS_SOURCE_EXACTLY=1")
print("NO_NAS_OR_MODEM_WRITES_PERFORMED=1")
