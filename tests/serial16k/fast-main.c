int main(void) {
    struct pvt p;
    for(int state=0;state<=7;state++) {
        p=reset(1,5000);call.state=state;complete_at=10;run(&p,2,1);
        int fast=state==2||state==3;
        assert(now_ms==(fast?1000:2000)&&clcc==1&&!timeout_tasks);
        printf("PASS state=%d response@10 next-query=%d ms\n",state,now_ms);
    }
    p=reset(1,5000);strcpy(p.manufacturer,"Baiwang-extra");complete_at=10;run(&p,2,1);assert(now_ms==2000);OK("manufacturer suffix keeps legacy 1000ms, not new fast phase");
    const char *models[]={"UNKNOWN","QDC507-other",""};
    for(unsigned i=0;i<3;++i){p=reset(1,5000);p.model=models[i];complete_at=10;run(&p,2,1);assert(now_ms==2000);}
    p=reset(1,5000);p.is_simcom=1;complete_at=10;run(&p,2,1);assert(now_ms==2000);
    OK("unknown/suffixed/empty model and SIMCom never enter new 500ms phase");
    p=reset(1,-1);p.chans=NULL;run(&p,1,1);assert(now_ms==1000&&clcc==1);OK("idle unchanged");
    p=reset(1,5000);call.flags=CALL_FLAG_DIRECTION;complete_at=10;run(&p,2,1);assert(now_ms==2000);OK("incoming never fast even with anomalous DIALING state");
    p=reset(1,5000);call.flags=CALL_FLAG_QDC507_HANGUP_PENDING;complete_at=10;run(&p,2,1);assert(now_ms==2000);OK("pending hangup excludes fast phase");
    p=reset(1,5000);run(&p,10,1);assert(now_ms==5000&&timeout_tasks==1&&last_timeout==5000&&command.timeout.tv_sec==5);OK("10 slices preserve real absolute 5000ms deadline");
    const int d[]={1,249,250,251,499,500,999,1000,1001};
    for(unsigned i=0;i<sizeof(d)/sizeof(*d);i++) {
        p=reset(1,d[i]);run(&p,(d[i]+499)/500,1);assert(now_ms==d[i]&&timeout_tasks==1&&last_timeout==d[i]);
    }OK("short timers 1/249/250/251/499/500/999/1000/1001ms");
    p=reset(1,0);run(&p,1,1);assert(now_ms==500&&timeout_tasks==1&&last_timeout==0);OK("expired timer retains 500ms backoff");
    p=reset(1,5000);complete_at=10;replace_at=20;run(&p,10,1);assert(now_ms==5000&&!clcc&&!timeout_tasks&&command.timeout.tv_sec==12);OK("replacement AT is neither bypassed nor timed out by old slice");
    p=reset(1,5000);run(&p,9,1);assert(now_ms==4500&&!clcc&&!timeout_tasks);OK("answer/hangup/SMS outstanding queue prevents polling");
    p=reset(1,-1);occupy_at=100;run(&p,1,1);assert(now_ms==500&&poll_tasks==1&&!clcc);OK("new AT arriving inside idle wait wins over CLCC");
    p=reset(1,-1);defer_poll=1;run(&p,1,1);assert(poll_tasks==1&&!clcc);present=1;qdc507_clcc_poll(&p);assert(!clcc);OK("serializer delayed poll rechecks queue at execution");
    p=reset(1,-1);mark_enqueued=1;qdc507_clcc_poll(&p);qdc507_clcc_poll(&p);assert(clcc==1&&present);OK("multiple pending poll tasks cannot stack CLCC commands");
    p=reset(1,-1);lock_fail=1;run(&p,3,1);assert(now_ms==3000&&poll_tasks==3&&!clcc&&!timeout_tasks);OK("lock failure conservatively uses 1000ms, never spins or reads cpvt");
    p=reset(1,-1);lock_fail=1;readable_at=20;run(&p,1,1);assert(reads==1&&last_read==20);OK("lock failure still reads serial");
    for(int when=1;when<=500;when+=499) {
        p=reset(1,5000);readable_at=when;run(&p,1,1);assert(reads==1&&last_read==when&&!timeout_tasks);
    }OK("serial ready before/at slice preserved");
    p=reset(1,5000);complete_at=10;state_at=20;new_state=CALL_STATE_ACTIVE;run(&p,2,1);assert(now_ms==1500&&clcc==1);OK("DIALING to ACTIVE during wait: next locked snapshot restores 1000ms");
    p=reset(1,5000);call.state=CALL_STATE_ACTIVE;complete_at=10;state_at=20;new_state=CALL_STATE_DIALING;run(&p,2,1);assert(now_ms==1500&&clcc==1);OK("ACTIVE to DIALING cannot interrupt existing 1000ms wait");
    p=reset(1,5000);complete_at=10;state_at=20;new_state=CALL_STATE_RELEASED;run(&p,2,1);assert(now_ms==1500);OK("release mid-wait exits fast phase");
    p=reset(1,5000);run(&p,1,1);p.chans=NULL;run(&p,1,1);assert(now_ms==1500);OK("removed channel never retained across wait");
    p=reset(1,-1);struct cpvt second={CALL_STATE_ALERTING,0,NULL};call.state=CALL_STATE_ACTIVE;call.entry=&second;run(&p,1,1);assert(now_ms==500);OK("active plus outgoing setup selects fast phase");
    p=reset(1,5000);push_fail=1;run(&p,11,1);assert(now_ms==5500&&!timeout_tasks);OK("push failures retain timeout backoff");
    p=reset(1,-1);p.terminate_monitor=1;run(&p,1,1);assert(!waits&&!lock_held);OK("stop unchanged");
    p=reset(1,5000);command.length=8;run(&p,1,1);assert(now_ms==500&&!clcc&&!timeout_tasks);OK("unsent command remains queued without false timeout");
    p=reset(0,5000);complete_at=10;run(&p,2,0);assert(now_ms==15000&&timeout_tasks==1&&ping_tasks==1&&!clcc);OK("nonQDC unchanged");
    p=reset(1,-1);p.initialized=0;run(&p,1,0);assert(now_ms==10000&&ping_tasks==1&&!clcc);OK("uninitialized unchanged");
    p=reset(1,-1);run(&p,4,1);assert(now_ms==2000&&clcc==4);OK("fast idle upper envelope two monitor polls per second");
    reset(1,-1);int fds[2];assert(pipe(fds)==0);real_io=1;
    const char payload[]="\r\n+CLCC: 1,0,0,0,0\r\nOK\r\n";
    assert(write(fds[1],payload,sizeof(payload))==(ssize_t)sizeof(payload));
    int ms=500;assert(at_wait(fds[0],&ms)==fds[0]);char out[sizeof(payload)];
    assert(read(fds[0],out,sizeof(out))==(ssize_t)sizeof(out));assert(!memcmp(out,payload,sizeof(out)));
    ms=1;assert(!at_wait(fds[0],&ms));close(fds[0]);close(fds[1]);OK("real pipe byte preservation and timeout");
    puts("ALL FAST PHASE CASES PASSED; virtual scheduling seams, production wait/control functions");
}
