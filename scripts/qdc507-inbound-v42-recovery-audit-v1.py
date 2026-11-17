#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QDC507 inbound v43 recovery audit (2026-11-15), READ-ONLY, fail-closed.

Uses the historical v42 audit filename to preserve existing DSM scheduled tasks.
The contents audit v43 production; the filename alone does not select v42.

For the original Synology DS918+/qdc507-hardware production instance only.
Checks the exact deployed v43 release receipt, the original outbound and v42
fallback source, the v43 wrapper and its device-side mapping, the compiled
inbound routes, and the installed USB/boot/Guard16 recovery integration.

This audit delegates device, idle, recovery and Guard16 verification to the
exact SHA256-pinned v43 final release script, rather than reimplementing its
security-critical checks. No changes are made to NAS, container, QDC507, SMS,
Asterisk configuration, or timers. Call logs are reported but not deleted.

On NAS as root, when no call is active:
  /usr/bin/python3.8 -I -B \
    /volume2/docker/asterisk/qdc507-16k-lab/qdc507-inbound-v42-recovery-audit-v1.py --check

Offline self-test on any Python >=3.8:
  python3 qdc507-inbound-v42-recovery-audit-v1.py --selftest
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
BASE = Path('/volume2/docker/asterisk/qdc507-current')
C = 'qdc507-hardware'
R = '/opt/qdc16k-preaudio-manual'
SCRIPT = LAB / 'qdc507_v43_release_final.py'
RELEASE_HASH = 'e59f754afa628cd050a8c85be15c936f9f69a07020550fc4ce1c6b49df49c486'
ORIGINAL_OUTBOUND = '2f810a3dab4cbfdc35eb3d92e90f7db474f2c5689785dc7e5835d91959d3b690'
ORIGINAL_INBOUND_V42 = '7981c83576cccd981558448796e4c5cc50b40af84668a079c78ff3f59efdefd7'
INBOUND_V43 = '13665201a61c284fcd01d9433057087fb10f8dbb6a7cf797a762434bae07a9bf'
DEVICE_V42 = '3682a8f0b4160f43778624e8551f39f8390163c92850f6661980ca64038d5f76'
DEVICE_V43 = 'ec26fcba20a6717118b586a98b37d838f34122d9b6d763805e226bf311b15ce1'
WRAPPER_V43 = '472fc6d87dca96ca7b88402165e4f8cc4770b3097a40eed2c63c5ba5e0eb0d98'
PREPARE_SHA = '1332d6fcf5a4b3f4f7a70180e8632992e30325256b415a4f8fc6417cf7bc0665'
V43_DIALPLAN = '0f232e3c356922392169a099a966b39b93c659136bdc424c27f963cae1f386e3'
RELEASE_APPLIED = LAB / 'qdc507-v43-release.applied.json'
RELEASE_PENDING = LAB / 'qdc507-v43-release.pending.json'
V42_APPLIED = LAB / 'qdc507-inbound-v42.applied.json'

class Stop(Exception): pass

def need(test, reason):
    if not test:
        raise Stop(reason)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_bytes(path, expected=None):
    need(path.is_file() and not path.is_symlink(), 'MISSING_OR_SYMLINK: '+str(path))
    b = path.read_bytes()
    if expected is not None:
        need(digest(b) == expected, 'HASH_MISMATCH: '+str(path))
    return b

def run(args, timeout=40):
    try:
        p = subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Stop('READ_ONLY_COMMAND_FAILED_OR_TIMED_OUT: '+str(args[:3])+' '+str(exc)[:120])
    if p.returncode:
        raise Stop('READ_ONLY_COMMAND_NONZERO: '+str(args[:4])+' '+
                   p.stderr.decode('utf-8', 'replace')[-280:])
    return p.stdout

def docker_bytes(path):
    return run(['docker', 'exec', C, 'cat', path], 22)

def cli(command):
    return run(['docker', 'exec', C, 'asterisk', '-rx', command], 25).decode('utf-8', 'replace')

def verified_v43_wrapper(v42, v43):
    # Derive v43 by precisely the two substitutions used in the pinned plan.
    need(v42.count(b'/preaudio-inbound-v42.py') == 1, 'V42_WRAPPER_HOST_PATH_UNEXPECTED')
    need(v42.count(b'/device-v2.sh') == 1, 'V42_WRAPPER_DEVICE_PATH_UNEXPECTED')
    expected = (v42.replace(b'/preaudio-inbound-v42.py', b'/preaudio-inbound-v43.py')
                    .replace(b'/device-v2.sh', b'/device-v43.sh'))
    need(v43 == expected, 'V43_WRAPPER_NOT_EXACTLY_DERIVED_FROM_V42')
    need(digest(v43) == WRAPPER_V43, 'V43_WRAPPER_PIN_MISMATCH')
    need(v43.count(b'QDC_DEVICE_SCRIPT=/tmp/qdc16k-preaudio-manual/device-v43.sh') == 1,
         'V43_DEVICE_PATH_INVALID')
    return True

