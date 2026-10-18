#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline regressions against a patched checkout; no modem/config/log inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path, help='checkout after qdc507_patch.py')
parser.add_argument('--sanitizers', action='store_true', help='require ASAN+UBSAN support')
args = parser.parse_args()
source = args.source.resolve()
for name, digest in json.loads((HERE/'source-sha256.json').read_text()).items():
    assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest, name
print('PASS all 54 production src file SHA-256 checks', flush=True)

def function(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth, end = 1, brace+1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]

def read(name):
    return (source/'src'/name).read_text()

with tempfile.TemporaryDirectory(prefix='qdc507-tests-') as directory:
    out = Path(directory)
    (out/'asterisk.h').write_text('/* Test-only external configuration header. */\n')
    cc = os.environ.get('CC', 'cc')
    def run(name, text, extra=(), flags=(), arguments=(), fails=False):
        c = out/(name+'.c')
        c.write_text(text)
        exe = out/name
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-misleading-indentation',
                        '-g', '-I'+str(out), '-I'+str(source/'src'),
                        str(c), *map(str, extra), *flags, '-o', str(exe)], check=True)
        env = os.environ.copy()
        # Leak checks are outside scope; keep address and UB failures fatal.
        env['ASAN_OPTIONS'] = 'detect_leaks=0:halt_on_error=1'
        env['UBSAN_OPTIONS'] = 'halt_on_error=1'
        result = subprocess.run([str(exe), *arguments], env=env)
        if fails:
            assert result.returncode != 0, 'old bug must be reproduced'
            print('PASS old ringbuffer negative control failed as expected', flush=True)
        else:
            result.check_returncode()
    at = read('at_read.c')
    funcs = '\n'.join(function(at, sig) for sig in [
        'size_t at_get_iov_size(', 'size_t at_get_iov_size_n(',
        'size_t at_combine_iov(', 'static size_t get_2ndeol_pos(',
        'int at_read_result_iov('])
    stubs = '''#define ARRAY_LEN(a) (sizeof(a)/sizeof((a)[0]))
#define STRLEN(a) (sizeof(a)-1)
struct ast_str { char data[2048]; size_t len; };
static void ast_str_truncate(struct ast_str *s,size_t n){assert(n<sizeof s->data);s->len=n;s->data[n]=0;}
static char *ast_str_buffer(struct ast_str *s){return s->data;}
'''
    ring = (HERE/'ringbuffer.in.c').read_text().replace('/* PRODUCTION_AT_READ */', stubs+funcs)
    extra = [source/'src/ringbuffer.c', source/'src/memmem.c']
    fixed = read('ringbuffer.c')
    needle = 'mem + len - i, i'
    assert fixed.count(needle) == 1
    old = out/'old-ringbuffer.c'
    old.write_text(fixed.replace(needle, 'mem + i, i'))
    run('old-ring', ring, [old, extra[1]], ['-O2'], ['reproducer'], fails=True)
    for name, flags in [('fallback', []), ('libc', ['-DHAVE_MEMMEM'])]:
        run('ring-'+name, ring, extra, ['-O2', *flags])
        if args.sanitizers:
            run('ring-'+name+'-sanitized', ring, extra,
                ['-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', *flags])
    run('guard-pages', (HERE/'guard-pages.c').read_text(), extra, ['-O2'])
    monitor = read('monitor_thread.c')
    start = monitor.index('        if (ast_mutex_trylock(&pvt->lock)) {  // pvt lock not acquired')
    end = monitor.index('        /* FIXME: access to device not locked */', start)
    wait = monitor[start:end]
    funcs = '\n'.join([function(read('at_queue.c'), 'int at_queue_timeout('),
                       function(at, 'int at_wait('),
                       function(monitor, 'static void qdc507_clcc_poll(')])
    template = (HERE/'clcc.in.c').read_text().replace('/* PRODUCTION_FUNCTIONS */', funcs)
    run('clcc-fixed', template.replace('/* PRODUCTION_WAIT */', wait), flags=['-O2', '-DPATCHED=1'])
    a = wait.index('                } else {\n                    /*\n                     * A response handled')
    b = wait.index('            } else {\n                t = qdc507_poll', a)
    oldwait = wait[:a]+'''                } else if (!at_wait(fd, &t)) {
                    if (ast_taskprocessor_push(tps, cmd_timeout_taskproc, pvt)) {
                        ast_debug(5, "[%s] Unable to handle timeout\\n", dev);
                    }
                    continue;
                }
'''+wait[b:]
    run('clcc-old', template.replace('/* PRODUCTION_WAIT */', oldwait), flags=['-O2', '-DPATCHED=0'])
    semantics = function(monitor, 'static void cmd_timeout(')+'\n'+function(read('at_response.c'), 'static void qdc507_clcc_finalize(')
    run('hangup', (HERE/'semantics.in.c').read_text().replace('/* PRODUCTION_FUNCTIONS */', semantics), flags=['-O2'])
    enum = '''#include <assert.h>
#include <limits.h>
#include <stddef.h>
#include <string.h>
#define ast_strlen_zero(s) (!(s) || !*(s))
'''+function(read('mutils.h'), 'static inline const char* enum2str_def(')+'''
int main(void) {
 const char *names[]={"registered",NULL,""}; const char *fallback="unknown";
 assert(!strcmp(enum2str_def(0,names,3,fallback),"registered"));
 assert(enum2str_def(1,names,3,fallback)==fallback);
 assert(enum2str_def(2,names,3,fallback)==fallback);
 assert(enum2str_def(3,names,3,fallback)==fallback);
 assert(enum2str_def(UINT_MAX,names,3,fallback)==fallback);
 assert(enum2str_def(0,NULL,0,fallback)==fallback);
 return 0;
}
'''
    run('enum-bounds', enum, flags=['-O2'])
    print('PASS enum bounds: valid/null/empty/out-of-range/UINT_MAX/empty-table')
print('ALL OFFLINE DRIVER REGRESSIONS PASSED')
