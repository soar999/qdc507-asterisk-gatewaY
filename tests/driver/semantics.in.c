// SPDX-License-Identifier: GPL-2.0-or-later
// QDC507 synthetic offline regression fixture, 2026-10-18.
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct cpvt {int flags,state,call_idx;struct cpvt*entry;};
struct pvt {int initialized,terminate_monitor,empty_str; char manufacturer[16];unsigned qdc507_clcc_snapshot_invalid,qdc507_clcc_seen;struct cpvt*chans;};
struct at_queue_cmd {int length,flags;};
#define CALL_FLAG_QDC507_HANGUP_PENDING 1
#define CALL_FLAG_ALIVE 2
#define CALL_STATE_INIT 0
#define CALL_STATE_RELEASED 6
#define AST_CAUSE_NORMAL_CLEARING 16
#define ATQ_CMD_FLAG_IGNORE 1
#define RES_TIMEOUT 99
#define CPVT_TEST_FLAG(p,f) ((p)->flags&(f))
#define CPVT_RESET_FLAG(p,f) ((p)->flags&=~(f))
#define AST_LIST_TRAVERSE(head,p,field) for((p)=*(head);(p);(p)=(p)->field)
#define PVT_ID(p) "offline"
#define LOG_ERROR 1
#define LOG_WARNING 2
#define ast_log(...) ((void)0)
#define ast_verb(level,fmt,id,idx) ((void)(idx))
static struct at_queue_cmd command;
static int present,response_error,responses,released;
static const struct at_queue_cmd*at_queue_head_cmd(struct pvt*p){(void)p;return present?&command:NULL;}
static int at_response(struct pvt*p,int*s,int r){(void)p;(void)s;assert(r==RES_TIMEOUT);++responses;return response_error;}
static void cpvt_change_state(struct cpvt*p,int state,int cause){assert(cause==16);p->state=state;++released;}
/* PRODUCTION_FUNCTIONS */
int main(void){
    struct pvt p={.initialized=1};strcpy(p.manufacturer,"Baiwang");
    present=0;cmd_timeout(&p);assert(!responses&&!p.terminate_monitor);
    present=1;command.length=1;cmd_timeout(&p);assert(!responses&&!p.terminate_monitor);
    command.length=0;command.flags=ATQ_CMD_FLAG_IGNORE;cmd_timeout(&p);assert(responses==1&&!p.terminate_monitor);
    command.flags=0;cmd_timeout(&p);assert(responses==2&&p.terminate_monitor);
    p.terminate_monitor=0;response_error=1;command.flags=ATQ_CMD_FLAG_IGNORE;cmd_timeout(&p);assert(responses==3&&p.terminate_monitor);
    puts("PASS production cmd_timeout: empty/unsent/IGNORE/real-timeout/response-error");
    struct cpvt c={CALL_FLAG_QDC507_HANGUP_PENDING,2,1,NULL};p.chans=&c;
    p.qdc507_clcc_snapshot_invalid=1;p.qdc507_clcc_seen=2;qdc507_clcc_finalize(&p);
    assert(!released&&(c.flags&1)&&!p.qdc507_clcc_snapshot_invalid&&!p.qdc507_clcc_seen);
    c.flags|=CALL_FLAG_ALIVE;qdc507_clcc_finalize(&p);assert(!released&&c.state==2);
    c.flags=CALL_FLAG_QDC507_HANGUP_PENDING;c.state=CALL_STATE_INIT;qdc507_clcc_finalize(&p);assert(!released);
    c.state=2;c.flags=0;qdc507_clcc_finalize(&p);assert(!released);
    c.flags=CALL_FLAG_QDC507_HANGUP_PENDING;qdc507_clcc_finalize(&p);assert(released==1&&c.state==CALL_STATE_RELEASED&&!c.flags);
    qdc507_clcc_finalize(&p);assert(released==1);
    strcpy(p.manufacturer,"Other");c.state=2;c.flags=1;qdc507_clcc_finalize(&p);assert(released==1&&c.flags==1);
    strcpy(p.manufacturer,"Baiwang");p.initialized=0;qdc507_clcc_finalize(&p);assert(released==1);
    puts("PASS production hangup finalizer: invalid/alive/init/not-pending/absent/repeat/non-QDC/uninitialized");
    puts("ALL 13 SEMANTIC CHECKS PASSED (mock list, state transition and response dependencies)");
}
