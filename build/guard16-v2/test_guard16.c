#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <poll.h>
#ifndef BRIDGE_SOURCE
#define BRIDGE_SOURCE "mavo_pcm_bridge_16k_clock8_guard16.c"
#endif
#define MS 1000000ULL
static uint64_t now = 1000*MS, delay_ns, produced, taken, dropped, real_bytes;
static uint64_t armed, first_write, last_write;
static unsigned reads, writes, zeros, first_reads, polls, closed, injected;
static unsigned reads_since_write, max_due_reads, steps;
static unsigned stopped_reads, stopped_writes, stop_seen;
static unsigned fragment_limit;
static size_t bytes_since_write, max_due_bytes;
static unsigned char expected[3072], pending;
static size_t used;
static int carried, rebuffer, baseline;
static const char *mode;
static int is(const char *s) { return strcmp(mode,s)==0; }
static void stop(void);
static void tick(void) { assert(++steps < 100000); }
static unsigned char byte_at(uint64_t n) { return (unsigned char)(1 + ((n*17+n/251)%255)); }
static void production(void) {
    uint64_t elapsed=now-1000*MS;
    if (is("initial_empty")) produced=0;
    else if (is("flood")) produced=taken+4096;
    else if (is("nodata") || is("eof")) produced=1024;
    else if (is("burst")) produced=(elapsed/(24*MS)+1)*768;
    else produced=(elapsed/(8*MS)+1)*256;
}
static int fake_clock(clockid_t id, struct timespec *t) {
    (void)id; t->tv_sec=now/1000000000ULL; t->tv_nsec=now%1000000000ULL; return 0;
}
static int fake_sleep(useconds_t u) { tick(); now+=(uint64_t)u*1000; return 0; }
static int fake_poll(struct pollfd *p, nfds_t n, int timeout) {
    tick(); ++polls; assert(n==1 && timeout>=0 && timeout<=8);
    p->revents=0;
    if (is("initial_empty") && polls==5) { stop(); return 0; }
    if (writes>=2 && !injected) {
        if (is("poll_eintr")) { injected=1; errno=EINTR; return -1; }
        if (is("poll_error")) { injected=1; errno=EIO; return -1; }
        if (is("hup")) { injected=1; p->revents=POLLHUP; return 1; }
        if (is("pollerr")) { injected=1; p->revents=POLLERR; return 1; }
        if (is("nval")) { injected=1; p->revents=POLLNVAL; return 1; }
        if (is("stop_poll")) { injected=1; stop(); p->revents=POLLIN; return 1; }
    }
    production();
    if (produced<=taken && !(is("eof") && writes>=2)) {
        uint64_t end=now+(uint64_t)timeout*MS;
        uint64_t period=is("burst")?24*MS:8*MS;
        uint64_t next=1000*MS+((now-1000*MS)/period+1)*period;
        now=(!is("nodata") && next<end)?next:end;
        production();
    }
    if (produced>taken || (is("eof") && writes>=2)) { p->revents=POLLIN; return 1; }
    return 0;
}
static void append_byte(unsigned char b) {
    if (!carried) { pending=b; carried=1; return; }
    /* Independent sample-stream oracle: retain newest bounded FIFO contents. */
    if (used+2>sizeof(expected)) {
        memmove(expected,expected+256,used-256); used-=256; dropped+=256;
    }
    expected[used++]=pending; expected[used++]=b; carried=0;
}
static ssize_t fake_read(int fd, void *p, size_t n) {
    (void)fd; tick(); assert(n<=256 && n>0);
    if (writes>=2 && !injected) {
        if (is("read_eintr") || is("eagain") || is("read_error")) {
            injected=1; errno=is("read_eintr")?EINTR:is("eagain")?EAGAIN:EIO; return -1;
        }
        if (is("zero_once")) { injected=1; return 0; }
    }
    if (is("eof") && writes>=2) { injected=1; return 0; }
    production(); assert(produced>taken);
    if (n>produced-taken) n=(size_t)(produced-taken);
    if ((is("odd") || is("frag127")) && n>127) n=127;
    if (is("frag1") && n>1) n=1;
    if (is("frag255") && n>255) n=255;
    if (fragment_limit && n>fragment_limit) n=fragment_limit;
    for (size_t i=0;i<n;i++) {
        unsigned char b=byte_at(taken++); ((unsigned char *)p)[i]=b; append_byte(b);
    }
    ++reads;
    if (writes) {
        bytes_since_write += n;
        if (now>=last_write+8*MS && bytes_since_write>max_due_bytes)
            max_due_bytes=bytes_since_write;
        ++reads_since_write;
        if (now>=last_write+8*MS && reads_since_write>max_due_reads)
            max_due_reads=reads_since_write;
    }
    if (!armed && used>=768) armed=now;
    if (is("flood")) now+=100000; /* finite real syscall overhead */
    if (is("stop_read") && writes>=2) { injected=1; stop(); }
    return (ssize_t)n;
}
#define clock_gettime fake_clock
#define usleep fake_sleep
#define poll fake_poll
#define read fake_read
#define main bridge_original_main
#include BRIDGE_SOURCE
#undef main
static void stop(void) {
    if (!stop_seen) { stopped_reads=reads; stopped_writes=writes; stop_seen=1; }
    request_worker_stop();
}
static void *fake_open(const char *a,unsigned b,unsigned rate,unsigned channels,unsigned e,unsigned f) {
    (void)a;(void)b;(void)e;(void)f; assert(rate==16000 && channels==1); return (void *)1;
}
static unsigned fake_length(void *p) { (void)p;return 256; }
static int fake_close(void *p) { (void)p;++closed;return 0; }
static int fake_write(void *h,void *p,unsigned n) {
    (void)h; tick(); assert(n==256); unsigned char *b=p;
    if (!writes) { first_reads=reads; first_write=now; assert(armed && first_write>=armed+16*MS); }
    if (used<256) rebuffer=1;
    if (rebuffer && used>=768) rebuffer=0;
    if (rebuffer) { for(unsigned i=0;i<n;i++) assert(b[i]==0); ++zeros; }
    else {
        assert(used>=256 && memcmp(b,expected,256)==0);
        memmove(expected,expected+256,used-256); used-=256; real_bytes+=256;
    }
    last_write=now; reads_since_write=0; bytes_since_write=0; ++writes; now+=delay_ns;
    if (is("write_error")) return -1;
    if (writes==60 || (is("stop_write") && writes==2)) stop();
    return 0;
}
int main(int argc,char **argv) {
    assert(argc>=3); delay_ns=strtoull(argv[1],0,10)*MS;mode=argv[2];baseline=argc>3;
    if (getenv("FRAGMENT")) fragment_limit=(unsigned)strtoul(getenv("FRAGMENT"),0,10);
    struct bridge_context c; memset(&c,0,sizeof(c));
    c.api.pcm_open=fake_open;c.api.pcm_close=fake_close;
    c.api.pcm_buffer_len=fake_length;c.api.pcm_write=fake_write;
    if (is("stop_initial")) stop();
    uplink_thread(&c);
    if (!is("flood")) production(); /* Include arrivals during the last write. */
    assert(taken==real_bytes+dropped+used+(unsigned)carried);
    assert(closed==(is("stop_initial")?0:1));
    int fatal=is("read_error")||is("poll_error")||is("hup")||is("pollerr")||is("nval")||is("write_error");
    assert(worker_failed(&c)==fatal);
    if (is("steady") || is("burst") || is("odd") || is("frag1") || is("frag127") || is("frag255")) {
        assert(writes==60 && dropped==0);
        if (baseline && delay_ns>=8*MS) { assert(reads==first_reads && zeros>0); }
        else { assert(reads>first_reads+40 && zeros==0); }
    }
    if (is("flood")) { assert(writes==60 && dropped>0 && zeros==0); }
    if (is("nodata") || is("eof")) assert(writes==60 && zeros>0);
    if (is("odd")) assert(reads>first_reads+40);
    if (is("stop_initial")) assert(writes==0 && reads==0 && polls==0);
    if (is("initial_empty")) assert(writes==0 && reads==0 && polls==5);
    if (is("stop_write") || is("stop_poll") || is("stop_read"))
        assert(stop_seen && reads==stopped_reads && writes==stopped_writes);
    if (is("stop_write") || is("stop_poll") || is("stop_read")) assert(writes==2);
    if (!baseline && delay_ns>=8*MS) {
        assert(max_due_reads<=256); assert(max_due_bytes<=256);
    }
    if (is("read_eintr")||is("poll_eintr")||is("eagain")||is("zero_once")) {
        assert(injected && writes==60 && reads>first_reads+40 && zeros==0);
    }
    printf("PASS ms=%llu mode=%s writes=%u zeros=%u reads=%u first_reads=%u tty_B=%llu real_B=%llu drop_B=%llu fifo_B=%zu carry=%d backlog_B=%llu max_due_reads=%u\n",
        (unsigned long long)(delay_ns/MS),mode,writes,zeros,reads,first_reads,
        (unsigned long long)taken,(unsigned long long)real_bytes,(unsigned long long)dropped,used,carried,
        (unsigned long long)(produced-taken),max_due_reads);
    return 0;
}
