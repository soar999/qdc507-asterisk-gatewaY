#!/usr/bin/env python3
"""Stage an experimental QDC507 v4.3 lock-timeout coordination pair OFFLINE.

Requires exact 2026-11-15 production v4.2 inputs by SHA256. Never touches
Asterisk, Docker, ADB or the QDC507. Writes only new files to --output-dir;
refuses to overwrite. Not an installer and not an approval to deploy.
"""
import argparse
import ast
import difflib
import hashlib
import pathlib
import subprocess
import sys

DEV_SHA='3682a8f0b4160f43778624e8551f39f8390163c92850f6661980ca64038d5f76'
HOST_SHA='7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7'
LAB=pathlib.Path('/volume2/docker/asterisk/qdc507-16k-lab')

OLD_LOCK='''lock() {
 i=0
 while ! mkdir "$D/lock" 2>/dev/null; do
  i=$((i+1)); [ "$i" -lt 40 ] || { echo LOCK_TIMEOUT; exit 75; }
  sleep 0.1
 done
'''
NEW_LOCK='''lock() {
 i=0
 # A valid teardown can hold the shared lock for almost five seconds.
 # Launch and cancel have a seven-second budget; host RPC and AGI budgets
 # are extended correspondingly. Never remove the lock or bypass owner checks.
 limit=40
 case "$A" in launch|cancel) limit=70;; esac
 while ! mkdir "$D/lock" 2>/dev/null; do
  i=$((i+1)); [ "$i" -lt "$limit" ] || { echo LOCK_TIMEOUT; exit 75; }
  sleep 0.1
 done
'''

HOST_REPL={
  "result = rpc('cancel', t, 11)": "result = rpc('cancel', t, 15)",
  "if rpc('launch', t) != f'LAUNCH_HELD {t}':": "if rpc('launch', t, 10) != f'LAUNCH_HELD {t}':",
  "end = time.monotonic() + (5 if action == 'submit' else 12)":
    "end = time.monotonic() + (10 if action == 'submit' else 12)",
}


def need(condition, label):
    if not condition: raise RuntimeError(label)


def digest(b): return hashlib.sha256(b).hexdigest()


def read_verified(p, expected):
    need(p.is_file() and not p.is_symlink(), 'SOURCE_NOT_REGULAR: '+str(p))
    data=p.read_bytes()
    need(digest(data)==expected,'SOURCE_SHA_MISMATCH: '+str(p)+' got '+digest(data))
    return data.decode('utf-8')


def one_replace(src, old, new):
    need(src.count(old)==1, 'EXPECTED_EXACTLY_ONE_MATCH: '+repr(old[:90]))
    return src.replace(old, new)


def put_new(p,data):
    # Exclusive creation: no overwrite of existing staged files, even in LAB.
    with p.open('x', encoding='utf-8', newline='\n') as f: f.write(data)
    p.chmod(0o600)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--device-source',type=pathlib.Path,required=True)
    ap.add_argument('--host-source',type=pathlib.Path,required=True)
    ap.add_argument('--output-dir',type=pathlib.Path,default=LAB)
    args=ap.parse_args()
    out=args.output_dir
    need(out.is_dir() and not out.is_symlink(),'OUTPUT_DIR_INVALID')
    dev=read_verified(args.device_source,DEV_SHA)
    host=read_verified(args.host_source,HOST_SHA)
    newdev=one_replace(dev,OLD_LOCK,NEW_LOCK)
    newhost=host
    for before,after in HOST_REPL.items(): newhost=one_replace(newhost,before,after)
    ast.parse(newhost)
    syntax=subprocess.run(['/bin/sh','-n'],input=newdev,text=True,capture_output=True)
    need(syntax.returncode==0,'DEVICE_SHELL_SYNTAX: '+syntax.stderr)
    need(newdev.count('rm "$D/owner"')==1 and newdev.count('STALE_FENCED')==1,
         'DEVICE_OWNER_FENCE_REMOVED')
    need(newdev.count('QUARANTINED')==1,'DEVICE_QUARANTINE_REMOVED')
    newfiles={
      'qdc507-device-v43-candidate.sh':newdev,
      'qdc507-inbound-v43-candidate.py':newhost,
      'qdc507-device-v43.diff':''.join(difflib.unified_diff(dev.splitlines(True),newdev.splitlines(True),fromfile='device-v2.sh',tofile='qdc507-device-v43-candidate.sh')),
      'qdc507-inbound-v43.diff':''.join(difflib.unified_diff(host.splitlines(True),newhost.splitlines(True),fromfile='preaudio-inbound-v42.py',tofile='qdc507-inbound-v43-candidate.py')),
    }
    for name in newfiles:
        need(not (out/name).exists() and not (out/name).is_symlink(), 'OUTPUT_ALREADY_EXISTS: '+name)
    for name,data in newfiles.items():
        put_new(out/name,data)
        print('STAGED='+str(out/name)+' SHA256='+digest(data.encode('utf-8')))
    print('V43_EXACT_SOURCE_HASHES_OK=1')
    print('V43_SOURCE_SYNTAX_OK=1')
    print('PRODUCTION_UNCHANGED=1')
    print('NOT_DEPLOYED=1')


if __name__=='__main__':
    try:main()
    except Exception as e:
        print('STAGE_FAILED='+str(e),file=sys.stderr)
        sys.exit(2)
