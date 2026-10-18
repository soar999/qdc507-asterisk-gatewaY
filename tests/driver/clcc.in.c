// SPDX-License-Identifier: GPL-2.0-or-later
// QDC507 synthetic offline regression fixture, 2026-10-18.
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <poll.h>
#include <sys/time.h>
/* Asterisk/serial/serializer are controlled seams. Time is virtual except pipe test. */
struct at_queue_cmd { int length; struct timeval timeout; };
typedef struct at_queue_cmd at_queue_cmd_t;
struct pvt { int lock, initialized, terminate_monitor, desired_state, sys_chan, at_queue; char manufacturer[16]; };
struct ast_taskprocessor { int dummy; };
static struct at_queue_cmd command;
static int now_ms, present, complete_at, readable_at, replace_at, waits, max_wait;
static int timeout_tasks, poll_tasks, ping_tasks, clcc, reads, lock_fail, push_fail;
static int lock_held, restart_tasks, overloaded, last_read, real_io, last_timeout;
#define RESPONSE_READ_TIMEOUT 10000
#define UNHANDLED_COMMAND_TIMEOUT 500
#define LOG_WARNING 1
#define LOG_NOTICE 2
#define PVT_ID(p) "test"
#define AST_LIST_EMPTY(p) (!present)
#define ast_debug(...) ((void)0)
#define ast_log(...) ((void)0)
static const at_queue_cmd_t *at_queue_head_cmd(const struct pvt *p) { (void)p; return present?&command:NULL; }
static struct timeval ast_tvnow(void) { return (struct timeval){now_ms/1000,(now_ms%1000)*1000}; }
static int ast_tvdiff_ms(struct timeval a, struct timeval b) { return (int)((a.tv_sec-b.tv_sec)*1000+(a.tv_usec-b.tv_usec)/1000); }
static int at_enqueue_clcc(int *c) { (void)c; ++clcc; return 0; }
static int ast_waitfor_n_fd(int *fd,int n,int *ms,int *exception) {
    (void)n; *exception=0; ++waits; assert(*ms>0); if(*ms>max_wait)max_wait=*ms;
    if(real_io) { struct pollfd p={*fd,POLLIN,0}; return poll(&p,1,*ms)>0?*fd:-1; }
    int end=now_ms+*ms;
    if(readable_at>=now_ms && readable_at<=end)end=readable_at;
    /* A serializer completion while the monitor is asleep does not wake its fd. */
    if(complete_at>=now_ms && complete_at<=end) {present=0; complete_at=-1;}
    if(replace_at>=now_ms && replace_at<=end) {
        present=1; command.timeout=(struct timeval){12,0}; replace_at=-1;
    }
    int ready=end==readable_at; *ms-=end-now_ms; now_ms=end;
    if(ready) {readable_at=-1; return *fd;} return -1;
}
/* PRODUCTION_FUNCTIONS */
static int ast_mutex_trylock(int *l) { (void)l; if(lock_fail)return 1; assert(!lock_held);lock_held=1;return 0; }
static void ast_mutex_unlock(int *l) {(void)l;assert(lock_held);lock_held=0;}
static int check_dev_status(struct pvt *p,struct ast_taskprocessor*t) {(void)p;(void)t;return 0;}
static int check_taskprocessor(struct ast_taskprocessor*t,const char*d) {(void)t;(void)d;return overloaded;}
static int cmd_timeout_taskproc(void *p) {(void)p;++timeout_tasks;last_timeout=now_ms;return 0;}
static int restart_monitor_taskproc(void *p) {(void)p;++restart_tasks;return 0;}
static int qdc507_clcc_poll_taskproc(void *p) {++poll_tasks;if(!lock_fail)qdc507_clcc_poll(p);return 0;}
static int at_enqueue_ping_taskproc(void *p) {(void)p;++ping_tasks;return 0;}
static int ast_taskprocessor_push(struct ast_taskprocessor*t,int(*fn)(void*),void*p) {(void)t;if(push_fail)return 1;return fn(p);}
static void run(struct pvt *pvt,int iterations,int cached) {
    struct ast_taskprocessor tp, *tps=&tp;
    const char *dev="test"; const int fd=7;
    int qdc507_poll_enabled=cached;
    for(int iteration=0;iteration<iterations;iteration++) {
/* PRODUCTION_WAIT */
        ++reads;last_read=now_ms;
    }
    return;
e_cleanup:
e_restart:
    if(lock_held)ast_mutex_unlock(&pvt->lock);
}
static struct pvt reset(int qdc,int deadline) {
    now_ms=0;present=deadline>=0;complete_at=readable_at=replace_at=-1;
    waits=max_wait=timeout_tasks=poll_tasks=ping_tasks=clcc=reads=lock_fail=push_fail=0;
    lock_held=restart_tasks=overloaded=last_read=real_io=last_timeout=0;
    command=(struct at_queue_cmd){0,{deadline/1000,(deadline%1000)*1000}};
    struct pvt p={0};p.initialized=1;strcpy(p.manufacturer,qdc?"Baiwang":"Other");return p;
}
#define OK(s) puts("PASS " s)
int main(void) {
    struct pvt p=reset(1,5000); complete_at=10;run(&p,2,1);
    assert(now_ms==(PATCHED?2000:6000));assert(clcc==1);assert(timeout_tasks==(PATCHED?0:1));
    printf("PASS stale-wait completion=10ms next-poll=%dms timeout-tasks=%d\n",now_ms,timeout_tasks);
    p=reset(1,5000);run(&p,PATCHED?5:1,1);assert(now_ms==5000&&timeout_tasks==1&&last_timeout==5000);
    assert(command.timeout.tv_sec==5);OK("real 5000ms deadline unchanged");
    const int short_ms[]={1,250,999,1000,1001};
    for(unsigned i=0;i<sizeof(short_ms)/sizeof(*short_ms);i++) {
        int d=short_ms[i];p=reset(1,d);run(&p,PATCHED&&d>1000?2:1,1);
        assert(now_ms==d&&last_timeout==d&&timeout_tasks==1);
    } OK("1/250/999/1000/1001ms timeouts");
    p=reset(1,0);run(&p,1,1);assert(timeout_tasks==1&&last_timeout==0&&now_ms==500);OK("already-expired deadline retains 500ms backoff");
    p=reset(1,5000);readable_at=75;run(&p,1,1);assert(reads==1&&last_read==75&&!timeout_tasks);OK("readable before slice falls through to read");
    p=reset(1,5000);readable_at=1000;run(&p,1,1);assert(reads==1&&!timeout_tasks);OK("readable at slice boundary is not discarded");
    p=reset(1,5000);run(&p,PATCHED?4:1,1);assert(!clcc&&present);if(PATCHED)assert(!timeout_tasks&&now_ms==4000);OK("other pending AT work is not bypassed by CLCC");
    p=reset(1,5000);complete_at=10;replace_at=20;run(&p,PATCHED?5:1,1);
    if(PATCHED)assert(now_ms==5000&&!timeout_tasks&&!clcc&&command.timeout.tv_sec==12);
    OK("replacement AT deadline re-read after sliced wait");
    p=reset(0,5000);complete_at=10;run(&p,2,0);assert(now_ms==15000&&timeout_tasks==1&&ping_tasks==1&&!clcc);OK("non-QDC old behavior unchanged");
    p=reset(1,-1);lock_fail=1;run(&p,3,1);assert(now_ms==3000&&poll_tasks==3&&!clcc&&!timeout_tasks);OK("QDC cached lock-failure retry; skipped task does not spin");
    p=reset(0,-1);lock_fail=1;run(&p,1,0);assert(now_ms==10000&&ping_tasks==1);OK("non-QDC lock-failure wait unchanged");
    p=reset(1,-1);lock_fail=1;readable_at=20;run(&p,1,1);assert(reads==1&&last_read==20);OK("lock failure still permits serial reading");
    p=reset(1,5000);push_fail=1;run(&p,6,1);assert(waits==6&&now_ms==(PATCHED?5500:7500)&&!timeout_tasks);OK("serializer push failure retains positive backoff");
    p=reset(1,-1);p.terminate_monitor=1;run(&p,1,1);assert(!waits&&!lock_held);OK("monitor stop path retained");
    p=reset(1,5000);command.length=8;run(&p,1,1);assert(now_ms==1000&&!clcc&&!timeout_tasks);OK("unsent command not timed out or overwritten");
    p=reset(1,-1);run(&p,1,1);assert(now_ms==1000&&clcc==1);OK("idle cadence remains 1000ms");
    p=reset(1,-1);p.initialized=0;run(&p,1,0);assert(now_ms==10000&&!clcc&&ping_tasks==1);OK("uninitialized Baiwang remains upstream behavior");
    /* Real OS pipe checks production at_wait never consumes serial-like bytes. */
    reset(1,-1);int fds[2];assert(pipe(fds)==0);real_io=1;
    const char payload[]="\r\n+CLCC: 1,0,0,0,0\r\nOK\r\n";
    assert(write(fds[1],payload,sizeof(payload))==(ssize_t)sizeof(payload));
    int ms=1000;assert(at_wait(fds[0],&ms)==fds[0]);char out[sizeof(payload)];
    assert(read(fds[0],out,sizeof(out))==(ssize_t)sizeof(out));assert(!memcmp(out,payload,sizeof(out)));
    ms=1;assert(!at_wait(fds[0],&ms));close(fds[0]);close(fds[1]);OK("real pipe readability, byte preservation and empty timeout");
    puts("ALL 18 CASE GROUPS PASSED (offline simulated Asterisk seams; not hardware)");
}
