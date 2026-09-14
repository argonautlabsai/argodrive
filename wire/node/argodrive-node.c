/* Bounded TCP expert server: independent listeners, queued file reads, shared
 * optional RAM cache. Whole-record protocol; no engine or piece-fallback claim. */
#include "../protocol.h"
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <poll.h>
#include <time.h>
#include <limits.h>

#define CONNECTIONS 8
#define SPANS 16
#define FILES 240
#define RECORDS 131072
typedef struct {int fd;uint64_t offset;uint32_t length;} span;
typedef struct {uint32_t layer,expert,length,count;span spans[SPANS];} record;
static record *records;static size_t count;
static char *paths[FILES];static int files[FILES],nfiles,listeners[3],nlisteners;
static unsigned char identity[32],secret[32];
static volatile sig_atomic_t stopping;
static atomic_uint_fast64_t get_count,bytes_sent,errors;
static void stop(int sig){(void)sig;stopping=1;}
static double now(void){struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);return ts.tv_sec+ts.tv_nsec/1e9;}
static int cmp(const void *a,const void *b){const record *x=a,*y=b;
    if(x->layer!=y->layer)return x->layer<y->layer?-1:1;
    return x->expert==y->expert?0:(x->expert<y->expert?-1:1);
}
static int number(const char *s,uint64_t limit,uint64_t *n){
    if(!s||!*s||*s<'0'||*s>'9')return -1;
    char *end;errno=0;unsigned long long v=strtoull(s,&end,10);
    if(errno||*end||v>limit)return -1;*n=v;return 0;
}
static int source(const char *path){
    if(!path||*path!='/')return -1;
    for(int i=0;i<nfiles;i++)if(!strcmp(path,paths[i]))return files[i];
    if(nfiles==FILES)return -1;
    int fd=open(path,O_RDONLY);if(fd<0)return -1;
#ifdef F_NOCACHE
    if(fcntl(fd,F_NOCACHE,1)<0){close(fd);return -1;}
#endif
    paths[nfiles]=strdup(path);if(!paths[nfiles]){close(fd);return -1;}
    files[nfiles++]=fd;return fd;
}
static int load_index(const char *path){
    FILE *f=fopen(path,"r");if(!f)return -1;char line[8192];int rc=-1;
    if(!fgets(line,sizeof(line),f))goto done;
    line[strcspn(line,"\r\n")]=0;
    if(strncmp(line,"AWRINDEX2\t",10)||awr_hex(line+10,identity))goto done;
    records=calloc(RECORDS,sizeof(*records));if(!records)goto done;
    while(fgets(line,sizeof(line),f)){
        if(!strchr(line,'\n')&&!feof(f))goto done;
        line[strcspn(line,"\r\n")]=0;
        char *save=NULL,*part[5];for(int i=0;i<5;i++)part[i]=strtok_r(i?NULL:line,"\t",&save);
        if(strtok_r(NULL,"\t",&save))goto done;
        uint64_t layer,expert,offset,length;
        if(number(part[0],UINT32_MAX,&layer)||number(part[1],UINT32_MAX,&expert)||
           number(part[2],INT64_MAX,&offset)||number(part[3],AWR_MAX_RECORD,&length)||!length||!part[4])goto done;
        int fd=source(part[4]);struct stat st;
        if(fd<0||fstat(fd,&st)||!S_ISREG(st.st_mode)||offset>(uint64_t)st.st_size||length>(uint64_t)st.st_size-offset)goto done;
        record *r=count?records+count-1:NULL;
        if(!r||r->layer!=layer||r->expert!=expert){
            if(count==RECORDS)goto done;r=records+count++;r->layer=(uint32_t)layer;r->expert=(uint32_t)expert;
        }
        if(r->count==SPANS||length>AWR_MAX_RECORD-r->length)goto done;
        r->spans[r->count++]=(span){fd,offset,(uint32_t)length};r->length+=(uint32_t)length;
    }
    if(ferror(f)||!count)goto done;
    qsort(records,count,sizeof(*records),cmp);
    for(size_t i=1;i<count;i++)if(!cmp(records+i-1,records+i))goto done;
    rc=0;
done:fclose(f);return rc;
}
#include "cache.h"

static int nak(int fd,const awr_header *request,uint64_t seq,const char *reason){
    unsigned char b[160];size_t n=strlen(reason);if(n>sizeof(b)-8)return -1;
    awr_put64(b,seq);memcpy(b+8,reason,n);awr_header h=*request;h.type=AWR_NAK;h.length=(uint32_t)n+8;
    return awr_write_header(fd,&h)||awr_io(fd,b,n+8,1);
}
static int read_record(record *r,unsigned char *buffer){
    size_t base=0;
    for(uint32_t i=0;i<r->count;i++){
        span *s=r->spans+i;size_t done=0;
        while(done<s->length){ssize_t n=pread(s->fd,buffer+base+done,s->length-done,(off_t)(s->offset+done));
            if(n<0&&errno==EINTR)continue;if(n<=0)return -1;done+=(size_t)n;atomic_fetch_add(&read_bytes,(uint64_t)n);
        }base+=s->length;
    }return 0;
}
#include "pipeline.h"

