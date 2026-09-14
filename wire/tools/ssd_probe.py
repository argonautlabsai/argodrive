#!/usr/bin/env python3
"""Read-only F_NOCACHE probe of indexed files; never changes network or memory limits."""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time


def probe(paths, streams, duration):
    def device_bytes():
        raw=subprocess.check_output(['ioreg','-r','-c','IOBlockStorageDriver','-l'],text=True)
        values=re.findall(r'"Bytes \(Read\)"=(\d+)',raw)
        if not values:raise ValueError('Physical read counters unavailable')
        return sum(map(int,values))
    physical_before=device_bytes()
    end=time.monotonic()+duration
    def reader(i):
        total=0; reads=0
        while time.monotonic()<end:
            path=paths[i%len(paths)];i+=streams
            fd=os.open(path,os.O_RDONLY)
            try:
                fcntl.fcntl(fd,48,1) # Darwin F_NOCACHE, fail if unavailable.
                offset=0
                while time.monotonic()<end:
                    data=os.pread(fd,4*1024**2,offset)
                    if not data:break
                    total+=len(data);reads+=1;offset+=len(data)
            finally:os.close(fd)
        return total,reads
    start=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=streams) as pool:
        results=list(pool.map(reader,range(streams)))
    elapsed=time.monotonic()-start
    physical_bytes=device_bytes()-physical_before
    total=sum(r[0] for r in results)
    return {'streams':streams,'bytes':total,'seconds':elapsed,'application_gbs':total/elapsed/1e9,
            'device_read_bytes':physical_bytes,'device_read_gbs':physical_bytes/elapsed/1e9,
            'device_to_application_ratio':physical_bytes/total if total else None,
            'cold_qualified':False,'reads':sum(r[1] for r in results)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('index',type=Path);p.add_argument('--seconds',type=float,default=5)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if not 1<=a.seconds<=30:p.error('Probe duration must be 1–30 seconds')
    paths=sorted({line.rstrip('\n').split('\t')[4] for line in a.index.read_text().splitlines()[1:]})
    report={'created_at':datetime.now(timezone.utc).isoformat(),'method':'os.pread, 4 MiB chunks, F_NOCACHE=1, existing indexed files, 5 s per arm',
            'scope':'Device bars use whole-machine IOKit read deltas, including background activity. Cached application throughput is reported separately. Cold SSD ceiling remains unqualified.',
            'results':[probe(paths,n,a.seconds) for n in (1,2,3)]}
    a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
