/* One reader and one sender per connection. Slots stay owned until send ends.
 * TCP responses remain ordered, including NAK and STAT. The host controls the
 * request window; the node separately bounds memory/read-ahead to 1–4 slots. */
#define MAX_SLOTS 4
static int read_ahead=1;
typedef struct {
    awr_header header;
    uint64_t seq;
    unsigned char *private_data,*data;
    size_t capacity,length;
    int cache_index;
    const char *error;
    char statistics[1536];
} response_slot;
typedef struct {
    int fd,used,read_pos,send_pos,done,cancel;
    pthread_mutex_t lock;
    pthread_cond_t changed;
    response_slot slots[MAX_SLOTS];
} connection_queue;

static void prepare_slot(response_slot *slot){
    if(slot->header.type==AWR_STAT){
        int n=stats_json(slot->statistics,sizeof(slot->statistics));
        slot->data=(unsigned char *)slot->statistics;slot->length=(size_t)n;return;
    }
    atomic_fetch_add(&get_count,1);
    record key={.layer=slot->header.layer,.expert=slot->header.expert};
    record *r=bsearch(&key,records,count,sizeof(*records),cmp);
    if(!r){slot->error="record not indexed";atomic_fetch_add(&errors,1);return;}
    int fill=0;slot->cache_index=cache_acquire(r,&fill);
    if(slot->cache_index>=0)slot->data=cache[slot->cache_index].data;
    else{
        if(slot->capacity<r->length){
            unsigned char *p=realloc(slot->private_data,r->length);
            if(!p){slot->error="slot allocation failed";atomic_fetch_add(&errors,1);return;}
            slot->private_data=p;slot->capacity=r->length;
        }
        slot->data=slot->private_data;
    }
    slot->length=r->length;
    if(slot->cache_index<0||fill){
        double start=now();int rc=read_record(r,slot->data);
        atomic_fetch_add(&read_calls,1);atomic_fetch_add(&read_ns,(uint64_t)((now()-start)*1e9));
        if(fill)cache_publish(slot->cache_index,!rc);
        if(rc){slot->cache_index=-1;slot->data=NULL;slot->error="source read failed";atomic_fetch_add(&errors,1);}
    }
}
static void *read_requests(void *arg){
    connection_queue *q=arg;uint64_t last=0;
    for(;;){
        pthread_mutex_lock(&q->lock);
        while(q->used==read_ahead&&!q->cancel)pthread_cond_wait(&q->changed,&q->lock);
        if(q->cancel||stopping){pthread_mutex_unlock(&q->lock);break;}
        response_slot *slot=q->slots+q->read_pos;
        pthread_mutex_unlock(&q->lock);
        unsigned char id[8];awr_header h;
        if(awr_read_header(q->fd,&h)||h.length!=8||h.piece||
           (h.type!=AWR_GET&&h.type!=AWR_STAT)||
           (h.type==AWR_STAT&&(h.layer||h.expert))||awr_io(q->fd,id,8,0))break;
        uint64_t seq=awr_u64(id);if(!seq||seq<=last)break;last=seq;
        slot->header=h;slot->seq=seq;slot->cache_index=-1;slot->error=NULL;slot->length=0;slot->data=NULL;
        prepare_slot(slot);
        pthread_mutex_lock(&q->lock);
        q->read_pos=(q->read_pos+1)%read_ahead;q->used++;
        pthread_cond_broadcast(&q->changed);pthread_mutex_unlock(&q->lock);
    }
    pthread_mutex_lock(&q->lock);q->done=1;pthread_cond_broadcast(&q->changed);pthread_mutex_unlock(&q->lock);
    return NULL;
}
static void serve_pipeline(int fd){
    connection_queue q={.fd=fd};
    pthread_mutex_init(&q.lock,NULL);pthread_cond_init(&q.changed,NULL);
    for(int i=0;i<MAX_SLOTS;i++)q.slots[i].cache_index=-1;
    pthread_t reader;
    if(pthread_create(&reader,NULL,read_requests,&q))goto cleanup;
    for(;;){
        pthread_mutex_lock(&q.lock);
        while(!q.used&&!q.done)pthread_cond_wait(&q.changed,&q.lock);
        if(!q.used){pthread_mutex_unlock(&q.lock);break;}
        response_slot *slot=q.slots+q.send_pos;
        pthread_mutex_unlock(&q.lock);
        int rc;
        if(slot->error)rc=nak(fd,&slot->header,slot->seq,slot->error);
        else{
            double start=now();unsigned char id[8];awr_put64(id,slot->seq);
            awr_header h=slot->header;h.type=h.type==AWR_STAT?AWR_STAT:AWR_DATA;h.length=(uint32_t)slot->length+8;
            rc=awr_write_header(fd,&h)||awr_io(fd,id,8,1)||awr_io(fd,slot->data,slot->length,1);
            if(h.type==AWR_DATA){
                atomic_fetch_add(&send_ns,(uint64_t)((now()-start)*1e9));
                if(!rc)atomic_fetch_add(&bytes_sent,slot->length);
            }
        }
        cache_release(slot->cache_index);slot->cache_index=-1;
        pthread_mutex_lock(&q.lock);q.used--;q.send_pos=(q.send_pos+1)%read_ahead;
        if(rc)q.cancel=1;
        pthread_cond_broadcast(&q.changed);pthread_mutex_unlock(&q.lock);
        if(rc)break;
    }
    pthread_mutex_lock(&q.lock);q.cancel=1;pthread_cond_broadcast(&q.changed);pthread_mutex_unlock(&q.lock);
    shutdown(fd,SHUT_RDWR);pthread_join(reader,NULL);
cleanup:
    for(int i=0;i<MAX_SLOTS;i++){cache_release(q.slots[i].cache_index);free(q.slots[i].private_data);}
    pthread_cond_destroy(&q.changed);pthread_mutex_destroy(&q.lock);
}
