#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifndef BRIDGE_SOURCE
#define BRIDGE_SOURCE "mavo_pcm_bridge_16k_clock8_guard16.c"
#endif
static uint64_t now=1000000000ULL, delay_ns;
static unsigned captures, frames, calls, closed, pauses, step, sleep_calls;
static size_t offset, sent;
static unsigned char expected[256];
static const char *mode;
static int is(const char *s) { return !strcmp(mode,s); }
static void stop(void);
static int fake_clock(clockid_t id,struct timespec *t) {
    (void)id;t->tv_sec=now/1000000000ULL;t->tv_nsec=now%1000000000ULL;return 0;
}
static int fake_usleep(useconds_t u) { now+=(uint64_t)u*1000;++pauses;return 0; }
static int fake_nanosleep(const struct timespec *t,struct timespec *r) {
    (void)r;now+=(uint64_t)t->tv_sec*1000000000ULL+t->tv_nsec;++sleep_calls;return 0;
}
static ssize_t fake_write(int fd,const void *p,size_t n) {
    if (fd==STDERR_FILENO) return write(fd,p,n);
    assert(++calls<1000);
    assert(n==256-offset);
    if (is("drop") && captures==1) { errno=EAGAIN;return -1; }
    if (is("error")) { errno=EIO;return -1; }
    if (is("partial") && offset==127 && step<2) {
        errno=(step++==0)?EINTR:EAGAIN;return -1;
    }
    if (is("stop_partial") && offset==127) { stop();errno=EAGAIN;return -1; }
    if ((is("partial")||is("stop_partial")) && offset==0) n=127;
    assert(memcmp(p,expected+offset,n)==0);
    offset+=n;sent+=n;
    if (offset==256) { ++frames;offset=0;step=0; if(frames==60)stop(); }
    return (ssize_t)n;
}
#define clock_gettime fake_clock
#define usleep fake_usleep
#define nanosleep fake_nanosleep
#define write fake_write
#define main bridge_original_main
#include BRIDGE_SOURCE
#undef main
static void stop(void) { request_worker_stop(); }
static void *fake_open(const char *a,unsigned b,unsigned rate,unsigned ch,unsigned e,unsigned f) {
    (void)a;(void)b;(void)e;(void)f;assert(rate==16000 && ch==1);return (void*)1;
}
static unsigned fake_len(void *p) { (void)p;return 256; }
static int fake_close(void *p) { (void)p;++closed;return 0; }
static int fake_capture(void *h,void *p,unsigned n) {
    (void)h;assert(n==256 && offset==0 && captures<100);++captures;now+=delay_ns;
    if(is("capture_error"))return -1;
    for(unsigned i=0;i<n;i++)expected[i]=(unsigned char)(1+(captures*13+i)%255);
    memcpy(p,expected,n);return 0;
}
int main(int argc,char **argv) {
    assert(argc==3);delay_ns=strtoull(argv[1],0,10)*1000000ULL;mode=argv[2];
    struct bridge_context c;memset(&c,0,sizeof(c));
    c.api.pcm_open=fake_open;c.api.pcm_close=fake_close;
    c.api.pcm_buffer_len=fake_len;c.api.pcm_read=fake_capture;
    downlink_thread(&c);assert(closed==1);
    assert(worker_failed(&c)==(is("error")||is("capture_error")));
    if(is("full")||is("partial")||is("drop")) {
        assert(frames==60 && sent==60*256 && offset==0);
        assert(captures==60+(unsigned)is("drop"));
    }
    if(is("partial")) assert(calls==240 && pauses==61);
    if(is("stop_partial")) assert(captures==1 && frames==0 && sent==127 && calls==2);
    if(is("capture_error")) assert(captures==1 && calls==0);
    if(is("error")) assert(captures==1 && calls==1 && sent==0);
    printf("PASS D6 ms=%llu mode=%s captures=%u frames=%u tty_B=%zu write_calls=%u pacing_sleeps=%u\n",
        (unsigned long long)(delay_ns/1000000ULL),mode,captures,frames,sent,calls,sleep_calls);
    return 0;
}
