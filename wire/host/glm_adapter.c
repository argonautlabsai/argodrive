/* Experimental CLI integration. All receives are synchronous and caller-owned.
 * A failure closes its connection before local fallback overwrites the buffer.
 * Exact source identity and component hashes prevent a different model or a
 * partial/corrupt remote component from being accepted as a landed GPU buffer. */
#include "glm_adapter.h"
#include "../protocol.h"
#include <CommonCrypto/CommonDigest.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <inttypes.h>

int awr_connect(const char *,int,const char *,const char *);
int awr_get(int,uint64_t,uint32_t,uint32_t,void *,size_t);
void awr_close(int);

#define MAX_RECORDS 6144
#define LINKS 4
typedef struct { uint64_t offset,len; uint32_t layer,expert; unsigned char sha[32]; } record;
typedef struct { pthread_mutex_t mu; int fd; uint64_t seq,bytes,gets,errors; } connection;
static connection links[LINKS];
static record records[MAX_RECORDS];
static size_t count;
static uint64_t model_size,model_dev,model_ino,model_sec,model_nsec;
static pthread_once_t once=PTHREAD_ONCE_INIT;
static pthread_mutex_t init_mu=PTHREAD_MUTEX_INITIALIZER;
static int model_checked,enabled;
static unsigned ticket;
static uint64_t eligible,busy;
static char identity[65];

static void summary(void) {
    uint64_t gets=0,bytes=0,errors=0;
    for(int i=0;i<LINKS;i++) {
        pthread_mutex_lock(&links[i].mu);
        gets+=links[i].gets;bytes+=links[i].bytes;errors+=links[i].errors;
        fprintf(stderr,"argodrive-glm: link=%d gets=%"PRIu64" bytes=%"PRIu64" errors=%"PRIu64"\n",i,links[i].gets,links[i].bytes,links[i].errors);
        if(links[i].fd>=0) { awr_close(links[i].fd);links[i].fd=-1; }
        pthread_mutex_unlock(&links[i].mu);
    }
    fprintf(stderr,"argodrive-glm: remote_gets=%"PRIu64" remote_bytes=%"PRIu64" failed_reads=%"PRIu64" eligible=%"PRIu64" local_busy=%"PRIu64" sha256=every-read\n",gets,bytes,errors,eligible,busy);
}

static void init(void) {
    const char *map=getenv("DS4_ARGODRIVE_MAP");
    if(!map||!*map)return;
    const char *endpoints=getenv("DS4_ARGODRIVE_ENDPOINTS"),*secret=getenv("DS4_ARGODRIVE_SECRET");
    if(!endpoints||!secret) { fprintf(stderr,"argodrive-glm: incomplete configuration; local only\n");return; }
    FILE *f=fopen(map,"r");char line[512],hex[65],extra;unsigned char identity_bytes[32];
    if(!f)return;
    if(!fgets(line,sizeof(line),f)||sscanf(line,"AWRDS41 %"SCNu64" %"SCNu64" %"SCNu64" %"SCNu64" %"SCNu64" %64s %c",
        &model_size,&model_dev,&model_ino,&model_sec,&model_nsec,identity,&extra)!=6||awr_hex(identity,identity_bytes))goto invalid;
    while(fgets(line,sizeof(line),f)) {
        if(count==MAX_RECORDS)goto invalid;
        record *r=&records[count];
        if(sscanf(line,"%"SCNu64" %"SCNu64" %"SCNu32" %"SCNu32" %64s %c",&r->offset,&r->len,&r->layer,&r->expert,hex,&extra)!=5||
           !r->len||r->len>AWR_MAX_RECORD||r->offset>model_size||r->len>model_size-r->offset||awr_hex(hex,r->sha)||
           (count&&r->offset<records[count-1].offset+records[count-1].len))goto invalid;
        count++;
    }
    if(ferror(f)||!count)goto invalid;
    fclose(f);
    char ips[2][16];int ports[2];
    if(sscanf(endpoints,"%15[0-9.]:%d,%15[0-9.]:%d%c",ips[0],&ports[0],ips[1],&ports[1],&extra)!=4){count=0;return;}
    for(int i=0;i<LINKS;i++) {
        pthread_mutex_init(&links[i].mu,NULL);
        links[i].fd=awr_connect(ips[i%2],ports[i%2],identity,secret);
        if(links[i].fd>=0) {
            /* A dead peer must not hold the inference buffer for the transport's
             * general ten-second diagnostic timeout. No background retry. */
            struct timeval tv={0,500000};
            setsockopt(links[i].fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
            setsockopt(links[i].fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
            enabled++;
        }
    }
    atexit(summary);
    fprintf(stderr,"argodrive-glm: %zu components, %d connections, model binding pending\n",count,enabled);
    return;
invalid:
    fclose(f);count=0;
    fprintf(stderr,"argodrive-glm: rejected invalid map; local only\n");
}

int argodrive_glm_read(int model_fd,uint64_t offset,uint64_t len,void *dst) {
    pthread_once(&once,init);
    if(!enabled||!dst)return 0;
    int checked=__atomic_load_n(&model_checked,__ATOMIC_ACQUIRE);
    if(!checked) {
      pthread_mutex_lock(&init_mu);
      if(!model_checked) {
        struct stat st;
        checked=(!fstat(model_fd,&st)&&S_ISREG(st.st_mode)&&
            (uint64_t)st.st_size==model_size&&(uint64_t)st.st_dev==model_dev&&(uint64_t)st.st_ino==model_ino&&
            (uint64_t)st.st_mtimespec.tv_sec==model_sec&&(uint64_t)st.st_mtimespec.tv_nsec==model_nsec)?1:-1;
        __atomic_store_n(&model_checked,checked,__ATOMIC_RELEASE);
        fprintf(stderr,"argodrive-glm: model_binding=%s\n",checked==1?"verified":"rejected-local-only");
      }
      checked=model_checked;
      pthread_mutex_unlock(&init_mu);
    }
    if(checked!=1)return 0;
    size_t lo=0,hi=count;
    while(lo<hi) { size_t mid=lo+(hi-lo)/2;if(records[mid].offset<offset)lo=mid+1;else hi=mid; }
    if(lo==count||records[lo].offset!=offset||records[lo].len!=len)return 0;
    const record *r=&records[lo];
    __atomic_fetch_add(&eligible,1,__ATOMIC_RELAXED);
    unsigned start=__atomic_fetch_add(&ticket,1,__ATOMIC_RELAXED)%LINKS;
    connection *c=NULL;
    for(unsigned n=0;n<LINKS;n++) {
        connection *candidate=&links[(start+n)%LINKS];
        if(!pthread_mutex_trylock(&candidate->mu)) {
            if(candidate->fd>=0){c=candidate;break;}
            pthread_mutex_unlock(&candidate->mu);
        }
    }
    /* Local reads continue immediately when all network lanes are occupied.
     * This initial adapter admits only four in flight; it has no unbounded queue. */
    if(!c){__atomic_fetch_add(&busy,1,__ATOMIC_RELAXED);return 0;}
    int ok=!awr_get(c->fd,++c->seq,r->layer,r->expert,dst,(size_t)len);
    if(ok) {
        unsigned char digest[32];
        CC_SHA256(dst,(CC_LONG)len,digest);
        ok=!memcmp(digest,r->sha,32);
    }
    if(ok){c->gets++;c->bytes+=len;}
    else { c->errors++;awr_close(c->fd);c->fd=-1; }
    pthread_mutex_unlock(&c->mu);
    return ok;
}
