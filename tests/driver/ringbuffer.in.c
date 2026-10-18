// SPDX-License-Identifier: GPL-2.0-or-later
// QDC507 synthetic offline regression fixture, 2026-10-18.
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ringbuffer.h"
static unsigned long checks, integrations;
static uint32_t rng=0x507c1cc;
static uint32_t rnd(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static size_t ref(const unsigned char *s,size_t n,const unsigned char *p,size_t m,size_t first){
    if(!m || m>n)return SIZE_MAX;
    /* len=1 is deliberately the existing control-prefix API, not memmem. */
    if(m==1){
        for(size_t part=0;part<2;part++){
            size_t a=part?first:0,b=part?n:first;
            for(size_t j=a;j<b;j++){if((char)s[j]>=' ')break;if((char)s[j]==(char)p[0])return j;}
        }
        return SIZE_MAX;
    }
    for(size_t j=0;j<=n-m;j++)if(!memcmp(s+j,p,m))return j;
    return SIZE_MAX;
}
static void verify_iov(struct ringbuffer *r,struct iovec *v,int k,const unsigned char *s,size_t n){
    assert(k==1 || k==2);size_t total=0;
    for(int j=0;j<k;j++){
        assert((unsigned char*)v[j].iov_base >= (unsigned char*)r->buffer);
        assert((unsigned char*)v[j].iov_base <= (unsigned char*)r->buffer+r->size);
        assert(v[j].iov_len <= (size_t)((unsigned char*)r->buffer+r->size-(unsigned char*)v[j].iov_base));
        assert(total+v[j].iov_len<=n);
        assert(!memcmp(v[j].iov_base,s+total,v[j].iov_len)); total+=v[j].iov_len;
    }
    assert(total==n);if(k==1)assert(v[1].iov_len==0);
    size_t first=r->size-r->read;
    assert(k==(n>first?2:1) || (k==2 && n==first && v[1].iov_len==0));
}
static void one(size_t cap,size_t head,const unsigned char*s,size_t n,const unsigned char*p,size_t m){
    unsigned char *b=malloc(cap),*pat=malloc(m?m:1);assert(b&&pat);
    memset(b,0xa5,cap);if(m)memcpy(pat,p,m);
    struct ringbuffer r;rb_init(&r,b,cap);r.read=r.write=head;
    assert(rb_write(&r,(const char*)s,n)==n);
    struct ringbuffer saved=r;struct iovec v[2]={{0,777},{0,777}};
    size_t first=cap-head;if(first>n)first=n;
    size_t expected=ref(s,n,pat,m,first);
    int k=rb_read_until_mem_iov(&r,v,pat,m);
    if((k!=0)!=(expected!=SIZE_MAX)){
        fprintf(stderr,"mismatch cap=%zu head=%zu used=%zu len=%zu expected=%zu k=%d check=%lu\n",cap,head,n,m,expected,k,checks);abort();
    }
    assert(!memcmp(&r,&saved,sizeof r));
    if(k){
        int want=(expected>cap-head || (m==1 && expected==cap-head))?2:1;
        assert(k==want);verify_iov(&r,v,k,s,expected);
    }
    checks++;free(pat);free(b);
}
static void exhaustive(void){
    unsigned char s[16],p[16];
    for(size_t cap=1;cap<=9;cap++)for(size_t n=0;n<=cap;n++)
    for(unsigned data=0;data<(1u<<n);data++){
        for(size_t j=0;j<n;j++)s[j]=(data>>j&1)?'A':'B';
        for(size_t m=2;m<=5;m++)for(unsigned pat=0;pat<(1u<<m);pat++){
            for(size_t j=0;j<m;j++)p[j]=(pat>>j&1)?'A':'B';
            for(size_t h=0;h<cap;h++)one(cap,h,s,n,p,m);
        }
    }
    puts("PASS exhaustive binary/repeated-prefix patterns, lengths 2..5, capacities 1..9, all used/head states");
}
static void vectors(void){
    {
        struct ringbuffer zero;struct iovec v[2];rb_init(&zero,NULL,0);
        assert(rb_read_until_mem_iov(&zero,v,NULL,0)==0);
        assert(rb_read_until_mem_iov(&zero,v,NULL,SIZE_MAX)==0);
        assert(rb_read_all_iov(&zero,v)==0 && rb_write_iov(&zero,v)==0);
    }
    const unsigned char *ok=(const unsigned char*)"\r\n\r\nOK\r\n";unsigned char s[1200],p[1100];
    for(size_t split=1;split<8;split++)for(size_t before=0;before<=19;before++)for(size_t after=0;after<=19;after++){
        memset(s,'x',before);memcpy(s+before,ok,8);memset(s+before+8,'y',after);
        one(64,64-before-split,s,before+8+after,ok,8);
    }
    memset(s,'x',348);memcpy(s+348,ok,8);one(1024,673,s,356,ok,8);
    memset(s+356,'z',17);one(1024,676,s+3,370,ok,8);
    for(size_t h=0;h<1024;h++){
        memset(s,'x',1024);memcpy(s+349,ok,8);one(1024,h,s,1024,ok,8);
        one(1024,h,s,0,ok,8);one(1024,h,s,7,ok,8);
        one(1024,h,s,1024,ok,0);memset(p,'x',1025);one(1024,h,s,1024,p,1025);
    }
    for(size_t n=0;n<8;n++){memcpy(s,"abc",3);memcpy(s+3,ok,n);for(size_t h=0;h<32;h++)one(32,h,s,n+3,ok,8);}
    const unsigned char single[]={0,'\r','\n','A',0xff};
    for(size_t h=0;h<16;h++)for(size_t n=0;n<=16;n++)for(size_t q=0;q<sizeof single;q++){
        for(size_t j=0;j<n;j++)s[j]=single[j%sizeof single];one(16,h,s,n,single+q,1);
    }
    puts("PASS OK split 1..7, synthetic [351+5]/[348+22], prefix/suffix, full/empty, len0/oversized, all 1024 heads, control-prefix len1");
    for(unsigned t=0;t<250000;t++){
        size_t cap=1+rnd()%256,n=rnd()%(cap+1),h=rnd()%cap,m=rnd()%(cap+3);
        for(size_t j=0;j<n;j++)s[j]=(unsigned char)(rnd()%9);
        for(size_t j=0;j<m;j++)p[j]=(unsigned char)(rnd()%9);
        if(m && m<=n && (t&1))memcpy(p,s+rnd()%(n-m+1),m);
        one(cap,h,s,n,p,m);
    }
    puts("PASS 250000 deterministic randomized linear-reference comparisons, seed 0x507c1cc");
}
static void updates(void){
    unsigned char b[64],s[64],out[64];struct ringbuffer r;struct iovec v[2];
    for(size_t cap=1;cap<=64;cap++)for(size_t h=0;h<cap;h++){
        rb_init(&r,b,cap);r.read=r.write=h;
        for(size_t i=0;i<cap;i++)s[i]=(unsigned char)i;
        int k=rb_write_iov(&r,v);size_t total=0;
        for(int j=0;j<k;j++){memcpy(v[j].iov_base,s+total,v[j].iov_len);total+=v[j].iov_len;}
        assert(total==cap);assert(rb_write_upd(&r,cap+5)==cap);assert(rb_free(&r)==0);assert(rb_write_iov(&r,v)==0);
        k=rb_read_all_iov(&r,v);verify_iov(&r,v,k,s,cap);
        assert(rb_memcmp(&r,(const char*)s,cap)==0);assert(rb_read_n_iov(&r,v,cap+1)==0);assert(rb_read_n_iov(&r,v,0)==0);
        for(size_t n=1;n<=cap;n++){k=rb_read_n_iov(&r,v,n);verify_iov(&r,v,k,s,n);}
        for(size_t j=0;j<cap;j++){assert(rb_read_n_iov(&r,v,1)==1);out[j]=*(unsigned char*)v[0].iov_base;assert(rb_read_upd(&r,1)==1);}
        assert(!memcmp(out,s,cap));assert(r.read==0&&r.write==0&&r.used==0);assert(rb_read_upd(&r,999)==0);
        assert(rb_read_all_iov(&r,v)==0);assert(rb_memcmp(&r,"",0)==-1);
        rb_reset(&r);assert(rb_write(&r,(const char*)s,cap)==cap);assert(rb_read_upd(&r,cap+1)==cap);
        /* readv update can retain write==size: zero first vector is valid. */
        if(cap>1){rb_reset(&r);assert(rb_write_upd(&r,cap)==cap);assert(r.write==cap);assert(rb_read_upd(&r,1)==1);k=rb_write_iov(&r,v);assert(k==2&&v[0].iov_len==0&&v[1].iov_len==1);assert(rb_write_upd(&r,1)==1);assert(r.write==1);}
    }
    puts("PASS actual read/write/update/n/all/memcmp APIs: all capacities 1..64 and heads, clamp/full/empty/write==size wrap");
}
/* PRODUCTION_AT_READ */
static void integration(void){
    const char *body="+CMGR: 0,,3\r\n001122";
    const char *clcc="+CLCC: 1,0,0,0,0";
    char stream[256];snprintf(stream,sizeof stream,"\r\n%s\r\n\r\nOK\r\n\r\n%s\r\n\r\nOK\r\n",body,clcc);
    const char *expected[]={body,"OK",clcc,"OK"};size_t length=strlen(stream);
    for(size_t head=0;head<128;head++)for(size_t chunk=1;chunk<=length;chunk++){
        unsigned char b[128];struct ringbuffer r;rb_init(&r,b,sizeof b);r.read=r.write=head;
        int state=0,count=0;size_t skip=0,pos=0;struct ast_str scratch={0};
        while(pos<length){size_t add=chunk;if(add>length-pos)add=length-pos;assert(rb_write(&r,stream+pos,add)==add);pos+=add;
            struct iovec v[2];int k;
            while((k=at_read_result_iov("synthetic",&state,&skip,&r,v,&scratch))>0){
                assert(count<4);size_t n=at_combine_iov(&scratch,v,k);
                assert(n==strlen(expected[count]));assert(!memcmp(scratch.data,expected[count],n));
                assert(skip==(count==0?4u:1u));assert(rb_read_upd(&r,n+skip)==n+skip);skip=0;count++;
            }
            assert(skip==0);
        }
        assert(count==4 && rb_used(&r)==1 && state==0);
        assert(rb_memcmp(&r,"\n",1)==0);integrations++;
    }
    printf("PASS %lu production at_read_result_iov CMGR/OK/CLCC/OK integrations: all 128 heads, every fixed chunk size 1..%zu; exact contents/skip/state/trailing-LF\n",integrations,length);
}
static void long_integration(void){
    unsigned char data[356],b[1024];memset(data,'0',348);
    memcpy(data,"\r\n+CMGR: 0,,166\r\n",17);memcpy(data+348,"\r\n\r\nOK\r\n",8);
    for(size_t split=1;split<8;split++){
        struct ringbuffer r;rb_init(&r,b,sizeof b);r.read=r.write=1024-348-split;
        assert(rb_write(&r,(const char*)data,348+split)==348+split);
        int state=0;size_t skip=0;struct iovec v[2];struct ast_str scratch={0};
        assert(at_read_result_iov("synthetic-long",&state,&skip,&r,v,&scratch)==0 && skip==0);
        assert(rb_write(&r,(const char*)data+348+split,8-split)==8-split);
        int k=at_read_result_iov("synthetic-long",&state,&skip,&r,v,&scratch);assert(k>0 && skip==4);
        size_t n=at_combine_iov(&scratch,v,k);assert(n==346 && !memcmp(scratch.data,data+2,n));
        assert(rb_read_upd(&r,n+skip)==n+skip);skip=0;
        k=at_read_result_iov("synthetic-long",&state,&skip,&r,v,&scratch);assert(k>0 && skip==1);
        n=at_combine_iov(&scratch,v,k);assert(n==2 && !memcmp(scratch.data,"OK",2));
        assert(rb_read_upd(&r,n+skip)==n+skip);skip=0;
        assert(at_read_result_iov("synthetic-long",&state,&skip,&r,v,&scratch)==0);
        assert(rb_used(&r)==1 && rb_memcmp(&r,"\n",1)==0);
    }
    puts("PASS 7 long synthetic CMGR integrations, all terminator splits including physical [351+5], partial then complete, body/OK/skip verified");
}
int main(int argc,char **argv){
    (void)argv;
    if(argc>1){unsigned char s[356];memset(s,'x',348);memcpy(s+348,"\r\n\r\nOK\r\n",8);one(1024,673,s,sizeof s,(const unsigned char*)"\r\n\r\nOK\r\n",8);puts("PASS synthetic 3+5 reproducer");return 0;}
    vectors();exhaustive();updates();integration();long_integration();printf("ALL PASS: %lu search comparisons; %lu parser integrations\n",checks,integrations);return 0;
}
