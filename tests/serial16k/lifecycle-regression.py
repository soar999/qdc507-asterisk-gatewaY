from pathlib import Path
import hashlib,json,subprocess
D=Path(__file__).resolve().parent;S=D.parent/'src/src';B=D.parent/'baseline/src'
def fn(t,s):
 a=t.index(s);b=t.index('{',a);n=1;e=b+1
 while n:n+=(t[e]=='{')-(t[e]=='}');e+=1
 return t[a:e]
q=(S/'at_queue.c').read_text();h=(S/'at_queue.h').read_text();r=(S/'at_response.c').read_text()
cmd=h[h.index('typedef struct at_queue_cmd {'):h.index('/* initializers */')]
task=h[h.index('typedef struct at_queue_task {'):h.index('void at_queue_free_data(')]
parts=[cmd,task,fn(h,'static inline const at_queue_cmd_t* at_queue_task_cmd(')]
parts += [fn(q,s) for s in ['void at_queue_free_data(','static void at_queue_free(','static void at_queue_remove(','static void at_queue_remove_cmd(','void at_queue_handle_result(','const struct at_queue_task* at_queue_head_task(','const at_queue_cmd_t* at_queue_head_cmd(']]
e=fn(r,'static int at_response_error(')
# Exact production guard + AT/AT_Z/AT_E fatal and CLCC branches + actual result/free tails.
e=e[:e.index('        case CMD_AT_FINAL:')]+'    }\n\n'+e[e.index('    at_queue_handle_result(pvt, at_res);'):]
parts.append(e)
a=fn(r,'int at_response(')
a=a[:a.index('    switch (at_res) {')]+'    switch (at_res) {\n'+a[a.index('        case RES_CMS_ERROR:'):a.index('        case RES_QIND:')]+'        default: abort();\n    }\n}'
parts.append(a)
record={'exact_complete_functions':['at_queue_task_cmd','at_queue_free_data','at_queue_free','at_queue_remove','at_queue_remove_cmd','at_queue_handle_result','at_queue_head_task','at_queue_head_cmd','cmd_timeout'], 'partial_functions':{'at_response_error':'exact entry guards, CMD_AT/AT_Z/AT_E failure and CMD_AT_CLCC branch, exact normal/failure result tails; unrelated command cases omitted','at_response':'exact entry/head lookup/show_response and RES_CMS_ERROR/ERROR/TIMEOUT dispatch; unrelated responses omitted; test default abort'},'stubs':'Asterisk structs (pvt minimal, list entry/head), list primitives, logging/show_response, virtual ast_tvnow, ast_free allocator adapter. at_response is NOT a response-count mock. Task and command structs extracted exactly. Guard mode ast_free poisons and mprotects dynamically mmap allocated pages; ASAN mode allocates calloc and real free.'}
(D/'lifecycle-extraction.json').write_text(json.dumps(record,indent=2)+'\n')

import os
mode=os.environ.get('LIFECYCLE_MODE','poison')
assert mode in ('poison','guard','asan')
results={}
for name,root in [('old',B),('fixed',S)]:
 text=(D/'lifecycle.in.c').read_text().replace('/* PRODUCTION */','\n'.join(parts)+'\n'+fn((root/'monitor_thread.c').read_text(),'static void cmd_timeout('))
 source=D/('lifecycle-'+name+'.c');source.write_text(text);exe=D/('lifecycle-'+name)
 flags={'poison':['-DPOISON_ONLY'],'guard':[],'asan':['-DUSE_ASAN','-fsanitize=address,undefined','-fno-omit-frame-pointer']}[mode]
 cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-O1','-g']+flags+[str(source),'-o',str(exe)]
 p=subprocess.run(cmd,capture_output=True,text=True);(D/('lifecycle-'+name+'-compile.log')).write_text(p.stdout+p.stderr);assert not p.returncode,p.stderr
 for case in range(17):
  launch=[str(exe)] if not Path('/lib/ld-musl-aarch64.so.1').exists() else ['/lib/ld-musl-aarch64.so.1',str(exe)]
  p=subprocess.run(launch+[name,str(case)],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0:halt_on_error=1'))
  (D/('lifecycle-'+name+'-case%02d.log'%case)).write_text(p.stdout+p.stderr)
  results[name+str(case)]={'rc':p.returncode,'mode':mode}
  if name=='fixed': assert p.returncode==0,p.stdout+p.stderr
  if name=='old' and case in (4,8,9,10): assert p.returncode!=0,'deadline negative control must fail'
  if name=='old' and mode!='poison':
   if case in (2,3,15,16): assert p.returncode==0
   else: assert p.returncode!=0
   if mode=='asan' and case in (0,1,5,6,7,11,12,13,14):
    assert 'ERROR: AddressSanitizer: heap-use-after-free' in p.stderr,p.stderr
 print(name,results,flush=True)
(D/'lifecycle-results.json').write_text(json.dumps(results,indent=2)+'\n')
print('Fixed 17 cases passed; mode='+mode+'; poison is NOT mprotect or ASAN evidence')
