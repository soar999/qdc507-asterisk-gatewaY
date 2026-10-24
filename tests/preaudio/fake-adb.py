#!/usr/bin/env python3
import os, sys, time
if os.environ.get('QDC_FAKE_ADB_LOG'):
    with open(os.environ['QDC_FAKE_ADB_LOG'], 'a') as log:
        log.write(sys.argv[-1] + '\n')
if os.environ.get('QDC_FAKE_OFFLINE'): sys.exit(88)
assert sys.argv[-2]=='shell'
if ' launch ' in sys.argv[-1]:
    time.sleep(float(os.environ.get('QDC_FAKE_LAUNCH_DELAY','0')))
os.execv('/bin/sh',['sh','-c',sys.argv[-1]])