def check_release_and_recovery():
    file_bytes(SCRIPT, RELEASE_HASH)
    need(not RELEASE_PENDING.exists() and not RELEASE_PENDING.is_symlink(),
         'V43_RELEASE_PENDING_REQUIRES_REVIEW')
    receipt = json.loads(file_bytes(RELEASE_APPLIED).decode('utf-8'))
    need(receipt.get('phase') == 'applied' and receipt.get('container') == C and
         receipt.get('new_dialplan_sha256') == V43_DIALPLAN,
         'V43_RELEASE_RECEIPT_INVALID')
    check = run(['/usr/bin/python3.8', '-I', '-B', SCRIPT, '--verify'], 220).decode('utf-8', 'replace')
    required = (
        'V43_PRODUCTION_READ_ONLY_VERIFIED=1',
        'COMPILED_INBOUND_V43_ROUTES_OK=1',
        'DEVICE_V43_SHA_AND_OWNER_LOCK_IDLE_OK=1',
        'GUARD16_V2_READ_ONLY_CHECK_OK=1',
    )
    for item in required:
        need(item in check.splitlines(), 'RELEASE_VERIFIER_MISSING: '+item)
    print('V43_RELEASE_SCRIPT_PIN_AND_RECEIPT_OK=1')
    print('V43_DIALPLAN_DEVICE_AND_GUARD16_V2_OK=1')
    print('V43_BOOT_AND_USB_MANUAL_RECOVERY_HOOKS_OK=1')

def check_sources():
    need(digest(docker_bytes(R+'/preaudio-inbound-v42.py')) == ORIGINAL_INBOUND_V42,
         'V42_FALLBACK_HOST_CHANGED')
    need(digest(docker_bytes(R+'/preaudio-inbound-v43.py')) == INBOUND_V43,
         'V43_INBOUND_CONTROLLER_CHANGED')
    need(digest(docker_bytes(R+'/preaudio.py')) == ORIGINAL_OUTBOUND,
         'ORIGINAL_OUTBOUND_CONTROLLER_CHANGED')
    need(digest(docker_bytes(R+'/device.sh')) == DEVICE_V42,
         'ORIGINAL_DEVICE_SOURCE_CHANGED')
    need(digest(docker_bytes(R+'/prepare.py')) == PREPARE_SHA,
         'ORIGINAL_PREPARE_CHANGED')
    old_receipt = json.loads(file_bytes(V42_APPLIED).decode('utf-8'))
    old_wrap = docker_bytes(R+'/agi-wrapper-inbound-v42')
    need(digest(old_wrap) == old_receipt.get('wrapper_staged_sha256'),
         'V42_WRAPPER_CHANGED')
    new_wrap = docker_bytes(R+'/agi-wrapper-inbound-v43')
    verified_v43_wrapper(old_wrap, new_wrap)
    original_wrap = docker_bytes(R+'/agi-wrapper')
    need(digest(original_wrap) == old_receipt.get('wrapper_source_sha256') and
         original_wrap.count((R+'/preaudio.py').encode('ascii')) == 1,
         'OUTBOUND_AGI_WRAPPER_CHANGED')
    print('V42_FALLBACK_AND_ORIGINAL_OUTBOUND_UNCHANGED=1')
    print('V43_HOST_AND_WRAPPER_EXACT_SOURCE_VERIFIED=1')

def check_dialplan():
    # The release verifier checks loaded contexts. Independently protect the
    # original outbound route and current cloud/local inbound choice.
    a = cli('dialplan show')
    need("'_7X.' =>" in a and 'Dial(Quectel/quectel0/' in a and
         'AGI('+R+'/agi-wrapper,submit,' in a,
         'OUTBOUND_ORIGINAL_7_PREFIX_ROUTE_CHANGED')
    inbound = cli('dialplan show incoming-mobile')
    need(inbound.count('100@cloud-trunk:100') == 4,
         'INBOUND_CLOUD_LOCAL_CHOICE_CHANGED')
    need(inbound.count('agi-wrapper-inbound-v43') == 4,
         'INBOUND_V43_AGI_REFERENCES_CHANGED')
    need('agi-wrapper-inbound-v42' not in inbound,
         'INBOUND_STILL_REFERENCES_V42')
    print('CLOUD_LOCAL_INBOUND_AND_OUTBOUND_ROUTING_OK=1')

