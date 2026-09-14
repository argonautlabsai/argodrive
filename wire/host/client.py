"""ctypes adapter for the native caller-owned-buffer S1 client."""
import ctypes
import json


class Client:
    def __init__(self, library, address, port, identity, secret):
        self.lib=ctypes.CDLL(str(library))
        self.lib.awr_connect.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_char_p]
        self.lib.awr_connect.restype=ctypes.c_int
        self.lib.awr_get.argtypes=[ctypes.c_int,ctypes.c_uint64,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_size_t]
        self.lib.awr_get.restype=ctypes.c_int
        self.lib.awr_get_batch.argtypes=[ctypes.c_int,ctypes.c_uint64,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_size_t),ctypes.c_size_t,ctypes.c_size_t]
        self.lib.awr_get_batch.restype=ctypes.c_int
        self.lib.awr_stats.argtypes=[ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_size_t]
        self.lib.awr_stats.restype=ctypes.c_int
        self.lib.awr_close.argtypes=[ctypes.c_int]
        self.lib.awr_close.restype=None
        self.fd=self.lib.awr_connect(address.encode(),port,identity.encode(),str(secret).encode())
        self.seq=0
        if self.fd<0:raise ConnectionError('Wire handshake failed; check identity, secret and endpoint')

    def get_into(self, layer, expert, destination, length):
        if self.fd<0:raise ConnectionError('Wire connection is closed')
        if length <= 0 or length > ctypes.sizeof(destination) or not all(type(x) is int and 0 <= x <= 0xffffffff for x in (layer, expert)):
            raise ValueError('Invalid destination length or record ID')
        self.seq+=1
        rc=self.lib.awr_get(self.fd,self.seq,layer,expert,destination,length)
        if rc==-2:raise LookupError('Node rejected the read; destination is not valid')
        if rc:
            self.close()
            raise ConnectionError('Wire read failed; connection closed before destination reuse')

    def get_many(self, requests, window=4):
        """Receive 1–64 (layer, expert, buffer, length) records into final buffers.

        On any failure all destinations must be discarded. The connection is
        closed before returning; no worker can write after this method returns.
        """
        if self.fd<0:raise ConnectionError('Wire connection is closed')
        if not 1<=len(requests)<=64 or type(window) is not int or not 1<=window<=8:
            raise ValueError('Batch requires 1–64 records and a window of 1–8')
        if self.seq+len(requests)>0xffffffffffffffff:raise ValueError('Request sequence exhausted')
        ranges=[]
        for layer,expert,buffer,length in requests:
            if type(length) is not int or not 1<=length<=min(ctypes.sizeof(buffer),64*1024*1024) or not all(type(v) is int and 0<=v<=0xffffffff for v in (layer,expert)):
                raise ValueError('Invalid record or destination')
            start=ctypes.addressof(buffer);end=start+length
            if any(start<b and a<end for a,b in ranges):raise ValueError('Batch destinations overlap')
            ranges.append((start,end))
        n=len(requests);layers=(ctypes.c_uint32*n)(*(r[0] for r in requests));experts=(ctypes.c_uint32*n)(*(r[1] for r in requests))
        buffers=(ctypes.c_void_p*n)(*(ctypes.addressof(r[2]) for r in requests));lengths=(ctypes.c_size_t*n)(*(r[3] for r in requests))
        first=self.seq+1;self.seq+=n
        rc=self.lib.awr_get_batch(self.fd,first,layers,experts,buffers,lengths,n,window)
        if rc:
            self.close()
            if rc==-2:raise LookupError('Node rejected a queued read; batch invalid, connection closed')
            raise ConnectionError('Queued read failed; batch invalid, connection closed')

    def stats(self):
        if self.fd<0:raise ConnectionError('Wire connection is closed')
        self.seq+=1;buffer=ctypes.create_string_buffer(4096)
        if self.lib.awr_stats(self.fd,self.seq,buffer,len(buffer))<0:
            self.close();raise ConnectionError('Statistics request failed')
        return json.loads(buffer.value)

    def close(self):
        if self.fd>=0:self.lib.awr_close(self.fd);self.fd=-1

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
