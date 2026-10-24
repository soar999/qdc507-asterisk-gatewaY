#!/usr/bin/env python3
"""Run real runtime against fake local ADB/PCM in a disposable directory."""
from pathlib import Path
import shutil, subprocess, sys, tempfile
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
with tempfile.TemporaryDirectory(prefix='qdc-preaudio-test-') as directory:
    root = Path(directory)
    for name in ('tests.py','fake-adb.py','fake-bridge.sh'):
        shutil.copyfile(HERE/name, root/name)
    for name in ('preaudio.py','device.sh'):
        shutil.copyfile(REPO/'helpers/preaudio'/name,root/name)
    subprocess.run([sys.executable,'-B',str(root/'tests.py')],check=True)
