"""Native transport contracts, including genuine partial source reads."""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
NODE=Path(os.environ.get('AWR_TEST_NODE',str(ROOT/'.build/wire/argodrive-node')))
sys.path.insert(0,str(ROOT/'wire'/'host'))
sys.path.insert(0,str(ROOT/'wire'/'tools'))
sys.path.insert(0,str(ROOT/'wire'/'sim'))
from client import Client
from transport import TCPTransport
from index_records import write_index
from wire_tier import LRU, replay


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-C',str(ROOT/'wire')],check=True,capture_output=True)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)
        self.source=self.p/'packed source.bin'
        self.data=random.Random(19).randbytes(30000)
        self.source.write_bytes(self.data)
        self.expected={}; records=[]
        for e in range(20):
            # Noncontiguous gate/up/down logical records, including odd sizes.
            spans=[{'path':str(self.source),'offset':e*200+i*5000,'length':99+i} for i in range(3)]
            records.append({'layer':3,'expert':e,'spans':spans})
            self.expected[e]=b''.join(self.data[s['offset']:s['offset']+s['length']] for s in spans)
        self.identity=write_index(records,self.p)['identity']
        self.secret=self.p/'secret';self.secret.write_bytes(os.urandom(32));self.secret.chmod(0o600)
        self.proc=subprocess.Popen([str(NODE),'127.0.0.1','0',str(self.p/'records.index'),str(self.secret)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop)
        ready=json.loads(self.proc.stdout.readline());self.port=ready['port']
        self.assertEqual(ready['cache_bytes'],0)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        _,err=self.proc.communicate(timeout=15)
        self.assertEqual(self.proc.returncode,0,err)

    def client(self, identity=None, secret=None):
        return Client(ROOT/'.build/wire/libargodrive-wire.dylib','127.0.0.1',self.port,identity or self.identity,secret or self.secret)

    def restart(self, *options, addresses='127.0.0.1'):
        self.stop()
        self.proc=subprocess.Popen([str(NODE),addresses,'0',str(self.p/'records.index'),str(self.secret),*options],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        ready=json.loads(self.proc.stdout.readline());self.port=ready['port'];return ready

    def test_queued_records_preserve_order_and_destination_guards(self):
        self.restart('--read-ahead','4')
        with self.client() as c:
            for window in (1,2,4,8):
                requests=[]
                for i in range(33):
                    b=ctypes.create_string_buffer(b'X'*302,302)
                    requests.append((3,i%20,b,300))
                c.get_many(requests,window=window)
                for _,expert,b,_ in requests:
                    self.assertEqual(b.raw,self.expected[expert]+b'XX')
            self.assertEqual(c.stats()['gets'],132)

    def test_one_cache_is_shared_across_connections(self):
        self.restart('--cache-mib','1','--read-ahead','2')
        with self.client() as a, self.client() as b:
            a.get_into(3,0,ctypes.create_string_buffer(300),300)
            target=ctypes.create_string_buffer(300);b.get_into(3,0,target,300)
            stats=b.stats();self.assertEqual(target.raw,self.expected[0])
            self.assertEqual(stats['cache_hits'],1);self.assertEqual(stats['cache_misses'],1)
            self.assertEqual(stats['read_bytes'],300);self.assertEqual(stats['cache_used_bytes'],300)

    def test_cache_short_read_never_publishes_and_batch_failure_closes(self):
        self.restart('--cache-mib','1','--read-ahead','4')
        self.source.write_bytes(self.data[:10050])
        with self.client() as c:
            requests=[(3,19,ctypes.create_string_buffer(300),300),(3,0,ctypes.create_string_buffer(300),300)]
            with self.assertRaises(LookupError):c.get_many(requests,window=2)
            self.assertEqual(c.fd,-1)
            self.assertEqual(requests[0][2].raw,b'\0'*300)
        self.source.write_bytes(self.data)
        with self.client() as c:
            b=ctypes.create_string_buffer(300);c.get_into(3,19,b,300)
            self.assertEqual(b.raw,self.expected[19]);s=c.stats()
            self.assertGreaterEqual(s['errors'],1)
            # The recovered record was a miss, not a previously published partial.
            self.assertEqual(s['cache_hits'],0)

    def test_lru_eviction_and_pinned_budget_with_large_records(self):
        self.stop();payload=bytes(range(256))*2048;self.source.write_bytes(payload*6)
        self.identity=write_index([{'layer':9,'expert':i,'spans':[{'path':str(self.source),'offset':i*len(payload),'length':len(payload)}]} for i in range(6)],self.p)['identity']
        self.restart('--cache-mib','1','--read-ahead','4')
        with self.client() as c:
            for expert in range(6):
                buf=ctypes.create_string_buffer(len(payload));c.get_into(9,expert,buf,len(payload));self.assertEqual(buf.raw,payload)
            stats=c.stats();self.assertGreaterEqual(stats['cache_evictions'],4)
            requests=[(9,i,ctypes.create_string_buffer(len(payload)),len(payload)) for i in range(6)]
            c.get_many(requests,window=8)
            for _,_,buf,_ in requests:self.assertEqual(buf.raw,payload)
            stats=c.stats();self.assertLessEqual(stats['cache_peak_bytes'],1024*1024)
        self.proc.terminate();out,_=self.proc.communicate(timeout=15)
        final=json.loads(out.splitlines()[-1])['stats']
        self.assertEqual(final['cache_refs'],0);self.assertEqual(final['cache_loading'],0)

    def test_transport_balances_connections_and_rejects_overlapping_buffers(self):
        self.restart('--read-ahead','2')
        with TCPTransport(ROOT/'.build/wire/libargodrive-wire.dylib',[('127.0.0.1',self.port)]*2,self.identity,self.secret,window=4) as t:
            requests=[(3,i,ctypes.create_string_buffer(300),300) for i in range(20)]
            t.read_many(requests)
            for _,expert,buf,_ in requests:self.assertEqual(buf.raw,self.expected[expert])
            with self.assertRaises(ValueError):t.read_many([requests[0],requests[0]])
            self.assertEqual(t.stats()['gets'],20)

    def test_failed_parallel_batch_finishes_other_writers_before_return(self):
        self.restart('--read-ahead','4')
        with TCPTransport(ROOT/'.build/wire/libargodrive-wire.dylib',[('127.0.0.1',self.port)]*2,self.identity,self.secret,window=4) as t:
            requests=[(3,99 if i==0 else i,ctypes.create_string_buffer(300),300) for i in range(8)]
            with self.assertRaises(LookupError):t.read_many(requests)
            # Worker on the healthy connection has completed its final request.
            self.assertEqual(requests[-1][2].raw,self.expected[7])
            self.assertEqual(t.clients[0].fd,-1)

    def test_concurrent_repeated_record_has_one_cached_payload(self):
        self.restart('--cache-mib','1','--read-ahead','4')
        with TCPTransport(ROOT/'.build/wire/libargodrive-wire.dylib',[('127.0.0.1',self.port)]*2,self.identity,self.secret,connections_per_link=2,window=4) as t:
            requests=[(3,0,ctypes.create_string_buffer(300),300) for _ in range(32)]
            t.read_many(requests)
            for _,_,buf,_ in requests:self.assertEqual(buf.raw,self.expected[0])
            s=t.stats();self.assertEqual(s['read_calls'],1)
            self.assertEqual(s['cache_used_bytes'],300)
            self.assertEqual(s['cache_hits']+s['cache_misses'],32)

    def test_1000_random_multispan_records_into_final_buffer(self):
        rng=random.Random(1);buffer=ctypes.create_string_buffer(302)
        with self.client() as c:
            for _ in range(1000):
                e=rng.randrange(20);buffer[300]=b'X';buffer[301]=b'Y'
                c.get_into(3,e,buffer,300)
                self.assertEqual(buffer.raw[:300],self.expected[e])
                self.assertEqual(buffer.raw[300:],b'XY')

    def test_auth_model_identity_and_reconnect(self):
        with self.assertRaises(ConnectionError):self.client(identity='0'*64)
        wrong=self.p/'wrong';wrong.write_bytes(b'x'*32);wrong.chmod(0o600)
        with self.assertRaises(ConnectionError):self.client(secret=wrong)
        for _ in range(2):
            with self.client() as c:
                buffer=ctypes.create_string_buffer(300);c.get_into(3,0,buffer,300)
                self.assertEqual(buffer.raw,self.expected[0])

    def test_short_read_nak_and_persistent_connection_recovers(self):
        with self.client() as c:
            buffer=ctypes.create_string_buffer(300)
            # Source truncation happens after the daemon validated and opened it.
            self.source.write_bytes(self.data[:10050])
            with self.assertRaises(LookupError):c.get_into(3,19,buffer,300)
            self.source.write_bytes(self.data)
            c.get_into(3,0,buffer,300)
            self.assertEqual(buffer.raw,self.expected[0])
            with self.assertRaises(LookupError):c.get_into(4,0,buffer,300)
            c.get_into(3,1,buffer,300)
            self.assertEqual(buffer.raw,self.expected[1])

    def test_size_mismatch_closes_before_destination_reuse(self):
        with self.client() as c:
            buffer=ctypes.create_string_buffer(b'X'*301,301)
            with self.assertRaises(ConnectionError):c.get_into(3,0,buffer,301)
            self.assertEqual(c.fd,-1)
            self.assertEqual(buffer.raw,b'X'*301)
        with self.client() as c:c.get_into(3,0,buffer,300)

    def test_malformed_headers_are_rejected_without_large_allocation(self):
        for magic,flags,length in [(b'BAD!',0,72),(b'AWR1',1,72),(b'AWR1',0,0xffffffff)]:
            with socket.create_connection(('127.0.0.1',self.port),timeout=3) as s:
                s.sendall(struct.pack('<4sBBHIIII',magic,1,flags,0,0,0,0xffffffff,length))
                self.assertEqual(s.recv(1),b'')
        with self.client() as c:c.get_into(3,0,ctypes.create_string_buffer(300),300)

    def test_fragmented_frames_and_duplicate_request_id(self):
        def read_exact(s,n):
            data=b''
            while len(data)<n:
                part=s.recv(n-len(data))
                if not part:raise EOFError()
                data+=part
            return data
        with socket.create_connection(('127.0.0.1',self.port),timeout=3) as s:
            hello=struct.pack('<II',2,1)+bytes.fromhex(self.identity)+self.secret.read_bytes()
            frame=struct.pack('<4sBBHIIII',b'AWR1',1,0,0,0,0,0xffffffff,len(hello))+hello
            for i in range(0,len(frame),3):s.sendall(frame[i:i+3])
            h=struct.unpack('<4sBBHIIII',read_exact(s,24));self.assertEqual(h[1],2)
            read_exact(s,h[-1])
            request=struct.pack('<4sBBHIIIIQ',b'AWR1',3,0,0,3,0,0,8,1)
            s.sendall(request);h=struct.unpack('<4sBBHIIII',read_exact(s,24))
            self.assertEqual(read_exact(s,h[-1])[8:],self.expected[0])
            s.sendall(request);self.assertEqual(s.recv(1),b'')

    def test_client_rejects_wrong_sequence_and_partial_network_payload(self):
        for wrong_sequence in (True,False):
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
            def fake_node():
                def read_exact(s,n):
                    data=b''
                    while len(data)<n:
                        part=s.recv(n-len(data))
                        if not part:raise EOFError()
                        data+=part
                    return data
                with listener:
                    s,_=listener.accept()
                    with s:
                        read_exact(s,96)
                        reply=struct.pack('<IIQQ',2,1,20,0)
                        s.sendall(struct.pack('<4sBBHIIII',b'AWR1',2,0,0,0,0,0xffffffff,len(reply))+reply)
                        read_exact(s,32)
                        # Either a stale reply ID, or a real network short payload.
                        s.sendall(struct.pack('<4sBBHIIIIQ',b'AWR1',4,0,0,3,0,0,308,2 if wrong_sequence else 1)+b'Z'*150)
            thread=threading.Thread(target=fake_node);thread.start()
            with Client(ROOT/'.build/wire/libargodrive-wire.dylib','127.0.0.1',listener.getsockname()[1],self.identity,self.secret) as c:
                buf=ctypes.create_string_buffer(b'X'*300,300)
                with self.assertRaises(ConnectionError):c.get_into(3,0,buf,300)
                self.assertEqual(c.fd,-1)
                if wrong_sequence:self.assertEqual(buf.raw,b'X'*300)
                else:self.assertEqual(buf.raw,b'Z'*150+b'X'*150)
            thread.join(timeout=3);self.assertFalse(thread.is_alive())

    def test_invalid_index_never_starts(self):
        (self.p/'bad.index').write_text('AWRINDEX2\t'+self.identity+'\n3\t0\t999999\t20\t'+str(self.source)+'\n')
        r=subprocess.run([str(NODE),'127.0.0.1','0',str(self.p/'bad.index'),str(self.secret)],capture_output=True,timeout=5)
        self.assertNotEqual(r.returncode,0)
        self.assertIn(b'Invalid record index',r.stderr)


class SimulationTests(unittest.TestCase):
    def test_lru_capacity_and_zero_slots(self):
        c=LRU(1);self.assertFalse(c.access('a'));self.assertTrue(c.access('a'))
        self.assertFalse(c.access('b'));self.assertFalse(c.access('a'))
        self.assertFalse(LRU(0).access('a'))

    def test_token_count_uses_distinct_steps_and_cold_node_conserves_bytes(self):
        rows=[(5,1,list(range(16))),(9,1,list(range(16)))]
        r=replay(rows,3,record_bytes=1000000,host_slots=0,local_gbs=.01,node_gbs=.1,latency_ms=0)
        self.assertEqual(r['tokens'],2)
        self.assertEqual(r['wire_bytes'],r['node_ssd_bytes'])
        self.assertGreaterEqual(r['io_seconds'],r['node_ssd_bytes']/(.1*1e9))


if __name__=='__main__':unittest.main()
