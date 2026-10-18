// SPDX-License-Identifier: GPL-2.0-or-later
// QDC507 synthetic offline regression fixture, 2026-10-18.
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "ringbuffer.h"
int main(void){
    size_t page=(size_t)sysconf(_SC_PAGESIZE);unsigned long tests=0;
    for(int side=0;side<2;side++)for(size_t split=1;split<8;split++)for(size_t prefix=0;prefix<64;prefix++){
        unsigned char *pm=mmap(NULL,3*page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        unsigned char *bm=mmap(NULL,3*page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        assert(pm!=MAP_FAILED && bm!=MAP_FAILED);
        assert(!mprotect(pm+page,page,PROT_READ|PROT_WRITE));assert(!mprotect(bm+page,page,PROT_READ|PROT_WRITE));
        unsigned char *pat=pm+page+(side?page-8:0),*buf=bm+page+(side?page-128:0);
        memcpy(pat,"\r\n\r\nOK\r\n",8);struct ringbuffer r;rb_init(&r,buf,128);r.read=r.write=128-prefix-split;
        unsigned char data[80];memset(data,'x',prefix);memcpy(data+prefix,pat,8);
        assert(rb_write(&r,(const char*)data,prefix+8)==prefix+8);struct iovec v[2];
        assert(rb_read_until_mem_iov(&r,v,pat,8)==1);assert(v[0].iov_len==prefix && v[1].iov_len==0);
        assert(!memcmp(v[0].iov_base,data,prefix));assert(!munmap(pm,3*page));assert(!munmap(bm,3*page));tests++;
    }
    printf("PASS %lu guard-page cases: exact 8-byte pattern and 128-byte ring against inaccessible page, split1..7, prefix0..63, both sides\n",tests);return 0;
}
