#!/usr/bin/env python3
"""Narrow opt-in 16 kHz raw USB-serial media patch for accepted QDC507 chan_quectel.

Apply AFTER the existing qdc507_patch.py and production-20261018.patch to a
FRESH checkout pinned at AlchemillaHQ e1c027750bdf878bfe552341de457a9245358ce1.

Only the chan_quectel.c source is changed. Everything else (AT call control, CLCC,
SMS, and ext UAC path) remains byte-for-byte the accepted version.
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

EXPECTED = {
    'src/chan_quectel.c': 'd207f8227102e8e73afe86379d08d0ac4ff9d3cee1ea3bdd5e9e3d7c61e1f386',
    'src/tty.c': 'd3354928d80e79dd76cf9fad4cbe08b5376f133bf2aaf5d2d58907fac943ab8d',
}

FMT_START = 'const struct ast_format* pvt_get_audio_format('
FMT_END = 'static size_t pvt_get_audio_frame_size_r('

FMT_OLD = '''    } else {
        switch (CONF_UNIQ(pvt, uac)) {
            case TRIBOOL_NONE:
                return ast_format_slin;

            default:
                return ast_format_slin;
        }
    }
'''
FMT_NEW = '''    } else {
        switch (CONF_UNIQ(pvt, uac)) {
            /* Opt-in QDC507 serial PCM: never change the 8 kHz UAC modes. */
            case TRIBOOL_FALSE:
                return CONF_UNIQ(pvt, slin16) ? ast_format_slin16 : ast_format_slin;

            case TRIBOOL_NONE:
            default:
                return ast_format_slin;
        }
    }
'''
DATA_OLD = 'pvt->data_fd = tty_open(CONF_UNIQ(pvt, data_tty), (CONF_UNIQ(pvt, uac) == TRIBOOL_NONE) ? 2 : 0);'
DATA_NEW = '''/* QDC507 16k serial mode keeps the proven external-UAC AT port raw/no-RTSCTS. */
    pvt->data_fd = tty_open(CONF_UNIQ(pvt, data_tty),
        (CONF_UNIQ(pvt, uac) == TRIBOOL_NONE ||
         (CONF_UNIQ(pvt, uac) == TRIBOOL_FALSE &&
          !pvt->is_simcom && CONF_UNIQ(pvt, slin16))) ? 2 : 0);'''
AUDIO_OLD = 'pvt->audio_fd = tty_open(CONF_UNIQ(pvt, audio_tty), pvt->is_simcom);'
AUDIO_NEW = '''/* USB IF1 gadget serial carries binary PCM, without hardware RTS/CTS. */
        pvt->audio_fd = tty_open(CONF_UNIQ(pvt, audio_tty),
            (CONF_UNIQ(pvt, uac) == TRIBOOL_FALSE &&
             !pvt->is_simcom && CONF_UNIQ(pvt, slin16)) ? 2 : pvt->is_simcom);'''


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise ValueError('{} anchor count={}, wanted exactly 1'.format(label, n))
    return text.replace(old, new, 1)


def patch_text(source):
    first = source.find(FMT_START)
    last = source.find(FMT_END, first if first >= 0 else 0)
    if first < 0 or last < 0 or first >= last:
        raise ValueError('pvt_get_audio_format anchors missing')
    area = source[first:last]
    patched_area = replace_once(area, FMT_OLD, FMT_NEW, 'non-Simcom audio format')
    source = source[:first] + patched_area + source[last:]
    source = replace_once(source, DATA_OLD, DATA_NEW, 'AT tty_open call')
    source = replace_once(source, AUDIO_OLD, AUDIO_NEW, 'audio tty_open call')
    if 'case TRIBOOL_FALSE:\n                return CONF_UNIQ(pvt, slin16)' not in source:
        raise ValueError('slin16 feature marker missing after patch')
    return source


def selftest():
    fixture = ('intro\n' + FMT_START + '\n' + FMT_OLD + '\n' + FMT_END + '\n'
               + DATA_OLD + '\n' + AUDIO_OLD + '\nend\n')
    result = patch_text(fixture)
    assert FMT_NEW in result
    assert DATA_NEW in result
    assert AUDIO_NEW in result
    assert FMT_OLD not in result
    assert DATA_OLD not in result
    assert AUDIO_OLD not in result
    try:
        patch_text(result)
    except ValueError:
        pass
    else:
        raise AssertionError('double patch was not rejected')
    try:
        patch_text(fixture.replace(AUDIO_OLD, 'unrecognized audio open'))
    except ValueError:
        pass
    else:
        raise AssertionError('unknown source was not rejected')
    print('SERIAL16K_PATCH_SELFTEST_PASSED=1', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selftest', action='store_true')
    parser.add_argument('--source-root', default=os.environ.get(
        'QDC507_SOURCE_ROOT', '/usr/src/asterisk-chan-quectel'))
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return

    root = Path(args.source_root).resolve()
    for rel, expected in EXPECTED.items():
        f = root / rel
        if not f.is_file():
            raise ValueError('ACCEPTED_SOURCE_NOT_FOUND: {}'.format(f))
        found = sha256(f.read_bytes())
        if found != expected:
            raise ValueError('ACCEPTED_SOURCE_SHA_MISMATCH: {} found {}'.format(rel, found))
        print('ACCEPTED_SOURCE_SHA_OK={}'.format(rel), flush=True)

    # Ensure the upstream tty.c mode=2 is already raw, without CRTSCTS.
    tty = (root / 'src/tty.c').read_text()
    if 'case 2:\n            term_attr.c_cflag = B115200 | CS8 | CREAD | CLOCAL;' not in tty:
        raise ValueError('TTY_MODE2_RAW_SEMANTICS_CHANGED')
    print('TTY_MODE2_NO_RTSCTS=1', flush=True)

    target = root / 'src/chan_quectel.c'
    old_bytes = target.read_bytes()
    source = old_bytes.decode('utf-8')
    out = patch_text(source).encode('utf-8')
    if sha256(out) == sha256(old_bytes):
        raise ValueError('PATCH_NO_CHANGE')
    # Atomic replace is limited to the throwaway build checkout.
    temp = target.with_name(target.name + '.serial16k-lab.tmp')
    if temp.exists():
        raise ValueError('STALE_TEMP_FILE: {}'.format(temp))
    temp.write_bytes(out)
    os.replace(str(temp), str(target))
    print('MODIFIED_ONLY=src/chan_quectel.c', flush=True)
    print('SERIAL16K_SOURCE_SHA256={}'.format(sha256(out)), flush=True)
    print('PRESERVE_UAC_EXT_8K=1', flush=True)
    print('QDC507_SERIAL16K_OPT_IN=1', flush=True)
    print('AT_SMS_CALL_CONTROL_UNMODIFIED=1', flush=True)
    print('PATCH_OK=1', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_ABORTED=' + repr(exc), file=sys.stderr, flush=True)
        sys.exit(2)