static void serve(int fd){
    /* Darwin inherits O_NONBLOCK from the listening socket. */
    int flags=fcntl(fd,F_GETFL,0);
    if(flags<0||fcntl(fd,F_SETFL,flags&~O_NONBLOCK)<0){close(fd);return;}
    awr_socket_options(fd);awr_header h;
    unsigned char hello[72],reply[24];
    if(awr_read_header(fd,&h)||h.type!=AWR_HELLO||h.length!=72||h.piece!=AWR_WHOLE||h.layer||h.expert)goto done;
    if(awr_io(fd,hello,sizeof(hello),0))goto done;
    unsigned mismatch=0;for(int i=0;i<32;i++)mismatch|=(unsigned)(hello[40+i]^secret[i]);
    if(awr_u32(hello)!=AWR_VERSION||awr_u32(hello+4)!=1||memcmp(hello+8,identity,32)||mismatch){nak(fd,&h,0,"handshake rejected");goto done;}
    awr_put32(reply,AWR_VERSION);awr_put32(reply+4,1);awr_put64(reply+8,count);awr_put64(reply+16,cache_limit);
    h.type=AWR_HELLO_OK;h.length=sizeof(reply);
    if(awr_write_header(fd,&h)||awr_io(fd,reply,sizeof(reply),1))goto done;
    serve_pipeline(fd);
done:close(fd);
}
static void *worker(void *unused){(void)unused;
    while(!stopping){
        struct pollfd p[3];for(int i=0;i<nlisteners;i++)p[i]=(struct pollfd){listeners[i],POLLIN,0};
        if(poll(p,(nfds_t)nlisteners,500)<=0)continue;
        for(int i=0;i<nlisteners&&!stopping;i++)if(p[i].revents&POLLIN){
            int fd=accept(listeners[i],NULL,NULL);if(fd>=0)serve(fd);
        }
    }return NULL;
}
int main(int argc,char **argv){
    if(argc<5||(argc-5)%2){fprintf(stderr,"usage: argodrive-node BIND_IPV4[,IP...] PORT INDEX SECRET_FILE [--cache-mib 0..8192] [--read-ahead 1..4]\n");return 2;}
    for(int i=5;i<argc;i+=2){
        uint64_t v;
        if(!strcmp(argv[i],"--cache-mib")&&!number(argv[i+1],8192,&v))cache_limit=v*1024*1024;
        else if(!strcmp(argv[i],"--read-ahead")&&!number(argv[i+1],MAX_SLOTS,&v)&&v)read_ahead=(int)v;
        else{fprintf(stderr,"Invalid cache or queue setting\n");return 2;}
    }
    uint64_t port;if(number(argv[2],65535,&port))return 2;
    int s=open(argv[4],O_RDONLY);struct stat st;
    if(s<0||fstat(s,&st)||!S_ISREG(st.st_mode)||(st.st_mode&077)||st.st_size!=32||read(s,secret,32)!=32){fprintf(stderr,"Secret must be a private 32-byte file\n");return 2;}close(s);
    if(load_index(argv[3])){fprintf(stderr,"Invalid record index or source range\n");return 2;}
    if(cache_limit){
        cache=calloc(count,sizeof(*cache));if(!cache)return 2;
        for(size_t i=0;i<count;i++)cache[i].prev=cache[i].next=-1;
    }
    signal(SIGPIPE,SIG_IGN);signal(SIGTERM,stop);signal(SIGINT,stop);
    char *addresses=strdup(argv[1]),*save=NULL;
    if(!addresses||!*addresses)return 2;
    for(char *ip=strtok_r(addresses,",",&save);ip;ip=strtok_r(NULL,",",&save)){
        struct sockaddr_in addr={.sin_family=AF_INET};
        if(nlisteners==3||inet_pton(AF_INET,ip,&addr.sin_addr)!=1||addr.sin_addr.s_addr==INADDR_ANY){fprintf(stderr,"Explicit IPv4 binds required\n");return 2;}
        int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0)return 2;int one=1;
        setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));addr.sin_port=htons((uint16_t)port);
        if(bind(fd,(struct sockaddr*)&addr,sizeof(addr))||listen(fd,CONNECTIONS)||fcntl(fd,F_SETFL,O_NONBLOCK)<0){perror("listen");return 2;}
        socklen_t len=sizeof(addr);getsockname(fd,(struct sockaddr*)&addr,&len);port=ntohs(addr.sin_port);
        listeners[nlisteners++]=fd;
    }
    free(addresses);if(!nlisteners)return 2;
    printf("{\"event\":\"ready\",\"protocol\":2,\"port\":%u,\"records\":%zu,\"cache_bytes\":%llu,\"read_ahead\":%d,\"listeners\":%d,\"max_connections\":8,\"nocache\":%s}\n",(unsigned)port,count,(unsigned long long)cache_limit,read_ahead,nlisteners,
#ifdef F_NOCACHE
    "true"
#else
    "false"
#endif
    );fflush(stdout);double start=now();pthread_t threads[CONNECTIONS];int started=0;
    for(;started<CONNECTIONS;started++)if(pthread_create(threads+started,NULL,worker,NULL)){stopping=1;break;}
    for(int i=0;i<started;i++)pthread_join(threads[i],NULL);
    char stats[1536];stats_json(stats,sizeof(stats));
    printf("{\"event\":\"stopped\",\"seconds\":%.3f,\"stats\":%s}\n",now()-start,stats);
    for(int i=0;i<nlisteners;i++)close(listeners[i]);
    for(int i=0;i<nfiles;i++){close(files[i]);free(paths[i]);}
    if(cache){for(size_t i=0;i<count;i++)free(cache[i].data);free(cache);}
    free(records);return started==CONNECTIONS?0:2;
}
