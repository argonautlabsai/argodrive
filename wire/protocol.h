#ifndef ARGODRIVE_WIRE_PROTOCOL_H
#define ARGODRIVE_WIRE_PROTOCOL_H
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <string.h>
#define AWR_VERSION 2
#define AWR_MAX_RECORD (64u * 1024u * 1024u)
#define AWR_WHOLE UINT32_MAX
enum { AWR_HELLO=1, AWR_HELLO_OK=2, AWR_GET=3, AWR_DATA=4,
       AWR_HINT=5, AWR_CANCEL=6, AWR_STAT=7, AWR_NAK=8 };
typedef struct { uint32_t type, layer, expert, piece, length; } awr_header;
static inline uint32_t awr_u32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static inline uint64_t awr_u64(const unsigned char *p) { return awr_u32(p) | (uint64_t)awr_u32(p+4)<<32; }
static inline void awr_put32(unsigned char *p, uint32_t n) { for(int i=0;i<4;i++)p[i]=(unsigned char)(n>>(i*8)); }
static inline void awr_put64(unsigned char *p, uint64_t n) { awr_put32(p,(uint32_t)n); awr_put32(p+4,(uint32_t)(n>>32)); }
static inline int awr_io(int fd, void *buf, size_t length, int writing) {
    unsigned char *p=buf;
    while(length) {
        ssize_t n=writing?send(fd,p,length,0):recv(fd,p,length,0);
        if(n<0 && errno==EINTR)continue;
        if(n<=0)return -1;
        p+=n; length-=(size_t)n;
    }
    return 0;
}
static inline int awr_read_header(int fd, awr_header *h) {
    unsigned char p[24]; if(awr_io(fd,p,sizeof(p),0))return -1;
    if(memcmp(p,"AWR1",4)||p[5]||p[6]||p[7])return -1;
    h->type=p[4];h->layer=awr_u32(p+8);h->expert=awr_u32(p+12);
    h->piece=awr_u32(p+16);h->length=awr_u32(p+20);
    return h->length>AWR_MAX_RECORD+8?-1:0;
}
static inline int awr_write_header(int fd, const awr_header *h) {
    unsigned char p[24]={0};memcpy(p,"AWR1",4);p[4]=(unsigned char)h->type;
    awr_put32(p+8,h->layer);awr_put32(p+12,h->expert);awr_put32(p+16,h->piece);awr_put32(p+20,h->length);
    return awr_io(fd,p,sizeof(p),1);
}
static inline void awr_socket_options(int fd) {
    struct timeval timeout={10,0}; int one=1, bytes=4*1024*1024;
    setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&one,sizeof(one));
    setsockopt(fd,SOL_SOCKET,SO_RCVBUF,&bytes,sizeof(bytes));
    setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&bytes,sizeof(bytes));
#ifdef SO_NOSIGPIPE
    setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one));
#endif
}
static inline int awr_hex(const char *s, unsigned char *out) {
    if(strlen(s)!=64)return -1;
    for(int i=0;i<32;i++) { unsigned n=0;
        for(int j=0;j<2;j++) {char c=s[i*2+j];unsigned v;
            if(c>='0'&&c<='9')v=(unsigned)(c-'0');else if(c>='a'&&c<='f')v=(unsigned)(c-'a'+10);else return -1;
            n=n*16+v;
        } out[i]=(unsigned char)n;
    } return 0;
}
#endif
