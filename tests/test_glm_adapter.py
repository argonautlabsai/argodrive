"""Exercise engine adapter success and local overwrite after real wire failures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import struct
import threading
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'wire/tools'))
from index_records import write_index

PROBE=r'''
import ctypes,os,sys,json,time
lib=ctypes.CDLL(sys.argv[1]);lib.argodrive_glm_read.argtypes=[ctypes.c_int,ctypes.c_uint64,ctypes.c_uint64,ctypes.c_void_p]
fd=os.open(sys.argv[2],os.O_RDONLY); n=int(sys.argv[3]); buf=ctypes.create_string_buffer(b'X'*(n+2),n+2)
rc=lib.argodrive_glm_read(fd,0,n,buf)
if not rc:
    data=os.pread(fd,n,0); ctypes.memmove(buf,data,len(data))
before=buf.raw;time.sleep(.08)
assert before==buf.raw,'writer continued after fallback'
assert buf.raw[-2:]==b'XX','destination guard overwritten'
assert buf.raw[:n]==os.pread(fd,n,0),'final data differs from local model'
print(json.dumps({'remote':rc}))
'''


class GLMAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(['make','-C',str(ROOT/'wire')],check=True,capture_output=True)
        cls.lib=ROOT/'.build/wire/libglm-adapter.dylib'
        subprocess.run(['clang','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-pthread','-dynamiclib',
                        str(ROOT/'wire/host/glm_adapter.c'),str(ROOT/'wire/host/wire_client.c'),'-o',str(cls.lib)],check=True)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Path(self.tmp.name)
        self.model=self.p/'model';self.model.write_bytes(bytes(range(256))*4096)
        self.source=self.p/'remote';self.source.write_bytes(self.model.read_bytes())
        self.identity=write_index([{'layer':3,'expert':6,'spans':[{'path':str(self.source),'offset':0,'length':self.source.stat().st_size}]}],self.p)['identity']
        self.secret=self.p/'secret';self.secret.write_bytes(os.urandom(32));self.secret.chmod(0o600)
        self.proc=subprocess.Popen([str(ROOT/'.build/wire/argodrive-node'),'127.0.0.1','0',str(self.p/'records.index'),str(self.secret),'--read-ahead','2'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop);self.port=json.loads(self.proc.stdout.readline())['port']
        s=self.model.stat();self.map=self.p/'engine.map'
        self.map.write_text(f'AWRDS41 {s.st_size} {s.st_dev} {s.st_ino} {s.st_mtime_ns//10**9} {s.st_mtime_ns%10**9} {self.identity}\n0 {s.st_size} 3 6 {hashlib.sha256(self.model.read_bytes()).hexdigest()}\n')

    def stop(self):
        self.proc.terminate();self.proc.communicate(timeout=10)

    def probe(self,expected,model=None,length=None,map_on=True):
        env={**os.environ,'DS4_ARGODRIVE_MAP':str(self.map) if map_on else '',
             'DS4_ARGODRIVE_ENDPOINTS':f'127.0.0.1:{self.port},127.0.0.1:{self.port}','DS4_ARGODRIVE_SECRET':str(self.secret)}
        r=subprocess.run([sys.executable,'-c',PROBE,str(self.lib),str(model or self.model),str(length or self.model.stat().st_size)],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['remote'],expected,r.stderr)
        return r.stderr

    def test_exact_remote_component_and_guard_bytes(self): self.probe(1)
    def test_disabled_adapter_uses_local(self): self.probe(0,map_on=False)
    def test_split_range_is_local(self): self.probe(0,length=12345)
    def test_wrong_model_inode_is_rejected(self):
        other=self.p/'other';other.write_bytes(self.model.read_bytes());self.assertIn('model_binding=rejected',self.probe(0,model=other))
    def test_corrupted_remote_payload_is_overwritten_locally(self):
        self.source.write_bytes(b'Z'*self.source.stat().st_size)
        self.assertIn('failed_reads=1',self.probe(0))
    def test_real_source_short_read_is_overwritten_locally(self):
        self.source.write_bytes(self.source.read_bytes()[:100])
        self.assertIn('failed_reads=1',self.probe(0))
    def test_half_network_payload_is_overwritten_before_return(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(4)
        upstream_port=self.port;self.port=listener.getsockname()[1];threads=[]
        def exact(s,n):
            data=b''
            while len(data)<n:
                chunk=s.recv(n-len(data))
                if not chunk:raise EOFError()
                data+=chunk
            return data
        def serve(peer):
            try:
                with peer, socket.create_connection(('127.0.0.1',upstream_port),timeout=2) as up:
                    peer.settimeout(2)
                    up.sendall(exact(peer,96));peer.sendall(exact(up,48))
                    request=exact(peer,32);up.sendall(request)
                    header=exact(up,24);length=struct.unpack_from('<I',header,20)[0]
                    peer.sendall(header+exact(up,8+(length-8)//2))
                    # Closing after a genuine half DATA frame exercises the
                    # partial destination, not just a before-payload NAK.
            except (OSError,EOFError):pass
        def accept():
            for _ in range(4):
                peer,_=listener.accept();t=threading.Thread(target=serve,args=(peer,));t.start();threads.append(t)
        accepting=threading.Thread(target=accept);accepting.start()
        try:self.assertIn('failed_reads=1',self.probe(0))
        finally:
            accepting.join(timeout=3);listener.close()
            for t in threads:t.join(timeout=3)
    def test_bad_map_falls_back(self):
        self.map.write_text(self.map.read_text()+'garbage\n');self.probe(0)


if __name__=='__main__':unittest.main()
