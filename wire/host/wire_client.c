/* S1 caller-owned destination. No payload memcpy and no retries writing behind
 * the caller. On transport/protocol failure the connection is unusable: close it
 * before local fallback reuses the destination. One request at a time. */
#include "../protocol.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <signal.h>

int awr_connect(const char *ip,int port,const char *identity_hex,const char *secret_path){
    if(port<1||port>65535)return -1;
    unsigned char hello[72],reply[24];awr_header h={AWR_HELLO,0,0,AWR_WHOLE,72};
    awr_put32(hello,AWR_VERSION);awr_put32(hello+4,1);
    if(awr_hex(identity_hex,hello+8))return -1;
    int s=open(secret_path,O_RDONLY);struct stat st;
    if(s<0)return -1;
    int valid=!fstat(s,&st)&&S_ISREG(st.st_mode)&&!(st.st_mode&077)&&st.st_size==32&&read(s,hello+40,32)==32;close(s);
    if(!valid)return -1;
    int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0)return -1;awr_socket_options(fd);
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons((uint16_t)port)};
    if(inet_pton(AF_INET,ip,&addr.sin_addr)!=1||connect(fd,(struct sockaddr*)&addr,sizeof(addr))||
       awr_write_header(fd,&h)||awr_io(fd,hello,sizeof(hello),1)||awr_read_header(fd,&h)||
       h.type!=AWR_HELLO_OK||h.length!=24||h.layer||h.expert||h.piece!=AWR_WHOLE||awr_io(fd,reply,24,0)||
       awr_u32(reply)!=AWR_VERSION||awr_u32(reply+4)!=1){close(fd);return -1;}
    return fd;
}
static int send_get(int fd,uint64_t seq,uint32_t layer,uint32_t expert){
    unsigned char id[8];awr_put64(id,seq);awr_header h={AWR_GET,layer,expert,0,8};
    return awr_write_header(fd,&h)||awr_io(fd,id,8,1);
}
static int receive_get(int fd,uint64_t seq,uint32_t layer,uint32_t expert,void *destination,size_t length){
    unsigned char id[8];awr_header h;
    if(awr_read_header(fd,&h)||
       h.layer!=layer||h.expert!=expert||h.piece!=0||h.length<8||awr_io(fd,id,8,0)||awr_u64(id)!=seq)return -1;
    if(h.type==AWR_NAK){unsigned char reason[160];if(h.length-8>sizeof(reason)||awr_io(fd,reason,h.length-8,0))return -1;return -2;}
    if(h.type!=AWR_DATA||h.length-8!=length)return -1;
    return awr_io(fd,destination,length,0);
}
int awr_get(int fd,uint64_t seq,uint32_t layer,uint32_t expert,void *destination,size_t length){
    if(!seq||!destination||!length||length>AWR_MAX_RECORD||send_get(fd,seq,layer,expert))return -1;
    return receive_get(fd,seq,layer,expert,destination,length);
}
/* The destination array belongs to the caller and stays exclusive until return.
 * No background writes. Any batch error invalidates the batch and connection. */
int awr_get_batch(int fd,uint64_t first,const uint32_t *layers,const uint32_t *experts,
                  void **destinations,const size_t *lengths,size_t count,size_t window){
    if(!first||!count||count>64||!window||window>8||first>UINT64_MAX-count+1||
       !layers||!experts||!destinations||!lengths)return -1;
    for(size_t i=0;i<count;i++){
        uintptr_t base=(uintptr_t)destinations[i];
        if(!base||!lengths[i]||lengths[i]>AWR_MAX_RECORD||base>UINTPTR_MAX-lengths[i])return -1;
        for(size_t j=0;j<i;j++){
            uintptr_t other=(uintptr_t)destinations[j];
            if(base<other+lengths[j]&&other<base+lengths[i])return -1;
        }
    }
    size_t sent=0;
    for(;sent<count&&sent<window;sent++)if(send_get(fd,first+sent,layers[sent],experts[sent]))return -1;
    for(size_t received=0;received<count;received++){
        int rc=receive_get(fd,first+received,layers[received],experts[received],destinations[received],lengths[received]);
        if(rc)return rc;
        if(sent<count){if(send_get(fd,first+sent,layers[sent],experts[sent]))return -1;sent++;}
    }
    return 0;
}
int awr_stats(int fd,uint64_t seq,char *destination,size_t capacity){
    if(!seq||!destination||capacity<2)return -1;
    unsigned char id[8];awr_put64(id,seq);awr_header h={AWR_STAT,0,0,0,8};
    if(awr_write_header(fd,&h)||awr_io(fd,id,8,1)||awr_read_header(fd,&h)||
       h.type!=AWR_STAT||h.layer||h.expert||h.piece||h.length<8||h.length-8>=capacity||
       awr_io(fd,id,8,0)||awr_u64(id)!=seq||awr_io(fd,destination,h.length-8,0))return -1;
    destination[h.length-8]=0;return (int)h.length-8;
}
void awr_close(int fd){shutdown(fd,SHUT_RDWR);close(fd);}
