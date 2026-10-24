#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include <stdint.h>
typedef int at_cmd_t;
typedef int at_res_t;
enum { CMD_AT=1,CMD_AT_Z,CMD_AT_E,CMD_AT_CLCC };
enum { RES_OK=1,RES_SMS_PROMPT,RES_TIMEOUT,RES_ERROR,RES_CMS_ERROR };
struct ast_str {int length;};
struct at_queue_task;
struct pvt {struct at_queue_task *at_queue;int initialized,terminate_monitor;char manufacturer[16];struct ast_str empty_str;unsigned qdc507_clcc_snapshot_invalid,qdc507_clcc_seen;int at_tasks,at_cmds;};
#define AST_LIST_ENTRY(t) struct {struct t *next;}
#define AST_LIST_FIRST(h) (*(h))
#define AST_LIST_REMOVE_HEAD(h,e) ({__typeof__(*(h)) old=*(h);if(old)*(h)=old->e.next;old;})
#define PVT_STATE(p,f) ((p)->f)
#define PVT_ID(p) "lifecycle"
#define ast_debug(...) ((void)0)
#define ast_log(...) ((void)0)
#define at_err_response_err(...) ((void)0)
#define at_err_response_wrn(...) ((void)0)
#define show_response(...) ((void)0)
#define ast_str_strlen(s) ((s)->length)
static long now_us;
static unsigned freed;
static inline struct timeval ast_tvnow(void){return (struct timeval){now_us/1000000,now_us%1000000};}
static void *alloc_task(size_t n){
#ifdef USE_ASAN
 return calloc(1,n);
#else
 size_t page=(size_t)sysconf(_SC_PAGESIZE);assert(n<=page);
 void *p=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(p!=MAP_FAILED);return p;
#endif
}
static void release(void *p){
 ++freed;
#ifdef USE_ASAN
 free(p);
#else
 size_t page=(size_t)sysconf(_SC_PAGESIZE);memset(p,0xa5,page);
#ifndef POISON_ONLY
 assert(!mprotect(p,page,PROT_NONE));
#endif
#endif
}
#define ast_free(p) release(p)
/* PRODUCTION */
static at_queue_task_t *add(struct pvt*p,unsigned flags,int cmd,long deadline,unsigned once){
 at_queue_task_t*t=alloc_task(sizeof(*t)+sizeof(t->cmds[0]));t->cmdsno=1;t->at_once=once;
 t->cmds[0]=(at_queue_cmd_t){.cmd=cmd,.res=RES_OK,.flags=flags,.timeout={deadline/1000000,deadline%1000000}};
 if(!p->at_queue)p->at_queue=t;else p->at_queue->entry.next=t;
 p->at_tasks++;p->at_cmds++;return t;
}
static void test(int id){
 struct pvt p={.initialized=1,.qdc507_clcc_seen=7,.qdc507_clcc_snapshot_invalid=1};strcpy(p.manufacturer,"Baiwang");freed=0;now_us=1000000;
 unsigned flags=(id==1||id==3||id==6||id==7||id==12)?ATQ_CMD_FLAG_IGNORE:0;
 int critical=(id==2||id==3);
 long delta=id==4?12000000:id==8?1:id==9?999:id==10?1000:id==11?-1:0;
 if(id==5||id==6) {strcpy(p.manufacturer,"Other");delta=12000000;}
 if(id==13){p.initialized=0;delta=12000000;}
 at_queue_task_t*t=add(&p,flags,critical?CMD_AT:CMD_AT_CLCC,now_us+delta,id==12);
 at_queue_task_t*next=NULL;
 if(id==7||id==14)next=add(&p,flags?0:ATQ_CMD_FLAG_IGNORE,CMD_AT_CLCC,9000000,0);
 if(id==15)t->cmds[0].length=8;
 if(id==16){p.at_queue=NULL;/* allocated fixture intentionally not a queue head */}
 cmd_timeout(&p);
 if(id==4||id==8||id==9||id==10||id==15||id==16){assert(freed==0&&!p.terminate_monitor);if(id!=16)assert(p.at_queue==t);return;}
 assert(freed==1&&p.at_queue==next);
 assert(p.terminate_monitor==(critical||!(flags&ATQ_CMD_FLAG_IGNORE)));
 if(!critical)assert(!p.qdc507_clcc_seen&&!p.qdc507_clcc_snapshot_invalid);
 assert(p.at_tasks==(next?1:0)&&p.at_cmds==(next?1:0));
}
int main(int argc,char**argv){
 assert(argc==2||argc==3);setbuf(stdout,NULL);if(argc==3){test(atoi(argv[2]));puts("CASE PASS");return 0;}int old=!strcmp(argv[1],"old");
 const char*names[]={"CLCC actual free","IGNORE actual free","fatal handling failure","fatal failure overrides IGNORE","replacement head not due","nonQDC original expiry policy","nonQDC IGNORE","IGNORE snapshot not next flags","future +1us","future +999us","future +1000us","truly expired -1us","at_once actual free","uninitialized original policy","normal snapshot not next IGNORE","unsent command","empty queue"};
 for(int i=0;i<17;i++){
  pid_t pid=fork();assert(pid>=0);if(!pid){test(i);_exit(0);}int st;assert(waitpid(pid,&st,0)==pid);
  int expected_fail=old&&i!=2&&i!=3&&i!=15&&i!=16;
  fprintf(stderr,"case=%d status=%d old=%d\n",i,st,old);
  if(expected_fail){assert(!WIFEXITED(st)||WEXITSTATUS(st)!=0);printf("EXPECTED OLD FAILURE %s status=%d\n",names[i],st);}
  else {assert(WIFEXITED(st)&&WEXITSTATUS(st)==0);printf("PASS %s\n",names[i]);}
 }
 puts(old?"PASS original defect observable; not a production pass":"PASS all 17 real task-release/deadline cases");
}
