#!/usr/bin/env python3
"""QDC507 dual-mode image V2: preserve LIVE 8k driver; offline Synology Python 3.8.

This program ONLY prepares and builds a local Docker image holding both the
live production container filesystem 8k module and verified V7 16k module.
It does not change or restart production, SMS, the modem, config or USB mapping.

--check    Inspect local images and active production (read-only, ephemeral
           --network none image inspections only). Print source hashes.
--prepare  Create an isolated Docker build context below QDC507 LAB root.
--build    Build isolated image from already present local images (--pull=false,
           --network=none). No modem access or service change.
--verify   Verify built image and exercise both mode selections in isolated,
           --network none disposable containers, WITHOUT starting Asterisk.

The image retains the V7 image's original ENTRYPOINT/CMD, and ships an OPT-IN
mode-boot wrapper. Production integration, configuration/USB bindings, and
reboot recovery are a SEPARATE guarded step, NOT claimed here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

ROOT = Path('/volume2/docker/asterisk/qdc507-16k-lab')
BUILD_DIR = ROOT / 'qdc507-dualmode-image-build-v2'
PROD = 'qdc507-hardware'
IMG8 = 'qdc507-asterisk:22.10.1-qdc507-clccsafe4'
IMG16 = 'qdc507-asterisk:22.10.1-serial16k-v7-lab'
IMGDUAL = 'qdc507-asterisk:22.10.1-qdc507-dualmode-guard16-v2'
MODULE = '/usr/lib/asterisk/modules/chan_quectel.so'
MANIFEST = 'dualmode-build-manifest.json'
LIVE_MODULE_NAME = 'qdc507-live-production-8k-chan_quectel.so'
PAYLOAD = Path(__file__).resolve().parent / 'qdc507-preaudio-runtime'
PIN16 = 'c3e9638d45da696096240a8c455b64a0d3c747a4bcfc41fbb22d75453c2b9fdf'
V7_SHA = 'ef6c208c3f67b969d699b2c8dab6fcba257af1c1424792561d438408580d4029'
GUARD_SHA = '7375b61dc6e8661d02ef17f98097ccae3598782c466b06a50c90ff5454515e71'
GUARD = ROOT / ('qdc507-16k-d5-clock8-guard16-build-v1/output/'
                'mavo-pcm-bridge-d5d6-16k-clock8-guard16-LAB.armv7')
CAND_META = ROOT / 'qdc507-prod16k-candidate-v1/.production-candidate-manifest.json'


def emit(k, v=1):
    if isinstance(v, (dict, list, tuple)):
        v = json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    print('{}={}'.format(k, v), flush=True)


def need(c, reason):
    if not c:
        raise RuntimeError(reason)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def cmd(args, timeout=50):
    p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                       timeout=timeout)
    need(p.returncode == 0, 'COMMAND_FAILED_{}_{}_rc{}_{}'.format(
        args[0], args[1] if len(args)>1 else '', p.returncode,
        (p.stderr + p.stdout)[-350:].replace('\n', ' ')))
    return p.stdout


def inspect(args):
    rows = json.loads(cmd(args))
    need(isinstance(rows, list) and len(rows) == 1, 'DOCKER_INSPECT_FORMAT_CHANGED')
    return rows[0]


def image_fingerprint(img):
    # No /dev, mounts, network, daemon connection or running Asterisk.
    output = cmd(['docker', 'run', '--rm', '--pull=never', '--network', 'none',
                  '--entrypoint', '/bin/sh', img, '-c',
                  'sha256sum '+MODULE+'; asterisk -V'], timeout=70)
    lines = output.strip().splitlines()
    need(len(lines) >= 2, 'IMAGE_FINGERPRINT_UNEXPECTED_'+img)
    m = re.fullmatch(r'([a-f0-9]{64})\s+\S+', lines[0].strip())
    need(bool(m), 'IMAGE_MODULE_HASH_MISSING_'+img)
    version = next((x.strip() for x in lines[1:] if x.strip().startswith('Asterisk ')), '')
    need(re.match(r'^Asterisk 22\.10\.1(?:\s|$)', version),
         'ASTERISK_VERSION_NOT_22_10_1_'+img+'_'+version[:50])
    return {'module_sha': m.group(1), 'asterisk_version': version}


def verify_stage_candidate(id16):
    # Stage candidate was previously verified on this NAS; bind to exact image ID.
    need(CAND_META.is_file() and not CAND_META.is_symlink(),
         'PROD16K_CANDIDATE_MANIFEST_NOT_FOUND_RUN_EXISTING_STAGE_VERIFY')
    data = json.loads(CAND_META.read_text(encoding='utf-8'))
    sources = data.get('sources') or {}
    need(sources.get('v7_image_id') == id16,
         'CANDIDATE_V7_IMAGE_ID_CHANGED_RECHECK_STAGE')
    need(sources.get('guard16_sha') == GUARD_SHA,
         'CANDIDATE_GUARD_SHA_CHANGED')


def facts():
    need(os.geteuid() == 0, 'SUDO_REQUIRED')
    need(ROOT.is_dir() and not ROOT.is_symlink(), 'LAB_ROOT_NOT_FOUND')
    need(GUARD.is_file() and not GUARD.is_symlink() and sha256(GUARD) == GUARD_SHA,
         'GUARD16_BINARY_MISSING_OR_HASH_CHANGED')
    p = inspect(['docker', 'inspect', PROD])
    need(bool((p.get('State') or {}).get('Running')), 'PROD_NOT_RUNNING')
    live_tag = (p.get('Config') or {}).get('Image')
    import importlib.util
    spec = importlib.util.spec_from_file_location('qdc_build_cutover', str(ROOT / 'qdc507-mode-cutover-v1.py'))
    cutover = importlib.util.module_from_spec(spec); spec.loader.exec_module(cutover)
    need(live_tag in (IMG8, cutover.DUAL), 'PRODUCTION_IMAGE_TAG_CHANGED')
    if live_tag != IMG8:
        # A 16k live module is not the 8k source. The immutable 8k slot
        # remains available without restarting or switching production.
        state = cutover.state_read()
        need(state and state.get('phase') == 'COMPLETE', 'CUTOVER_NOT_COMPLETE')
        cutover.verify_containers(state)
    i8 = inspect(['docker', 'image', 'inspect', IMG8])
    i16 = inspect(['docker', 'image', 'inspect', IMG16])
    if live_tag == IMG8:
        need(p.get('Image') == i8.get('Id'), 'PRODUCTION_AND_8K_IMAGE_DIFFER')
    need(i8.get('Architecture') == i16.get('Architecture') == 'amd64',
         'SOURCE_IMAGE_ARCH_MISMATCH')
    u16 = str((i16.get('Config') or {}).get('User') or 'root')
    need(u16 in ('root', '0', '0:0'), '16K_IMAGE_DEFAULT_USER_NOT_ROOT')
    need(sha256(PAYLOAD / 'chan_quectel-16k.so') == PIN16, 'ACCEPTED_16K_PAYLOAD_CHANGED')
    verify_stage_candidate(i16.get('Id'))
    h8, h16 = image_fingerprint(IMG8), image_fingerprint(IMG16)
    need(h16['module_sha'] == V7_SHA, 'V7_16K_MODULE_HASH_CHANGED')
    source_path = MODULE if live_tag == IMG8 else '/opt/qdc507/modes/8k/chan_quectel.so'
    # ADB is a runtime bind (helpers/adb), not an image build dependency.
    live = cmd(['docker', 'exec', PROD, '/bin/sh', '-c',
                'test -x /usr/local/bin/adb && sha256sum '+source_path], timeout=18)
    livehash = live.strip().split()[0] if live.strip() else ''
    need(livehash == '9899154c0869a7e7a62112c2cace1e3ebdf2d22a245371559fab15e912de320f',
         'PINNED_8K_SOURCE_CHANGED')
    # The active container can legitimately carry a post-image production fix.
    # Never silently fall back to the older image's 8k driver.
    emit('LIVE_8K_MODULE_SHA256', livehash)
    emit('ORIGINAL_8K_IMAGE_MODULE_SHA256', h8['module_sha'])
    emit('LIVE_8K_DIFFERS_FROM_BASE_IMAGE', int(livehash != h8['module_sha']))
    runtime_version = cmd(['docker','exec', PROD, 'asterisk', '-V'], timeout=15).strip()
    need(runtime_version == h8['asterisk_version'], 'LIVE_8K_ASTERISK_VERSION_DIFFERS_FROM_IMAGE')
    need(livehash != h16['module_sha'], 'LIVE_8K_AND_16K_MODULES_IDENTICAL')
    need(h8['asterisk_version'] == h16['asterisk_version'],
         'ASTERISK_SOURCE_VERSIONS_DIFFER')
    emit('PINNED_8K_SOURCE_PATH', source_path)
    emit('PINNED_8K_SOURCE_VERIFIED', 1)
    emit('V7_16K_DRIVER_PIN_VERIFIED', 1)
    emit('SOURCE_ASTERISK_VERSION', h16['asterisk_version'])
    emit('GUARD16_AUDIO_PIN_VERIFIED', 1)
    emit('PROD8K_MODULE_SHA256', livehash)
    emit('V7_16K_MODULE_SHA256', h16['module_sha'])
    emit('PROD_NOT_CHANGED', 1)
    emit('LIVE_LOADED_MODULE_IDENTITY_NOT_PROVEN', 1)
    return {'source_8k_image_id': i8['Id'], 'source_16k_image_id': i16['Id'],
            'source_8k_path': source_path,
            'source_8k_container_image_id': p['Image'],
            'live_8k_container_id': p['Id'],
            'source_8k_image_module_sha256': h8['module_sha'],
            'source_8k_module_sha256': livehash,
            'source_16k_module_sha256': PIN16,
            'asterisk_version': h16['asterisk_version'],
            'v7_image_original_entrypoint': (i16.get('Config') or {}).get('Entrypoint'),
            'v7_image_original_cmd': (i16.get('Config') or {}).get('Cmd'),
            'guard16_sha256': GUARD_SHA}


def make_dockerfile(info):
    sha8, sha16 = info['source_8k_module_sha256'], info['source_16k_module_sha256']
    # Mode selection is opt-in: image's original ENTRYPOINT and CMD untouched.
    lines = [
        '# AUTO-GENERATED QDC507 DUAL-MODE IMAGE. OFFLINE BUILD ONLY.',
        'FROM {} AS qdc_source_16k'.format(info['source_16k_image_id']),
        'FROM {}'.format(info['source_16k_image_id']),
        'USER root',
        'RUN mkdir -p /opt/qdc507/modes/8k /opt/qdc507/modes/16k',
        'COPY {} /opt/qdc507/modes/8k/chan_quectel.so'.format(LIVE_MODULE_NAME),
        'COPY qdc507-preaudio-runtime/chan_quectel-16k.so /opt/qdc507/modes/16k/chan_quectel.so',
        'COPY qdc507-preaudio-runtime/ /opt/qdc16k-preaudio-manual/',
        'COPY entrypoint.sh /usr/local/bin/entrypoint.sh',
        'RUN chmod 755 /usr/local/bin/entrypoint.sh /opt/qdc16k-preaudio-manual/agi-wrapper && chmod 644 /opt/qdc16k-preaudio-manual/*.py && command -v python3',
        'COPY qdc507-mode-boot.sh /usr/local/sbin/qdc507-mode-boot.sh',
        'RUN chmod 755 /usr/local/sbin/qdc507-mode-boot.sh',
        ' && test "$(sha256sum /opt/qdc507/modes/8k/chan_quectel.so | cut -d \' \' -f1)" = "{}"'.format(sha8),
        ' && test "$(sha256sum /opt/qdc507/modes/16k/chan_quectel.so | cut -d \' \' -f1)" = "{}"'.format(sha16),
        ' && cp /opt/qdc507/modes/16k/chan_quectel.so {}'.format(MODULE),
        ' && ldd /opt/qdc507/modes/8k/chan_quectel.so > /tmp/qdc507-8k-ldd.txt',
        ' && ! grep -Fq "not found" /tmp/qdc507-8k-ldd.txt',
        ' && ldd /opt/qdc507/modes/16k/chan_quectel.so > /tmp/qdc507-16k-ldd.txt',
        ' && ! grep -Fq "not found" /tmp/qdc507-16k-ldd.txt',
        ' && rm -f /tmp/qdc507-8k-ldd.txt /tmp/qdc507-16k-ldd.txt',
        '# No ENTRYPOINT/CMD override. Production cutover must install and',
        '# invoke mode-boot BEFORE starting Asterisk and retain original boot arguments.',
    ]
    # Use explicit backslash continuation on RUN; never combine Dockerfile
    # commands via accidental Python string literal line continuation.
    start = lines.index('RUN chmod 755 /usr/local/sbin/qdc507-mode-boot.sh')
    end = lines.index(' && rm -f /tmp/qdc507-8k-ldd.txt /tmp/qdc507-16k-ldd.txt')
    for i in range(start, end):
        lines[i] += ' ' + chr(92)
    return '\n'.join(lines) + '\n'


def make_boot(info):
    # Only used by a FUTURE mode manager as an explicit pre-Asterisk entrypoint.
    # Self-test also exercises it in a disposable, no-network container.
    return '''#!/bin/sh\n# QDC507 dual-mode module selector; must run before Asterisk begins.\nset -eu\nSELF_TEST=0\nif [ "${{1:-}}" = "--self-test" ]; then SELF_TEST=1; shift; fi\nif [ "$(id -u)" != 0 ]; then echo MODE_BOOT_REQUIRES_ROOT >&2; exit 41; fi\nif [ "$SELF_TEST" = 1 ]; then\n    MODE="${{QDC507_AUDIO_MODE:-}}"\nelse\n    # Configuration source is an explicit dedicated readonly bind mount.\n    # Never silently infer 8k/16k from stale environment or a running call.\n    test -f /etc/qdc507-mode/current || {{ echo MODE_FILE_NOT_MOUNTED >&2; exit 42; }}\n    MODE=$(cat /etc/qdc507-mode/current)\nfi\ncase "$MODE" in\n    8k) SRC=/opt/qdc507/modes/8k/chan_quectel.so; HASH={sha8} ;;\n    16k) SRC=/opt/qdc507/modes/16k/chan_quectel.so; HASH={sha16} ;;\n    *) echo INVALID_AUDIO_MODE >&2; exit 43 ;;\nesac\nACTUAL=$(sha256sum "$SRC" | cut -d ' ' -f1)\ntest "$ACTUAL" = "$HASH" || {{ echo MODE_DRIVER_HASH_MISMATCH >&2; exit 44; }}\nTARGET={module}\nTMP="${{TARGET}}.qdc-new.$$"\ntrap 'rm -f "$TMP"' EXIT HUP INT TERM\ncp "$SRC" "$TMP"\nchmod 644 "$TMP"\nmv -f "$TMP" "$TARGET"\ntrap - EXIT HUP INT TERM\nCURRENT=$(sha256sum "$TARGET" | cut -d ' ' -f1)\ntest "$CURRENT" = "$HASH" || {{ echo MODE_DRIVER_INSTALL_MISMATCH >&2; exit 45; }}\nprintf 'QDC507_AUDIO_MODE=%s\\nQDC507_DRIVER_SHA256=%s\\n' "$MODE" "$CURRENT"\nif [ "$SELF_TEST" = 1 ]; then\n    echo MODE_SELECTOR_SELF_TEST_PASSED=1\n    exit 0\nfi\nif [ "$#" -eq 0 ]; then echo ASTERISK_BOOT_ARGUMENTS_MISSING >&2; exit 46; fi\nexec "$@"\n'''.format(sha8=info['source_8k_module_sha256'], sha16=info['source_16k_module_sha256'], module=MODULE)


def expected_files(info):
    return {'Dockerfile': make_dockerfile(info),
            'qdc507-mode-boot.sh': make_boot(info),
            'README.txt': '''QDC507 one-container dual-mode driver image V2 (BUILD ONLY).\n
This directory has no production secrets and does not alter Docker state until\n--build. The generated Docker image preserves the original V7 Asterisk boot\ncommand, and contains the 8k stable driver and V7 16k driver.\n
8k source is the SHA-pinned /opt/qdc507/modes/8k/chan_quectel.so slot
of the verified current dual-mode container, even while production runs 16k.
Only the original single-mode image uses its verified production module path.
Never switch production mode to prepare or build this image.
This verifies only the on-disk driver, not what the running Asterisk process
may have loaded earlier; a production call test is still mandatory.
The supplied qdc507-mode-boot.sh is NOT the image default ENTRYPOINT.\nFuture production mode manager must select 8k/16k before starting Asterisk,\nchange qdc507.conf / helper policy, and mount union IF1/IF2+UAC devices.\nThe same container can be restarted for subsequent mode changes.\n
NEVER use docker exec to hot-swap a module while Asterisk is running.\nThis build does not set up boot persistence or SMS recovery.\n'''}


def ensure_created_fresh():
    need(not BUILD_DIR.exists() and not BUILD_DIR.is_symlink(),
         'BUILD_DIRECTORY_ALREADY_EXISTS_USE_VERIFY')


def prepare(info):
    ensure_created_fresh()
    BUILD_DIR.mkdir(mode=0o700)
    try:
        files = expected_files(info)
        shutil.copytree(str(PAYLOAD), str(BUILD_DIR / 'qdc507-preaudio-runtime'))
        shutil.copyfile(str(PAYLOAD / 'entrypoint.sh'), str(BUILD_DIR / 'entrypoint.sh'))
        checks = {}
        for name, content in files.items():
            p = BUILD_DIR / name
            with p.open('x', encoding='utf-8') as fd:
                fd.write(content)
                fd.flush(); os.fsync(fd.fileno())
            os.chmod(str(p), 0o700 if name.endswith('.sh') else 0o600)
            checks[name] = sha256(p)
        for p in sorted((BUILD_DIR / 'qdc507-preaudio-runtime').iterdir()):
            checks[str(p.relative_to(BUILD_DIR))] = sha256(p)
        checks['entrypoint.sh'] = sha256(BUILD_DIR / 'entrypoint.sh')
        data = dict(info)
        data['format'] = 'qdc507-dualmode-image-build-v2-live8k'
        data['input_files_sha256'] = checks
        # Snapshot the exact 8k driver running production has on disk.
        # docker cp reads only, with no exec into Asterisk or live file changes.
        module_stage = BUILD_DIR / LIVE_MODULE_NAME
        temp_module = BUILD_DIR / (LIVE_MODULE_NAME + '.copying')
        cmd(['docker', 'cp', info['live_8k_container_id'] + ':' + info['source_8k_path'], str(temp_module)], timeout=35)
        need(temp_module.is_file() and not temp_module.is_symlink(),
             'LIVE_8K_DRIVER_COPY_NOT_REGULAR_FILE')
        need(sha256(temp_module) == info['source_8k_module_sha256'],
             'LIVE_8K_DRIVER_COPY_HASH_CHANGED')
        live_now = inspect(['docker', 'inspect', PROD])
        need(live_now.get('Id') == info['live_8k_container_id'] and
             live_now.get('Image') == info['source_8k_container_image_id'] and
             bool((live_now.get('State') or {}).get('Running')),
             'LIVE_8K_CONTAINER_CHANGED_DURING_SNAPSHOT')
        snap_hash = cmd(['docker','exec',info['live_8k_container_id'],'sha256sum',info['source_8k_path']],timeout=18).strip().split()[0]
        need(snap_hash == info['source_8k_module_sha256'],
             'LIVE_8K_MODULE_CHANGED_DURING_SNAPSHOT')
        os.replace(str(temp_module), str(module_stage))
        os.chmod(str(module_stage), 0o600)
        data['live_8k_binary_sha256'] = sha256(module_stage)
        p = BUILD_DIR / MANIFEST
        with p.open('x', encoding='utf-8') as fd:
            json.dump(data, fd, sort_keys=True, indent=2)
            fd.write('\n')
        os.chmod(str(p), 0o600)
    except BaseException:
        shutil.rmtree(str(BUILD_DIR))
        raise
    emit('DUALMODE_LIVE_8K_BINARY_SNAPSHOT_VERIFIED', 1)
    emit('DUALMODE_BUILD_CONTEXT_PREPARED', 1)
    emit('DUALMODE_BUILD_CONTEXT', str(BUILD_DIR))
    emit('IMAGE_NOT_BUILT_NOT_DEPLOYED', 1)


def staged_verify(info):
    need(BUILD_DIR.is_dir() and not BUILD_DIR.is_symlink(),
         'BUILD_CONTEXT_NOT_PREPARED')
    manifest = BUILD_DIR / MANIFEST
    need(manifest.is_file() and not manifest.is_symlink(), 'BUILD_MANIFEST_MISSING')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    for key, val in info.items():
        need(data.get(key) == val, 'BUILD_CONTEXT_INPUT_CHANGED_'+key)
    module_snapshot = BUILD_DIR / LIVE_MODULE_NAME
    need(module_snapshot.is_file() and not module_snapshot.is_symlink(),
         'LIVE_8K_BINARY_SNAPSHOT_MISSING')
    need(sha256(module_snapshot) == info['source_8k_module_sha256'] and
         data.get('live_8k_binary_sha256') == info['source_8k_module_sha256'],
         'LIVE_8K_BINARY_SNAPSHOT_HASH_CHANGED')
    for name, digest in data['input_files_sha256'].items():
        p = BUILD_DIR / name
        need(p.is_file() and not p.is_symlink() and sha256(p) == digest,
             'BUILD_INPUT_CHANGED_' + name)
    files = expected_files(info)
    for name, content in files.items():
        p = BUILD_DIR / name
        need(p.is_file() and not p.is_symlink(), 'BUILD_FILE_MISSING_'+name)
        need(p.read_text(encoding='utf-8') == content and
             data['input_files_sha256'].get(name) == sha256(p),
             'BUILD_FILE_CONTENT_CHANGED_'+name)
        need((p.stat().st_mode & stat.S_IWOTH) == 0,
             'BUILD_FILE_WORLD_WRITABLE_'+name)
    emit('LIVE_8K_BINARY_SNAPSHOT_VERIFIED', 1)
    emit('BUILD_CONTEXT_VERIFIED', 1)


def build(info):
    staged_verify(info)
    # Refuse clobbering an already tagged image, even if it looks the same.
    p = subprocess.run(['docker', 'image', 'inspect', IMGDUAL],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=12)
    need(p.returncode != 0, 'TARGET_DUALMODE_IMAGE_TAG_ALREADY_EXISTS_USE_VERIFY')
    emit('BUILDING_DUALMODE_IMAGE', IMGDUAL)
    # Docker uses two already present local images: no pull or network.
    subprocess.run(['docker', 'build', '--pull=false', '--network=none',
                    '-t', IMGDUAL, str(BUILD_DIR)], check=True)
    emit('DUALMODE_IMAGE_BUILD_COMPLETE', 1)
    emit('NO_PRODUCTION_CUTOVER', 1)


def verify_image(info):
    staged_verify(info)
    d = inspect(['docker', 'image', 'inspect', IMGDUAL])
    need(d.get('Architecture') == 'amd64', 'DUALMODE_IMAGE_WRONG_ARCH')
    c = d.get('Config') or {}
    need(c.get('Entrypoint') == info['v7_image_original_entrypoint'] and
         c.get('Cmd') == info['v7_image_original_cmd'],
         'DUALMODE_IMAGE_ORIGINAL_BOOT_CHANGED')
    for mode, expected in [('8k', info['source_8k_module_sha256']),
                           ('16k', info['source_16k_module_sha256'])]:
        output = cmd(['docker', 'run', '--rm', '--pull=never', '--network', 'none',
                      '-e', 'QDC507_AUDIO_MODE='+mode,
                      '--entrypoint', '/usr/local/sbin/qdc507-mode-boot.sh',
                      IMGDUAL, '--self-test'], timeout=60)
        need('MODE_SELECTOR_SELF_TEST_PASSED=1' in output and
             'QDC507_AUDIO_MODE='+mode in output and
             'QDC507_DRIVER_SHA256='+expected in output,
             'MODE_BOOT_SELF_TEST_FAILED_'+mode)
        emit('MODE_BOOT_'+mode.upper()+'_PASSED', 1)
    emit('IMAGE_ORIGINAL_ENTRYPOINT_CMD_PRESERVED', 1)
    emit('DUALMODE_IMAGE_READY_FOR_CUTOVER_DESIGN', 1)
    emit('NO_PRODUCTION_CUTOVER', 1)
    emit('NO_SMS_OR_QDC507_DEVICE_CHANGE', 1)
    emit('LIVE_8K_DRIVER_SOURCE_PINNED', info['source_8k_module_sha256'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    for act in ('check', 'prepare', 'build', 'verify'):
        g.add_argument('--'+act, action='store_true')
    global IMGDUAL, BUILD_DIR
    p.add_argument('--build-tag', default=IMGDUAL,
                   help='Optional isolated candidate tag; never rewrites cutover C.DUAL')
    p.add_argument('--build-dir', type=Path, default=BUILD_DIR,
                   help='Fresh private context; never overwrite the frozen V2 build')
    args = p.parse_args()
    need(bool(re.fullmatch(r'[a-z0-9][a-z0-9._/:\-]+', args.build_tag)), 'INVALID_BUILD_TAG')
    IMGDUAL = args.build_tag
    BUILD_DIR = args.build_dir
    info = facts()
    if args.check:
        if BUILD_DIR.exists(): staged_verify(info)
        emit('READ_ONLY_IMAGE_BUILD_CHECK_PASSED', 1)
    elif args.prepare:
        prepare(info)
    elif args.build:
        build(info)
    else:
        verify_image(info)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit('DUALMODE_IMAGE_STOPPED', type(exc).__name__ + ':' + str(exc)[:250])
        sys.exit(2)
