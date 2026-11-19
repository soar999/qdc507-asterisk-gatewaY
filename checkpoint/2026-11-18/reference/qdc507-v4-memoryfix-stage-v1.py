#!/usr/bin/python3.8
# -*- coding: utf-8 -*-
"""QDC507 RemoteHangup v4: pin-checked OFFLINE source staging only.
No Docker, USB, ADB, Asterisk or system-directory writes. Python 3.8 compatible.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

LAB = Path('/volume2/docker/asterisk/qdc507-16k-lab')
SOURCE = LAB / 'qdc507-remotehangup-v4-buildctx/source/src'
STAGED = LAB / 'qdc507-v4-memoryfix-src-20261118'
MANIFEST = LAB / 'qdc507-v4-memoryfix-stage-20261118.json'

BASELINE = {'CMakeLists.txt': 'd496d9a193aa2fcf66eb8ff9a5cd7704006d90a1b6db7e709bfd8845b1b586d1', 'app.c': '0eb0d800060c7811de0d509c4a39ac64d9f3bcb1077a22ddbcc5758da8e9ab99', 'app.h': '6d5864f2f62b823109741823115bdd5461873007b04c1e724589ab8e3ffe2db9', 'ast_config.h': '57d757e736cd68fed2b5a6fb3283aaa4cd8db2a203074f089fe03f2aa1ea6f01', 'at_command.c': '69a0d766c84d1971f7682cc8ca9ce33081322b5044697f5445fff2069d3756dd', 'at_command.h': '9dbedfcc4e4c7f18fecff054934f0cd4ee29e8cd4ffd3b1b5b19273e9134d9b7', 'at_parse.c': '257c99d7a5b4b3346bd9191dc354677c08a11cd1dce75dcb7bd7ae48bc32a711', 'at_parse.h': '235573020bf3d526282d020ebcf176aa7a9029f03287c9074487ebe85aa13552', 'at_queue.c': '12228406ffc06708fccac4df84e3ddc9c8eb787f74d939d4640039de0576c56b', 'at_queue.h': '01494fa9a3f999a7a4bdb6588ce88c0f8d0d2776a9c2d76a325594add023e150', 'at_read.c': 'cd899e98b8a7859251678d12d7d3b128f3857125c38d59c4f1c081a8cdb70c59', 'at_read.h': '91c53af5d998581002feb78ca621f66cae1d25e0620af2452179f40e0e08be8c', 'at_response.c': 'af434671de79656e218e66bd0d5564487ed2f3aaff97bbb4458d3c63b571d060', 'at_response.h': '6c715052a30e0318faa62d6d371039ca9f22f5ae128cc21437cbd2db9f5a4c25', 'chan_quectel.c': '00dfa085cf3fbb46bfd4b669da9b4422196d6b51b3f4401236f4b6072abc5451', 'chan_quectel.h': '69891c58b0678b28e6ad3cf84c1761ef80b74cf394de3c18a5869f50a829314a', 'channel.c': '8f0d4b8f879a57a04a72264884b6dca1ff1a93683f5e69f3e5e4b8d6af01f3f5', 'channel.h': '8b18aa4a93d0c1eaa0fe7cffdb301808ede305dbb30b01491a92de32226326eb', 'char_conv.c': '4cbab1c0133012c965e15dc9ec4fa130f678a7c835e10324a36c5897bc01a138', 'char_conv.h': '90848c901d47c51da6300b95b5ab2b36f70bab918f312206777938f77af4e2b3', 'cli.c': '27a24bc1d5e8c3121ecc0577a3fad6cebe907218b68f34a790e676099d30d515', 'cli.h': '59ca1b759799a3a11a7dce58bda21073c46d9823e389fda806a7b7216f381bff', 'cpvt.c': '6489615edc7f59f73ed8dde963636a3e99e03dae6869eb18ec23603a6c330e10', 'cpvt.h': 'a8f1a71c9bc502ae204405ac7722b091b0f829403c0fd63106ba5497d1a1e2ef', 'dc_config.c': 'ee22294f7cc728b0f35119fc4e234c0742e6cb3b1478ccd1528211696b96a97e', 'dc_config.h': '34041d7a61a0d6e6fd1640d2eaa7323a68e3ef627ed2029c20736b4bbb12234c', 'error.c': '0a1d9ede8ae080ea7bd16399676146696ab070e53992ae33b83dec21442856d2', 'error.h': '8dd075a93610ae0923a463ae791db15e876de88b9d84a2fdef020b287e6254e7', 'eventfd.c': '589d85cdc785a80b0ed72f10a23f2478e608b71ce160c57e4cae7e46d3a9230b', 'eventfd.h': '63cc6e7f914dcd7c29d77519669b2ffc3dc89f72c4902f2cbece8177ad725c66', 'gsm7_luts.h': '34d31291b833932455864281f9a30e0892d283292e937d26bcad09b6ed91cdbd', 'helpers.c': 'c3c92174f9176f84ff9e8b332d686be2d86f44c8582691e3167473e967fbed1b', 'helpers.h': '0c754616b169d719f2d6d21d4ba5f34b00f4eeb64ecabfc3d645ec4be1fe7f12', 'memmem.c': 'd33f77c4e538f6edca5a0e04aead3be1e98bb3f71ec843e9975c14350ce09da2', 'memmem.h': '8c2a862a0b1c0ca7356e4b658dde4cd56d3ba71b227f160196c5f23f1e2cbde7', 'mixbuffer.c': 'd21d951e55c53d25930cf9e36ea3bab60501607fb229f09994623094779af37d', 'mixbuffer.h': '8bd73a900b0bae8d7a4429857362b2fef45d441509c970194b633a512a48f1ba', 'monitor_thread.c': '7e42c5634b177d79057a7eb4740e6ea556dca7b4b4a3d0616944f3e6ce8522bd', 'monitor_thread.h': '5466cca452d68b4ffaf50397c5b2360890c6ce4bbfc18c2d0cb84b1c198d06a1', 'msg_tech.c': 'fd4f155907642e3982b63aca2c585ca7f77f7c776a08064ba3711977052266c8', 'msg_tech.h': '246a94ed339fb2744408443980e5568ec4383151e22383a0f7442c42a7b18e7d', 'mutils.h': '3d10f185e664461b60bf984f178279814631671b2eecfcbffbbee1b75eaa0554', 'pcm.c': '1dc254768506a26299450a6a9fd38f1ddaef79b85e09e4935bca9a5c797dcc0e', 'pcm.h': 'a3305ef5a099f619fadef5ee2b57556d1a3c6f9c8370574afce46bc6bb1e5f21', 'pdu.c': 'd3d0f167efb95eba35a56338b9a34e51010a66e3670c349eb9ada367c9baeade', 'pdu.h': 'f50eccf0f80f6bc4fd245cd9b2846c2df1d18017fb4d20e23dd3a0669c2c8d2c', 'ptime-config.h.in': 'ccf28f2a5497cc471be8a5e424f6c3534db98dab4469a61bec4c4aa9ff3dde79', 'ringbuffer.c': 'c77643d6b6c8f09829099f19075b7c8a2e134d49fdc6134f7db9bf96f566ac33', 'ringbuffer.h': '654ebc0e0c7aad713331979c470666f001f479aeed67184e1786dd6c86008905', 'smsdb.c': '26e1d136dd3a728b2e2a79db40e0fbe8b89b69ab319ceed99ccf948e6b086f55', 'smsdb.h': '55cd2ce4768de7015225b04a08d631b5aa5f156081bd2a6f7a09d5de1f745d51', 'source-files.cmake': 'ddf541244042bdcc397a7a5abdb76ab28b1f624e66c8ba09d77d5ae121f2788e', 'tty.c': 'd3354928d80e79dd76cf9fad4cbe08b5376f133bf2aaf5d2d58907fac943ab8d', 'tty.h': '5f1a0021ae3ee97762df1b1e32878fb968856316d229d0f73cfe9af8aa7d40f2'}
FIXED = {'cpvt.c': '65b6ea46b3be16e8ff0bccc0ee615d4ad1db298a8029d9fee702783b45175b0b', 'monitor_thread.c': '266c95bd63c87c8ed80fcdb1830acec8152b5d67bab1dda9adc26873d7ac6c93'}

OLD_MONITOR = b'RAII_VAR(struct ast_str* const, result, ast_str_create(RINGBUFFER_SIZE), ast_free);'
NEW_MONITOR = b'/* at_combine_iov() may copy all RINGBUFFER_SIZE bytes and\n     * ast_str_truncate() writes one trailing NUL byte. */\n    RAII_VAR(struct ast_str* const, result, ast_str_create(RINGBUFFER_SIZE + 1u), ast_free);'

OLD_CPVT = b'    if (PVT_NO_CHANS(pvt)) {\n        pvt_on_remove_last_channel(pvt);\n        pvt_try_restate(pvt);\n    }\n\n    decrease_chan_counters(cpvt, pvt);\n    relink_to_sys_chan(cpvt, pvt);'
NEW_CPVT = b'    /* Drop the live channel count before deciding whether this was the\n     * final channel.  The old pre-decrement check could never be true for\n     * a cpvt still in pvt->chans, leaking silence_buf across calls. */\n    decrease_chan_counters(cpvt, pvt);\n    relink_to_sys_chan(cpvt, pvt);\n\n    if (PVT_NO_CHANS(pvt)) {\n        pvt_on_remove_last_channel(pvt);\n        pvt_try_restate(pvt);\n    }'

class Halt(Exception):
    pass

def need(cond, reason):
    if not cond:
        raise Halt(reason)

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def scan(d):
    need(d.is_dir() and not d.is_symlink(), 'SOURCE_DIRECTORY_MISSING_OR_SYMLINK: ' + str(d))
    files = sorted(x for x in d.iterdir() if x.is_file() and not x.is_symlink())
    need({x.name for x in files} == set(BASELINE), 'SOURCE_FILE_SET_DRIFT')
    need(not any(x.is_symlink() or x.is_dir() for x in d.iterdir()), 'SOURCE_NESTED_OR_SYMLINK_UNEXPECTED')
    return {x.name: sha(x) for x in files}

def check_source():
    hashes = scan(SOURCE)
    need(hashes == BASELINE, 'ORIGINAL_V4_SOURCE_HASH_DRIFT_REFUSE_PATCH')
    need(SOURCE.joinpath('monitor_thread.c').read_bytes().count(OLD_MONITOR) == 1, 'MONITOR_PATCH_ANCHOR_BAD')
    need(SOURCE.joinpath('cpvt.c').read_bytes().count(OLD_CPVT) == 1, 'CPVT_PATCH_ANCHOR_BAD')
    print('SOURCE_FILE_COUNT=' + str(len(hashes)))
    print('SOURCE_V4_HASH_PINS_OK=1')
    print('OFFLINE_PATCH_ANCHORS_OK=1')

def check_stage():
    hashes = scan(STAGED)
    expected = dict(BASELINE)
    expected.update(FIXED)
    need(hashes == expected, 'OFFLINE_STAGE_HASH_MISMATCH')
    print('OFFLINE_STAGE_VERIFIED=1')
    print('AT_2048_BOUNDARY_PATCH=1')
    print('CPVT_LAST_CHANNEL_CLEANUP_PATCH=1')
    print('PRODUCTION_MODULE_NOT_MODIFIED=1')

def stage():
    check_source()
    if STAGED.exists():
        check_stage()
        print('OFFLINE_STAGE_ALREADY_PRESENT=1')
        return
    need(not MANIFEST.exists(), 'STALE_MANIFEST_REQUIRES_REVIEW')
    holder = Path(tempfile.mkdtemp(prefix='.qdc507-v4-memoryfix-', dir=str(LAB)))
    try:
        work = holder / 'src'
        shutil.copytree(str(SOURCE), str(work))
        for name, before, after in (
            ('monitor_thread.c', OLD_MONITOR, NEW_MONITOR),
            ('cpvt.c', OLD_CPVT, NEW_CPVT),
        ):
            f = work / name
            old = f.read_bytes()
            need(old.count(before) == 1, 'PATCH_ANCHOR_MISSING_' + name)
            f.write_bytes(old.replace(before, after, 1))
            need(sha(f) == FIXED[name], 'PATCH_HASH_MISMATCH_' + name)
        actual = scan(work)
        expected = dict(BASELINE)
        expected.update(FIXED)
        need(actual == expected, 'STAGE_VERIFY_BEFORE_COMMIT_FAILED')
        os.rename(str(work), str(STAGED))
        # Manifest lives in the LAB root; only staged src files were changed.
        with MANIFEST.open('x') as out:
            json.dump({'original_source': str(SOURCE), 'staged_source': str(STAGED),
                       'original_sha256': BASELINE, 'staged_sha256': actual,
                       'source_changes': sorted(FIXED)}, out, indent=2, sort_keys=True)
            out.write('\n')
    finally:
        shutil.rmtree(str(holder), ignore_errors=True)
    check_stage()
    print('OFFLINE_PATCH_STAGED_OK=1')

def main():
    need(len(sys.argv) == 2 and sys.argv[1] in ('--check', '--stage', '--verify'),
         'USE --check / --stage / --verify')
    check_source()
    if sys.argv[1] == '--stage':
        stage()
    elif sys.argv[1] == '--verify':
        check_stage()
    elif STAGED.exists():
        check_stage()
    print('NO_LIVE_ASTERISK_ACTION=1')

if __name__ == '__main__':
    try:
        main()
    except (Halt, OSError, ValueError) as exc:
        print('OFFLINE_MEMORY_STAGE_REFUSED=' + str(exc), file=sys.stderr)
        sys.exit(2)
