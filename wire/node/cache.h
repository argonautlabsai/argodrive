/* One process-wide cache, shared by every listener and connection.
 * Loading and referenced entries cannot be evicted. Failed reads never publish.
 * The configured bound includes entries being filled; slots are separate. */
typedef struct {
    unsigned char *data;
    uint32_t refs;
    int loading, prev, next;
} cache_entry;
static cache_entry *cache;
static uint64_t cache_limit, cache_used, cache_peak;
static int cache_head=-1, cache_tail=-1;
static pthread_mutex_t cache_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cache_changed=PTHREAD_COND_INITIALIZER;
static atomic_uint_fast64_t cache_hits,cache_misses,cache_waits,cache_evictions,cache_bypasses,cache_hit_bytes;
static atomic_uint_fast64_t read_bytes,read_calls,read_ns,send_ns;

static void unlink_entry(int i){
    cache_entry *e=cache+i;
    if(e->prev>=0)cache[e->prev].next=e->next;else cache_head=e->next;
    if(e->next>=0)cache[e->next].prev=e->prev;else cache_tail=e->prev;
    e->prev=e->next=-1;
}
static void touch_entry(int i){
    cache_entry *e=cache+i;
    if(cache_head==i)return;
    if(e->prev>=0||e->next>=0||cache_tail==i)unlink_entry(i);
    e->prev=-1;e->next=cache_head;
    if(cache_head>=0)cache[cache_head].prev=i;else cache_tail=i;
    cache_head=i;
}
static void cache_release(int i){
    if(i<0)return;
    pthread_mutex_lock(&cache_lock);
    if(cache[i].refs)cache[i].refs--;
    pthread_cond_broadcast(&cache_changed);
    pthread_mutex_unlock(&cache_lock);
}

/* Return a referenced cache entry, or -1 to use the caller's private slot.
 * *fill is true only for the thread responsible for loading this entry. */
static int cache_acquire(record *r,int *fill){
    *fill=0;
    if(!cache_limit){atomic_fetch_add(&cache_misses,1);return -1;}
    int i=(int)(r-records);
    pthread_mutex_lock(&cache_lock);
    cache_entry *e=cache+i;
    if(e->data&&!e->loading){
        e->refs++;touch_entry(i);atomic_fetch_add(&cache_hits,1);
        atomic_fetch_add(&cache_hit_bytes,r->length);
        pthread_mutex_unlock(&cache_lock);return i;
    }
    atomic_fetch_add(&cache_misses,1);
    if(e->loading){
        atomic_fetch_add(&cache_waits,1);
        while(e->loading)pthread_cond_wait(&cache_changed,&cache_lock);
        if(e->data){e->refs++;touch_entry(i);pthread_mutex_unlock(&cache_lock);return i;}
    }
    if(r->length<=cache_limit){
        while(cache_used+r->length>cache_limit){
            int victim=cache_tail;
            while(victim>=0&&(cache[victim].refs||cache[victim].loading))victim=cache[victim].prev;
            if(victim<0)break;
            unlink_entry(victim);free(cache[victim].data);cache[victim].data=NULL;
            cache_used-=records[victim].length;atomic_fetch_add(&cache_evictions,1);
        }
        if(cache_used+r->length<=cache_limit){
            unsigned char *p=malloc(r->length);
            if(p){
                e->data=p;e->refs=1;e->loading=1;touch_entry(i);cache_used+=r->length;
                if(cache_used>cache_peak)cache_peak=cache_used;
                *fill=1;pthread_mutex_unlock(&cache_lock);return i;
            }
        }
    }
    atomic_fetch_add(&cache_bypasses,1);
    pthread_mutex_unlock(&cache_lock);return -1;
}
static void cache_publish(int i,int success){
    pthread_mutex_lock(&cache_lock);
    cache_entry *e=cache+i;
    if(!success){
        unlink_entry(i);free(e->data);e->data=NULL;e->refs=0;
        cache_used-=records[i].length;
    }
    e->loading=0;pthread_cond_broadcast(&cache_changed);
    pthread_mutex_unlock(&cache_lock);
}
static int stats_json(char *buf,size_t capacity){
    pthread_mutex_lock(&cache_lock);
    uint64_t used=cache_used,peak=cache_peak,refs=0,loading=0;
    if(cache)for(size_t i=0;i<count;i++){refs+=cache[i].refs;loading+=(cache[i].loading!=0);}
    pthread_mutex_unlock(&cache_lock);
    return snprintf(buf,capacity,
        "{\"gets\":%llu,\"bytes_sent\":%llu,\"errors\":%llu,"
        "\"cache_limit_bytes\":%llu,\"cache_used_bytes\":%llu,\"cache_peak_bytes\":%llu,"
        "\"cache_hits\":%llu,\"cache_misses\":%llu,\"cache_waits\":%llu,"
        "\"cache_evictions\":%llu,\"cache_bypasses\":%llu,\"cache_hit_bytes\":%llu,"
        "\"cache_refs\":%llu,\"cache_loading\":%llu,"
        "\"read_bytes\":%llu,\"read_calls\":%llu,\"read_seconds_gross\":%.6f,\"send_seconds_gross\":%.6f}",
        (unsigned long long)atomic_load(&get_count),(unsigned long long)atomic_load(&bytes_sent),
        (unsigned long long)atomic_load(&errors),(unsigned long long)cache_limit,
        (unsigned long long)used,(unsigned long long)peak,
        (unsigned long long)atomic_load(&cache_hits),(unsigned long long)atomic_load(&cache_misses),
        (unsigned long long)atomic_load(&cache_waits),(unsigned long long)atomic_load(&cache_evictions),
        (unsigned long long)atomic_load(&cache_bypasses),(unsigned long long)atomic_load(&cache_hit_bytes),
        (unsigned long long)refs,(unsigned long long)loading,
        (unsigned long long)atomic_load(&read_bytes),(unsigned long long)atomic_load(&read_calls),
        atomic_load(&read_ns)/1e9,atomic_load(&send_ns)/1e9);
}
