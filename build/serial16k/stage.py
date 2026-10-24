#!/usr/bin/env python3
"""Reconstruct accepted 8k -> opt-in serial16k -> deadline/fastpoll locally.
No Docker, ADB, service operations or network access. Outputs must be fresh.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PIN = 'e1c027750bdf878bfe552341de457a9245358ce1'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--upstream', required=True, type=Path, help='local upstream Git repository containing the pinned commit')
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
out = a.output.resolve()
if out.exists(): raise SystemExit('Output already exists; use a fresh directory')
def run(*cmd, **kw): subprocess.run(list(map(str, cmd)), check=True, **kw)
commit = subprocess.check_output(['git','-C',str(a.upstream),'rev-parse',PIN+'^{commit}'], text=True).strip()
assert commit == PIN
out.mkdir(parents=True)
source = out/'src'; source.mkdir()
with tempfile.TemporaryFile() as archive:
 run('git','-C',a.upstream,'archive','--format=tar',PIN,stdout=archive)
 archive.seek(0)
 run('tar','-xf','-','-C',source,stdin=archive)
env = dict(os.environ, QDC507_SOURCE_ROOT=str(source))
run(sys.executable,REPO/'build/clccsafe4/qdc507_patch.py',env=env)
# Capture 8k before adding opt-in 16k; existing 8k recipe is never modified.
shutil.copytree(source,out/'accepted8k',symlinks=True)
run(sys.executable,HERE/'qdc507_serial16k_patch.py','--selftest',env=env)
run(sys.executable,HERE/'qdc507_serial16k_patch.py',env=env)
for rel,sha in json.loads((HERE/'baseline-manifest.json').read_text()).items():
 assert hashlib.sha256((source/rel).read_bytes()).hexdigest()==sha,rel
shutil.copytree(source,out/'baseline',symlinks=True)
for name in ('01-timeoutfix.patch','02-fastpoll.patch'):
 run('git','-C',source,'apply','--check',HERE/name)
 run('git','-C',source,'apply',HERE/name)
run(sys.executable,HERE/'verify.py',source)
shutil.copytree(REPO/'tests/serial16k',out/'tests',ignore=shutil.ignore_patterns('__pycache__'))
print('STAGED pinned sources and tests; no module compiled:',out)
