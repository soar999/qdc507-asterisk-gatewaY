#!/usr/bin/env python3
"""V44 separate-file restage candidate. Offline library: no live CLI.
Caller must hold outer exclusive recovery gate, block admission, and validate
Guard16 V2/SMS/USB identity. The helper never starts audio or publishes READY.
"""
import hashlib, pathlib, re, shlex
P=pathlib.Path
DEVICE='6352805ba1461451186d52eaa0adbb945abca395c2e9ce6c5482c9c792db1ffb'
HOST='84a35a708defd000dfee0d4ef599d858f32bbe829646e10030fb29878b1db45c'
GATE='12e9d456dd210d19eb95f897ca16ccefc96c9b074f5432ea6d08ec3f702366f4'
LIFE='b65aff643318415713085c004a51427daf4e88613145d216bec9f9ecb30ae784'
D4='dc7d080922a6af77128f227b9cee1efc7a6b6d16b42bce859f636ac99e6c523c'
D5='0e9272cdd832759c1704857d4a2c4abd0323a3af41d2529f84058b4431edc913'
V43='ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1'
PINS={'device-v44-candidate.sh':DEVICE,'preaudio-v44-candidate.py':HOST,'adb_coordination.py':GATE,'lifecycle.py':LIFE}
class Stop(RuntimeError): pass
def need(ok,msg):
    if not ok: raise Stop(msg)
def verified(p,h):
    p=P(p);need(p.is_file() and not p.is_symlink(),'UNSAFE_SOURCE_'+p.name)
    need(hashlib.sha256(p.read_bytes()).hexdigest()==h,'SOURCE_HASH_'+p.name)
    return p

def restage(root, rpc, *, authorized, generation, expected_generation, mode):
    """rpc accepts ('shell',script) or ('push',local,remote), returns exact stdout.
    Production adapter must use the pinned ADB serial, timeouts and outer gate.
    No remote deletion or override of unknown bytes. A verified upload may resume.
    """
    need(mode()=='16k','NOT_16K')
    for n,h in PINS.items():verified(P(root)/n,h)
    def guard():
        need(authorized(),'OUTER_TRANSACTION_NOT_AUTHORIZED')
        need(generation()==expected_generation,'GENERATION_CHANGED')
    def run(*args):
        guard(); result=rpc(*args); guard();return result.strip()
    # Same owner/lock as V43: never create a second audio ownership domain.
    common=f'''set -eu
D=/tmp/qdc16k-preaudio-manual
B=/tmp/qdc507-serial16k-v7
[ -d "$D" ]; [ ! -L "$D" ]; [ -d "$D/state" ]; [ ! -L "$D/state" ]
[ ! -e "$D/state/owner" ]; [ ! -L "$D/state/owner" ]
[ ! -e "$D/state/lock" ]; [ ! -L "$D/state/lock" ]
[ "$(cat /sys/class/android_usb/f_audio/audio_enable)" = 0 ]
for n in 4p 4c 5p 6c; do [ "$(cat /proc/asound/card0/pcm$n/sub0/hw_params)" = closed ]; done
[ -x "$B/bridge-d4" ]; [ -x "$B/bridge-d5" ]
[ "$(sha256sum "$B/bridge-d4" | cut -d ' ' -f1)" = {D4} ]
[ "$(sha256sum "$B/bridge-d5" | cut -d ' ' -f1)" = {D5} ]
[ -f "$D/device-v43.sh" ]; [ ! -L "$D/device-v43.sh" ]
[ "$(sha256sum "$D/device-v43.sh" | cut -d ' ' -f1)" = {V43} ]
F="$D/device-v44.sh"; U="$F.upload"
[ ! -L "$F" ]; [ ! -L "$U" ]
'''
    check=common+f'''if [ -e "$F" ]; then
 [ ! -e "$U" ]; [ -f "$F" ]; [ -x "$F" ]
 [ "$(sha256sum "$F" | cut -d ' ' -f1)" = {DEVICE} ]
 echo READY
elif [ -e "$U" ]; then
 [ -f "$U" ]; [ "$(sha256sum "$U" | cut -d ' ' -f1)" = {DEVICE} ]
 echo UPLOAD_VERIFIED
else echo MISSING; fi
'''
    state=run('shell',check)
    if state=='READY':return 'ALREADY_READY_NO_WRITE'
    need(state in ('UPLOAD_VERIFIED','MISSING'),'UNKNOWN_RESTAGE_STATE')
    if state=='MISSING':run('push',str(P(root)/'device-v44-candidate.sh'),'/tmp/qdc16k-preaudio-manual/device-v44.sh.upload')
    finish=common+f'''[ ! -e "$F" ]; [ -f "$U" ]
[ "$(sha256sum "$U" | cut -d ' ' -f1)" = {DEVICE} ]
chmod 500 "$U"
mv "$U" "$F"
[ -x "$F" ]; [ "$(sha256sum "$F" | cut -d ' ' -f1)" = {DEVICE} ]
echo INSTALLED
'''
    need(run('shell',finish)=='INSTALLED','INSTALL_UNCONFIRMED')
    need(run('shell',check)=='READY','FINAL_READBACK_FAILED')
    return 'RESTAGED_VERIFIED'
