#!/usr/bin/env python3
"""Reproduce and regression-check A-cleanup/B-launch overlap, offline only.

Usage: python3 qdc507_race_regression.py --expect-timeout device-v2.sh
       python3 qdc507_race_regression.py --expect-admitted candidate.sh

Linux-only POSIX shell test, uses simulated PCM files, no modem, no ADB,
no NAS production access. All state is isolated in a temporary directory.
"""
import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time


def run(args, env):
    return subprocess.run(args, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=16)


def still_same(pid, birth):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()
        return fields[0]!='Z' and fields[19]==birth
    except (OSError,IndexError): return False


def regression(script, admitted):
    with tempfile.TemporaryDirectory(prefix='qdc507-race-offline-') as tmp:
        root=Path(tmp)
        src=root/'device.sh'; shutil.copy2(script,src); src.chmod(0o700)
        d=root/'state'; d.mkdir()
        b=root/'bin'; b.mkdir()
        pcm=root/'pcm'
        for n in ('4p','4c','5p','6c'):
            folder=pcm/('pcm'+n)/'sub0';folder.mkdir(parents=True)
            (folder/'hw_params').write_text('closed\n')
            (folder/'status').write_text('state: PREPARED\n')
        enable=root/'audio_enable';enable.write_text('0\n')
        for name in ('bridge-d4','bridge-d5'):
            path=b/name
            path.write_text('#!/bin/sh\ntrap "" TERM\nwhile :; do sleep 0.1; done\n')
            path.chmod(0o700)
        # Avoid dependency on util-linux setsid inside minimal Python containers.
        # This is a process-launch shim, not an audio or ownership simulation.
        shim=b/'setsid'
        shim.write_text('#!/bin/sh\nexec "$@"\n')
        shim.chmod(0o700)
        env=os.environ.copy()
        env.update(PATH=str(b)+os.pathsep+env.get('PATH','/usr/bin:/bin'),
                   QDC_TEST_INTERPRETERS='1',QDC_TEST_PCM_ROOT=str(pcm),
                   QDC_TEST_ENABLE=str(enable))
        cmd=lambda action,token:['/bin/sh',str(src),str(d),str(b),action,token]
        print('OFFLINE_ONLY=1',flush=True)
        results={}
        try:
            a=run(cmd('launch','callA'),env)
            print('LAUNCH_A',a.returncode,repr(a.stdout.strip()),flush=True)
            assert a.returncode==0 and a.stdout.strip()=='LAUNCH_HELD callA'
            limit=time.monotonic()+4
            while not (d/'tokens/callA/d5.pid').exists() and time.monotonic()<limit:
                time.sleep(.05)
            assert (d/'tokens/callA/d5.pid').exists()
            enable.write_text('1\n')
            cstarted=time.monotonic()
            def cancel_a():
                results['cancel_a']=run(cmd('cancel','callA'),env)
                results['cancel_elapsed']=time.monotonic()-cstarted
            th=threading.Thread(target=cancel_a);th.start()
            limit=time.monotonic()+2
            while not (d/'lock').exists() and time.monotonic()<limit:
                time.sleep(.02)
            assert (d/'lock').exists(),'cancel did not acquire lock'
            timer=threading.Timer(4.50,lambda:enable.write_text('0\n'))
            timer.start()
            bstart=time.monotonic()
            second=run(cmd('launch','callB'),env)
            belapsed=time.monotonic()-bstart
            print('LAUNCH_B',second.returncode,repr(second.stdout.strip()),
                  f'elapsed={belapsed:.2f}s',flush=True)
            timer.join()
            th.join(12)
            assert not th.is_alive()
            cancel=results['cancel_a']
            print('CANCEL_A',cancel.returncode,repr(cancel.stdout.strip()),
                  f'elapsed={results["cancel_elapsed"]:.2f}s',flush=True)
            assert cancel.returncode==0 and cancel.stdout.strip()=='CLEAN callA'
            if admitted:
                assert second.returncode==0 and second.stdout.strip()=='LAUNCH_HELD callB', 'B must start after A clean'
                assert (d/'owner').read_text().strip()=='callB'
                stale=run(cmd('cancel','callA'),env)
                print('LATE_CANCEL_A',stale.returncode,repr(stale.stdout.strip()),flush=True)
                assert stale.returncode==0 and stale.stdout.strip()=='STALE_FENCED callA'
                assert (d/'owner').read_text().strip()=='callB', 'old cancel displaced new owner'
                ended=run(cmd('cancel','callB'),env)
                print('CANCEL_B',ended.returncode,repr(ended.stdout.strip()),flush=True)
                assert ended.returncode==0 and ended.stdout.strip()=='CLEAN callB'
                assert not (d/'owner').exists() and not (d/'lock').exists()
                print('PASS=NEW_CALL_ADMITTED_AFTER_CLEANUP_AND_OLD_CANCEL_FENCED')
            else:
                assert second.returncode==75 and second.stdout.strip()=='LOCK_TIMEOUT', 'Original lock timeout was not reproduced'
                assert not (d/'owner').exists() and not (d/'lock').exists()
                print('PASS=ORIGINAL_NEW_CALL_TIMED_OUT_BEFORE_OLD_CLEAN')
        finally:
            enable.write_text('0\n')
            for record in (d/'tokens').glob('*/*.pid'):
                try:
                    pid_s,birth=record.read_text().split()
                    pid=int(pid_s)
                    if still_same(pid,birth):
                        os.kill(pid,signal.SIGKILL)
                except (ValueError,OSError,ProcessLookupError): pass


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--expect-timeout',action='store_true')
    selection.add_argument('--expect-admitted',action='store_true')
    parser.add_argument('device_script',type=Path)
    args=parser.parse_args()
    regression(args.device_script,args.expect_admitted)
