#!/usr/bin/env python3
import re, sys
from pathlib import Path
binary,h,p,v,d=sys.argv[1:]
h,p,v,d=[Path(x).read_text() for x in (h,p,v,d)]
assert re.search(r'Class:\s+ELF32',h)
assert re.search(r'Machine:\s+ARM\b',h)
assert 'little endian' in h
assert 'Version5 EABI' in h and 'soft-float ABI' in h
assert 'Requesting program interpreter: /lib/ld-linux.so.3' in p
versions=set(re.findall(r'GLIBC_([0-9]+(?:\.[0-9]+)+)',v))
assert versions and all(tuple(map(int,x.split('.'))) <= (2,19) for x in versions), versions
assert 'GLIBC_PRIVATE' not in v
assert not re.search(r'\((?:RPATH|RUNPATH)\)',d)
needed=re.findall(r'Shared library: \[(.*?)\]',d)
assert set(needed)<= {'libc.so.6','libpthread.so.0','libdl.so.2','librt.so.1','libgcc_s.so.1'},needed
assert 'libc.so.6' in needed
print('ABI_AUDIT_PASS ELF32 ARM EABI5 soft-float interpreter=/lib/ld-linux.so.3')
print('GLIBC_VERSIONS='+','.join(sorted(versions,key=lambda x:tuple(map(int,x.split('.'))))))
print('NEEDED='+','.join(needed))
