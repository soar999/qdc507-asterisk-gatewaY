import importlib.util,hashlib
spec=importlib.util.spec_from_file_location('prepare','prepare-v44-stage.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
b=b'''#!/bin/sh
set -eu
exec /usr/bin/env -i PATH=/usr/bin:/bin QDC_DEVICE_SCRIPT=/tmp/qdc16k-preaudio-manual/device-v43.sh QDC_SERIAL=FAKEADB /usr/bin/python3 -B /opt/qdc16k-preaudio-manual/preaudio-inbound-v43.py "$1" "$2"
'''
i={'idVendor':'abcd','idProduct':'1234','serial':'FAKESYSFS'};h=m.sha(b);out=m.make_wrapper(b,i,h)
for n,condition in [('KEEPS_ADB_SERIAL',b'QDC_SERIAL=FAKEADB' in out),('DISTINCT_SYSFS_SERIAL',b'QDC_USB_SYSFS_SERIAL=FAKESYSFS' in out),('NEW_DEVICE',b'device-v44.sh' in out),('PROTECTED_GATE',b'QDC_PROTECTED_GATE=1' in out),('NEW_HOST',b'preaudio-v44-candidate.py' in out)]:assert condition,n;print('PASS='+n)
for n,old,ids,expected in [('DRIFT',b+b'\n',i,h),('BAD_ID',b,dict(i,serial='x;id'),h),('AMBIGUOUS_SERIAL',b+b'QDC_SERIAL=FAKE2\n',i,m.sha(b+b'QDC_SERIAL=FAKE2\n'))]:
    try:m.make_wrapper(old,ids,expected)
    except RuntimeError:print('PASS='+n)
    else:raise AssertionError(n)
print('OFFLINE_ONLY=1')
