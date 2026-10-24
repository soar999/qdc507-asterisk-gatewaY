from pathlib import Path
import subprocess,hashlib,json
D=Path(__file__).resolve().parent
OLD=D/'old-tests'
BASE=D.parent/'baseline'
NEW=D.parent/'src'
def function(text,sig):
 a=text.index(sig);b=text.index('{',a);n=1;e=b+1
 while n:n+=(text[e]=='{')-(text[e]=='}');e+=1
 return text[a:e]
records={}
def build(name,root,extended=False):
 m=(root/'src/monitor_thread.c').read_text();a=m.index('        if (ast_mutex_trylock(&pvt->lock)) {  // pvt lock not acquired');b=m.index('        /* FIXME: access to device not locked */',a);flow=m[a:b]
 funcs=function((root/'src/at_queue.c').read_text(),'int at_queue_timeout(')+'\n'+function((root/'src/at_read.c').read_text(),'int at_wait(')+'\n'+function(m,'static void qdc507_clcc_poll(')+'\n'
 h=(OLD/'harness.in.c').read_text()
 if root==NEW:
  defs='''
struct cpvt {int state;unsigned flags;struct cpvt *entry;};
#define CALL_STATE_ACTIVE 0
#define CALL_STATE_ONHOLD 1
#define CALL_STATE_DIALING 2
#define CALL_STATE_ALERTING 3
#define CALL_STATE_INCOMING 4
#define CALL_STATE_WAITING 5
#define CALL_STATE_RELEASED 6
#define CALL_STATE_INIT 7
#define CALL_FLAG_DIRECTION 512
#define CALL_FLAG_QDC507_HANGUP_PENDING 4096
#define CPVT_DIR_OUTGOING(p) (!((p)->flags & CALL_FLAG_DIRECTION))
#define CPVT_TEST_FLAG(p,f) ((p)->flags & (f))
#define AST_LIST_TRAVERSE(h,p,e) for(assert(lock_held),(p)=*(h);(p);(p)=(p)->e)
'''
  h=h.replace('struct pvt {',defs+'struct pvt {struct cpvt *chans;const char *model;int is_simcom;')
  h=h.replace('p.initialized=1;', 'p.initialized=1;p.model="QDC507";')
  funcs=function(m,'static int qdc507_poll_wait_ms(')+'\n'+funcs
 if extended:
  h=h.replace('static int now_ms,','static struct cpvt call;\nstatic int state_at=-1,new_state,remove_at=-1,occupy_at=-1,defer_poll,mark_enqueued;\nstatic int now_ms,')
  h=h.replace('++clcc; return 0;','++clcc; if(mark_enqueued)present=1; return 0;')
  h=h.replace('int ready=end==readable_at;', '''if(state_at>=now_ms && state_at<=end) { call.state=new_state;state_at=-1; }
    if(occupy_at>=now_ms && occupy_at<=end) {present=1;occupy_at=-1;}
    int ready=end==readable_at;''')
  h=h.replace('if(!lock_fail)qdc507_clcc_poll(p);','if(!lock_fail&&!defer_poll)qdc507_clcc_poll(p);')
  h=h.replace('now_ms=0;present=', 'state_at=remove_at=occupy_at=-1;defer_poll=mark_enqueued=0;call=(struct cpvt){CALL_STATE_DIALING,0,NULL};\n    now_ms=0;present=')
  h=h.replace('struct pvt p={0};p.initialized=1;', 'struct pvt p={0};p.chans=&call;p.initialized=1;')
  h=h[:h.index('int main(void) {')]+(D/'fast-main.c').read_text()
 h=h.replace('/* PRODUCTION_FUNCTIONS */',funcs).replace('/* PRODUCTION_WAIT */',flow)
 p=D/(name+'.c');p.write_text(h);exe=D/('test-'+name)
 cmd=['cc','-std=c11','-Wall','-Wextra','-Werror','-O2','-DPATCHED=1',str(p),'-o',str(exe)]
 r=subprocess.run(cmd,text=True,capture_output=True);(D/(name+'-compile.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
 r=subprocess.run(([str(exe)] if not Path('/lib/ld-musl-aarch64.so.1').exists() else ['/lib/ld-musl-aarch64.so.1',str(exe)]),text=True,capture_output=True);(D/(name+'.log')).write_text(r.stdout+r.stderr);print(name,r.returncode,r.stdout,r.stderr,flush=True);assert r.returncode==0
 records[name]={'flow_sha256':hashlib.sha256(flow.encode()).hexdigest(),'production_functions_sha256':hashlib.sha256(funcs.encode()).hexdigest(),'rc':r.returncode}
build('baseline',BASE)
build('compat',NEW)
build('fast',NEW,True)
(D/'extraction-audit.json').write_text(json.dumps(records,indent=2)+'\n')
