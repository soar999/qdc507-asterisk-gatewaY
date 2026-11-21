#!/usr/bin/env python3
"""Guarded, offline, one-time in-place compatibility patch of the original V22 hot recovery script.
Never touches hardware, baselines, schedules or Docker service.
"""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

TARGET = Path('/volume2/docker/asterisk/qdc507-16k-lab/qdc507-usb-recover-memoryfix-v22.sh')
KERNEL_OLD = "need(os.uname().release == '4.4.180+', 'NAS_KERNEL_VERSION_CHANGED')"
KERNEL_NEW = "need(os.uname().release == '4.4.302+', 'NAS_KERNEL_VERSION_CHANGED')"
COMMAND_ANCHOR = 'def command(argv, timeout=30, allow_fail=False, passthrough=False):\n'
IMPORT_ANCHOR = 'import hashlib\n'
MARKER = '# V22_DOCKER_API_COMPAT_DSM722_V1\n'

DOCKER_API_HELPER = '''# V22_DOCKER_API_COMPAT_DSM722_V1
def docker_api_cli(argv, timeout):
    class _UnixHTTP(http.client.HTTPConnection):
        def __init__(self):
            super().__init__('localhost', timeout=timeout)
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect('/var/run/docker.sock')

    def api(method, uri, payload=None):
        c = _UnixHTTP()
        try:
            body = None if payload is None else json.dumps(payload).encode('utf-8')
            headers = {'Content-Type':'application/json'} if body is not None else {}
            c.request(method, '/v1.43' + uri, body=body, headers=headers)
            r = c.getresponse()
            code = r.status
            raw = r.read(2097153)
            need(len(raw) <= 2097152, 'DOCKER_API_RESPONSE_TOO_LARGE')
            need(code in (200, 201), 'DOCKER_API_HTTP_' + str(code) + '_' + uri[:50])
            return raw
        finally:
            c.close()

    need(argv[0] == 'docker' and len(argv) >= 3, 'DOCKER_CLI_ARGS_INVALID')
    if argv[1] == 'inspect':
        need(len(argv) == 3 and argv[2] == CONTAINER, 'DOCKER_INSPECT_TARGET_INVALID')
        data = json.loads(api('GET', '/containers/' + CONTAINER + '/json'))
        return subprocess.CompletedProcess(argv, 0, json.dumps([data]), '')

    need(argv[1] == 'exec', 'DOCKER_API_UNSUPPORTED_COMMAND')
    i = 2
    user = ''
    if argv[i:i+2] == ['-u', '0']:
        user = '0'
        i += 2
    need(i < len(argv) and argv[i] == CONTAINER and len(argv) > i + 1,
         'DOCKER_EXEC_TARGET_OR_COMMAND_INVALID')
    spec = dict(AttachStdout=True, AttachStderr=True, Tty=False,
                Cmd=argv[i+1:])
    if user:
        spec['User'] = user
    created = json.loads(api('POST', '/containers/' + CONTAINER + '/exec', spec))
    eid = created.get('Id', '')
    need(re.fullmatch(r'[0-9a-f]{64}', eid), 'DOCKER_EXEC_ID_INVALID')
    raw = api('POST', '/exec/' + eid + '/start', dict(Detach=False, Tty=False))
    outs = {1: bytearray(), 2: bytearray()}
    cursor = 0
    while cursor < len(raw):
        need(len(raw) - cursor >= 8 and raw[cursor+1:cursor+4] == b'\\x00'*3,
             'DOCKER_EXEC_STREAM_HEADER_INVALID')
        channel = raw[cursor]
        length = int.from_bytes(raw[cursor+4:cursor+8], 'big')
        cursor += 8
        need(channel in outs and length <= len(raw) - cursor,
             'DOCKER_EXEC_STREAM_BODY_INVALID')
        outs[channel].extend(raw[cursor:cursor+length])
        cursor += length
    detail = json.loads(api('GET', '/exec/' + eid + '/json'))
    rc = detail.get('ExitCode')
    need(isinstance(rc, int) and not detail.get('Running'),
         'DOCKER_EXEC_EXIT_STATE_UNKNOWN')
    return subprocess.CompletedProcess(
        argv, rc, outs[1].decode('utf-8','replace'),
        outs[2].decode('utf-8','replace'))

'''