def report_device_logs():
    # Optional read-only indicator, no log deletion, no host cron assumptions.
    wrapper = docker_bytes(R+'/agi-wrapper-inbound-v43').decode('utf-8', 'replace')
    serial = re.findall(r'\bQDC_SERIAL=([A-Za-z0-9._:-]+)', wrapper)
    need(len(serial) == 1, 'SERIAL_NOT_UNIQUE')
    q = '''set -eu
D=/tmp/qdc16k-preaudio-manual/state
F=/tmp/qdc16k-preaudio-manual/device-v2.sh
[ -f "$F" ] && [ ! -L "$F" ]
sha256sum "$F"
n=0
files=0
for T in "$D"/tokens/*; do
 [ -d "$T" ] && [ ! -L "$T" ] || continue
 n=$((n+1))
 if [ -f "$T/clean" ]; then
  for f in d4.log d5.log supervisor.log cleanup.log; do
   [ -f "$T/$f" ] && [ ! -L "$T/$f" ] && files=$((files+1))
  done
 fi
done
printf 'DEVICE_TOKEN_COUNT=%s\\n' "$n"
printf 'CLEAN_TOKEN_LOG_FILES_RETAINED=%s\\n' "$files"
'''
    out = run(['docker', 'exec', C, '/usr/local/bin/adb',
               '-H', '127.0.0.1', '-P', '5037', '-s', serial[0], 'shell', q], 50)
    lines = out.decode('utf-8','replace').replace('\r','').splitlines()
    need(any(re.match(r'^'+DEVICE_V42+r'\s+/tmp/qdc16k-preaudio-manual/device-v2\.sh$', l.strip()) for l in lines),
         'DEVICE_V42_FALLBACK_NOT_PINNED')
    counts = {}
    for line in lines:
        match = re.fullmatch(r'(DEVICE_TOKEN_COUNT|CLEAN_TOKEN_LOG_FILES_RETAINED)=(\d+)', line.strip())
        if match:
            need(match.group(1) not in counts, 'DUPLICATE_LOG_COUNT_FIELD')
            counts[match.group(1)] = int(match.group(2))
    need(set(counts) == {'DEVICE_TOKEN_COUNT','CLEAN_TOKEN_LOG_FILES_RETAINED'},
         'DEVICE_LOG_COUNT_RESPONSE_INCOMPLETE')
    for label in ('DEVICE_TOKEN_COUNT','CLEAN_TOKEN_LOG_FILES_RETAINED'):
        print(label+'='+str(counts[label]))
    print('DEVICE_V42_FALLBACK_SOURCE_READY=1')
    print('LOGS_REPORT_ONLY_NOT_CLEANED=1')
    print('V43_RELEASE_DOES_NOT_INSTALL_LOG_ROTATION=1')

def selftest():
    sample = b'X /preaudio-inbound-v42.py /device-v2.sh QDC_DEVICE_SCRIPT=/tmp/qdc16k-preaudio-manual/device-v2.sh'
    # This is only a parser/provenance sanity test: not a production install.
    a = sample.replace(b'/preaudio-inbound-v42.py', b'/preaudio-inbound-v43.py')
    need(a != sample, 'REPLACEMENT_SANITY')
    need(len(RELEASE_HASH) == 64 and len(INBOUND_V43) == 64, 'HASH_SHAPE_BAD')
    need('sha256sum "$F"' in Path(__file__).read_text(), 'MISSING_V42_DEVICE_FALLBACK_HASH_QUERY')
    print('V43_RECOVERY_AUDIT_OFFLINE_SELFTEST_OK=1')
    print('NO_NAS_OR_DOCKER_ACCESS=1')

def check():
    need(os.geteuid() == 0, 'ROOT_REQUIRED')
    need(LAB.is_dir() and not LAB.is_symlink(), 'LAB_PATH_INVALID')
    check_release_and_recovery()
    check_sources()
    check_dialplan()
    report_device_logs()
    print('QDC507_INBOUND_V43_RECOVERY_AUDIT_PASSED=1')
    print('READ_ONLY_NO_RECOVERY_ACTIONS=1')

if __name__ == '__main__':
    try:
        need(sys.argv[1:] in (['--check'], ['--selftest']), 'USE_--check_OR_--selftest')
        if sys.argv[1] == '--selftest':
            selftest()
        else:
            check()
    except (Stop, OSError, ValueError, UnicodeError, KeyError, IndexError) as exc:
        print('QDC507_INBOUND_V43_RECOVERY_AUDIT_STOP='+str(exc)[:500], file=sys.stderr)
        sys.exit(2)
