"""Bounded whole-record TCP transport. The engine can target this interface.

An RDMA backend can implement read_many/close with the same ownership contract.
RDMA is not implemented or auto-enabled here. No retries or local fallback yet.
"""
from concurrent.futures import ThreadPoolExecutor, wait
import ctypes
import threading
from typing import Protocol
from client import Client


class ExpertTransport(Protocol):
    def read_many(self, requests): ...
    def close(self): ...


class TCPTransport:
    def __init__(self, library, endpoints, identity, secret, *, connections_per_link=1, window=4):
        if not 1<=len(endpoints)<=3 or connections_per_link not in (1,2):
            raise ValueError('Use 1–3 endpoints and 1–2 connections per link')
        total=len(endpoints)*connections_per_link
        if type(window) is not int or not 1<=window<=8 or total*window>32:
            raise ValueError('At most 32 requests may be in flight')
        self.window=window;self.lock=threading.Lock();self.clients=[];self.executor=None;self.closed=False
        try:
            for address,port in endpoints:
                for _ in range(connections_per_link):self.clients.append(Client(library,address,port,identity,secret))
            self.executor=ThreadPoolExecutor(max_workers=total,thread_name_prefix='wire-link')
        except Exception:
            self.close();raise

    def read_many(self, requests):
        """Balance records by bytes; return only after every destination is idle.

        The caller owns buffers and must discard the entire batch on failure.
        Calls serialize so a connection is never driven by two workers at once.
        """
        with self.lock:
            if self.closed:raise ConnectionError('Transport is closed')
            if not requests or len(requests)>64:raise ValueError('Use a bounded batch of 1–64 records')
            # Check cross-connection overlap before any connection starts writing.
            ranges=[]
            for _,_,buffer,length in requests:
                if type(length) is not int or not 0<length<=ctypes.sizeof(buffer):raise ValueError('Invalid destination length')
                start=ctypes.addressof(buffer);end=start+length
                if any(start<b and a<end for a,b in ranges):raise ValueError('Batch destinations overlap')
                ranges.append((start,end))
            groups=[[] for _ in self.clients];loads=[0]*len(groups)
            for request in requests:
                index=min(range(len(groups)),key=lambda i:loads[i]);groups[index].append(request);loads[index]+=request[3]
            futures=[self.executor.submit(c.get_many,group,self.window) for c,group in zip(self.clients,groups) if group]
            wait(futures)  # A failed peer never returns while another writer is active.
            for future in futures:future.result()

    def stats(self):
        with self.lock:
            if self.closed:raise ConnectionError('Transport is closed')
            return self.clients[0].stats()

    def close(self):
        with self.lock:
            self.closed=True
            if self.executor:self.executor.shutdown(wait=True)
            for c in self.clients:c.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
