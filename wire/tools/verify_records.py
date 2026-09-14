#!/usr/bin/env python3
"""Check random records against trusted out-of-band SHA-256s and save evidence.

Transfer time excludes SHA-256 verification. Wall rate includes verification.
Neither metric is an engine decode result or an independently measured link cap.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
from client import Client


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',required=True)
    p.add_argument('--port',required=True,type=int)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--secret',required=True,type=Path)
    p.add_argument('--library',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--requests',type=int,default=1000)
    a=p.parse_args()
    manifest=json.loads(a.manifest.read_text())
    if a.requests<1 or not manifest['records']:p.error('Positive request count and nonempty index required')
    rng=random.Random(20260911)
    buffer=ctypes.create_string_buffer(max(r['length'] for r in manifest['records']))
    timings=[]; total=0; checked=0; visited=set(); failure=None
    start=time.monotonic()
    try:
        with Client(a.library,a.host,a.port,manifest['identity'],a.secret) as c:
            for _ in range(a.requests):
                r=rng.choice(manifest['records']); before=time.monotonic()
                c.get_into(r['layer'],r['expert'],buffer,r['length'])
                timings.append(time.monotonic()-before)
                digest=hashlib.sha256(memoryview(buffer).cast('B')[:r['length']]).hexdigest()
                if digest!=r['sha256']:raise ValueError('Record SHA-256 mismatch')
                checked+=1;total+=r['length'];visited.add((r['layer'],r['expert']))
    except Exception as e:
        failure=str(e)
    wall=time.monotonic()-start
    ordered=sorted(timings)
    report={'stage':'S1', 'created_at':datetime.now(timezone.utc).isoformat(), 'transport':'TCP',
            'endpoint':a.host, 'links':1, 'scope':'Existing K3 expert sample; no application cache; OS cache uncontrolled; sequential requests; SHA-256 checked via trusted SSH manifest',
            'identity':manifest['identity'],'seed':20260911,'requested':a.requests,'verified':checked,
            'unique_verified':len(visited),'indexed':len(manifest['records']),'bytes':total,
            'transfer_seconds':sum(timings),'wall_seconds':wall,
            'transfer_gbs':total/sum(timings)/1e9 if timings else None,'wall_gbs':total/wall/1e9,
            'p50_ms':ordered[len(ordered)//2]*1000 if ordered else None,
            'p90_ms':ordered[min(len(ordered)-1,int(len(ordered)*.9))]*1000 if ordered else None,
            'identity_pass':checked==a.requests and not failure,'failure':failure,
            'performance_qualified':False,'engine_integrated':False,'cold_ssd_qualified':False,
            'host_buffer':'Native recv into preallocated caller destination; no payload memcpy in this path; kernel/socket copies still occur'}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    return 1 if failure else 0


if __name__=='__main__':sys.exit(main())