DISPATCH = '''    if argv and argv[0] == 'docker':
        p = docker_api_cli(argv, timeout)
        if passthrough:
            sys.stdout.write(p.stdout)
            sys.stderr.write(p.stderr)
        if not allow_fail:
            err = '' if passthrough else (p.stderr or p.stdout)[-220:]
            need(p.returncode == 0, 'COMMAND_FAILED_docker_' + err)
        return p
'''

def must(ok, why):
    if not ok:
        raise RuntimeError(why)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def parse_shell_python(raw):
    start = raw.index("<<'PY'\n") + len("<<'PY'\n")
    end = raw.rindex('\nPY\n')
    return raw[start:end]

def main():
    must(len(sys.argv) == 2 and sys.argv[1] in ('--verify', '--apply'), 'USE_--verify_OR_--apply')
    must(os.geteuid() == 0, 'ROOT_REQUIRED')
    must(os.uname().release == '4.4.302+', 'KERNEL_NOT_4.4.302')
    st = TARGET.lstat()
    must(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1 and
         not (stat.S_IMODE(st.st_mode) & 0o077) and st.st_size < 100000,
         'TARGET_FILE_OWNERSHIP_OR_MODE_UNTRUSTED')
    raw = TARGET.read_bytes()
    old = raw.decode('utf-8')
    must(MARKER not in old, 'PATCH_ALREADY_PRESENT')
    for anchor in (KERNEL_OLD, COMMAND_ANCHOR, IMPORT_ANCHOR):
        must(old.count(anchor) == 1, 'SOURCE_ANCHOR_COUNT_INVALID_' + anchor[:32])
    must("'host_boot_id') == Path('/proc/sys/kernel/random/boot_id')" in old,
         'BOOT_ID_GUARD_MISSING')
    must('def baseline():' in old and 'def recover():' in old, 'CORE_RECOVERY_MISSING')
    modified = old.replace(KERNEL_OLD, KERNEL_NEW)
    modified = modified.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + 'import http.client\n')
    modified = modified.replace(COMMAND_ANCHOR, DOCKER_API_HELPER + COMMAND_ANCHOR + DISPATCH)
    code = parse_shell_python(modified)
    compile(code, TARGET.name, 'exec')
    p = subprocess.run(['/bin/sh','-n'], input=modified, text=True, capture_output=True)
    must(p.returncode == 0, 'SHELL_SYNTAX_INVALID_' + p.stderr[:150])
    print('ORIGINAL_SHA256=' + sha(raw), flush=True)
    print('PATCHED_SHA256=' + sha(modified.encode()), flush=True)
    print('BOOT_ID_GUARD_RETAINED=1', flush=True)
    print('ORIGINAL_ENTRYPOINT_UNCHANGED=1', flush=True)
    if sys.argv[1] == '--verify':
        print('PATCH_VERIFY_READ_ONLY=1', flush=True)
        return
    backup = TARGET.with_name(TARGET.name + '.before-dsm722-' + time.strftime('%Y%m%d-%H%M%S'))
    backup_fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(backup_fd, 'wb') as f:
        f.write(raw)
        f.flush(); os.fsync(f.fileno())
    must(TARGET.read_bytes() == raw, 'TARGET_CHANGED_SINCE_BACKUP')
    tmp = TARGET.with_name(TARGET.name + '.patch-' + str(os.getpid()) + '.tmp')
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(modified.encode())
            f.flush(); os.fsync(f.fileno())
        os.replace(str(tmp), str(TARGET))
    finally:
        if tmp.exists():
            tmp.unlink()
    must(TARGET.read_bytes() == modified.encode(), 'POST_WRITE_VERIFY_FAILED')
    print('BACKUP_FILE=' + str(backup), flush=True)
    print('PATCH_IN_PLACE_OK=1', flush=True)
    print('NOTE_BASELINE_BOOT_GUARD_STILL_ACTIVE=1', flush=True)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('PATCH_STOP=' + type(e).__name__ + ':' + str(e)[:250], file=sys.stderr)
        sys.exit(2)
