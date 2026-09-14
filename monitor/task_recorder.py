"""Shared task records for diagnostic/build/wait runners. Never invent token rates."""
from datetime import datetime, timezone
import json
import os
import plistlib
from pathlib import Path
import re
import subprocess

KINDS = {'correctness', 'build', 'waiting', 'diagnostic'}
STATES = {'running', 'completed', 'failed', 'interrupted', 'unavailable'}

class TaskRecorder:
    def __init__(self, directory, label, kind='diagnostic', source=None):
        if kind not in KINDS or not isinstance(label,str) or len(label)>512:
            raise ValueError('Invalid task label/type')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.base=self.directory/'activity';self.sampler=None;self.sampler_log=None
        self.record={'schema':1,'label':label,'kind':kind,'status':'running',
                     'started_at':datetime.now(timezone.utc).isoformat(),
                     'source':str(source) if source else None,'sampling':'Not started',
                     'note':'Task status only; token throughput is not measured.'}
        if self.base.with_suffix('.task.json').exists():
            self.record=json.loads(self.base.with_suffix('.task.json').read_text())
        else:
            self.base.with_suffix('.log').write_text('ARM activity START '+self.record['started_at']+'\nARGODRIVE_TASK '+json.dumps({'schema':1,'kind':kind})+'\n')
            self.update()
    def update(self, **fields):
        if 'status' in fields and fields['status'] not in STATES:raise ValueError('Invalid task state')
        self.record.update(fields);self.record['updated_at']=datetime.now(timezone.utc).isoformat()
        p=self.base.with_suffix('.task.json');tmp=p.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self.record,indent=2)+'\n');os.replace(tmp,p)
    def start_sampler(self,binary,devices,seconds=1800):
        if self.sampler is not None:return
        if not devices or len(set(devices.values()))!=len(devices) or any(not re.fullmatch(r'disk\d+',v) or not re.fullmatch(r'[\w-]+',k) for k,v in devices.items()):raise ValueError('Physical device map required')
        if self.base.with_suffix('.csv').exists():
            self.update(sampling='Previous recorded window retained; collector was not restarted')
            return
        self.base.with_suffix('.map').write_text(' '.join(k+'='+v for k,v in devices.items())+'\n')
        self.sampler_log=self.base.with_suffix('.sampler.log').open('w')
        try:
            self.sampler=subprocess.Popen([str(binary),'100',str(seconds),str(self.base.with_suffix('.csv')),*devices.values()],stdout=self.sampler_log,stderr=subprocess.STDOUT)
            self.update(sampling='100 ms physical-device counters; includes other device activity',sample_interval_ms=100)
        except BaseException:
            self.sampler_log.close();self.sampler_log=None;raise
    def close(self):
        if self.sampler is not None:
            if self.sampler.poll() is None:
                self.sampler.terminate()
                try:self.sampler.wait(timeout=3)
                except subprocess.TimeoutExpired:self.sampler.kill();self.sampler.wait()
            self.sampler=None
        if self.sampler_log:self.sampler_log.close();self.sampler_log=None
    def finish(self,status,detail=None):
        if status=='running':raise ValueError('Terminal task status required')
        self.close();self.update(status=status,detail=detail,finished_at=datetime.now(timezone.utc).isoformat())


def physical_devices(volumes):
    top=plistlib.loads(subprocess.check_output(['/usr/sbin/diskutil','apfs','list','-plist'],timeout=10))
    result={}
    for label,name in volumes.items():
        found=[c for c in top['Containers'] if any(v.get('Name')==name for v in c.get('Volumes',[]))]
        if len(found)!=1 or len(found[0].get('PhysicalStores',[]))!=1:raise ValueError('Ambiguous/missing volume '+name)
        m=re.fullmatch(r'(disk\d+)s\d+',found[0]['PhysicalStores'][0]['DeviceIdentifier'])
        if not m:raise ValueError('Physical store unavailable')
        result[label]=m[1]
    if len(set(result.values()))!=len(result):raise ValueError('Volumes share a physical store')
    return result
