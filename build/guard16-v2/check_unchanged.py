#!/usr/bin/env python3
"""Verify D6 and all code outside the uplink loop remain byte-identical."""
import hashlib
from pathlib import Path
root = Path(__file__).resolve().parent
old = Path(__import__('os').environ.get('ORIGINAL', str(root/'original.c'))).read_bytes()
new = (root/'mavo_pcm_bridge_16k_clock8_guard16.c').read_bytes()
a = b'static void *uplink_thread(void *opaque)'
b = b'd5_clock8_loop_end:'
assert old.split(a)[0] == new.split(a)[0]
assert old.split(b)[1] == new.split(b)[1]
start = b'static int write_downlink_frame('
end = b'static void reap_worker('
d6_old = old.split(start, 1)[1].split(end, 1)[0]
d6_new = new.split(start, 1)[1].split(end, 1)[0]
assert d6_old == d6_new
print('PASS all code outside uplink_thread unchanged')
print('PASS D6 write/pace/thread region unchanged sha256=' + hashlib.sha256(start+d6_new).hexdigest())
