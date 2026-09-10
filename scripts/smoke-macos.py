#!/usr/bin/env python3
"""Exercise a frozen app's backend using only disposable data. No UI or sampler."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('app',type=Path)
    args=parser.parse_args()
    app=args.app.resolve()
    binary=app/'Contents/Resources/backend/argodrive-server'
    with tempfile.TemporaryDirectory(prefix='argodrive-package-test-') as tmp:
        root=Path(tmp).resolve(); state=root/'state'; state.mkdir()
        runs=root/'runs'; block=runs/'fixture';block.mkdir(parents=True)
        (block/'control.log').write_text('ARM control START 2026-09-10 10:00:00 tokens=11\nCONFIG model=fixture.gguf ctx=4096 raw=1 temp=0 think=nothink\nPROMPT [Packaging test fixture]\n'+json.dumps({'tag':'control','tokens_req':11,'decode_tok_s':2,'chunks':11,'gen_s':5,'first_byte_s':6,'rc':0,'valid':'ok'})+'\n')
        before=hashlib.sha256((block/'control.log').read_bytes()).hexdigest()
        env={**os.environ,'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','PYTHONHOME':'/nonexistent','PYTHONPATH':'/nonexistent'}
        ready=state/'ready.json'; log=(state/'test.log').open('w+')
        def start():
            ready.unlink(missing_ok=True)
            process=subprocess.Popen([str(binary),'--reports-only','--port','0','--state-dir',str(state),'--ready-file',str(ready),'--parent-pid',str(os.getpid())],cwd=root,env=env,stdout=log,stderr=log)
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and process.poll() is None and not ready.exists():time.sleep(.05)
            if not ready.exists():
                process.terminate();process.wait(timeout=8);log.seek(0);raise RuntimeError(log.read())
            data=json.loads(ready.read_text());assert data['pid']==process.pid
            return process,'http://127.0.0.1:'+str(data['port'])
        def get(base,path):
            with urllib.request.urlopen(base+path,timeout=10) as response:return json.load(response)
        p=None
        try:
            p,base=start()
            info=get(base,'/data');assert info['mode']=='reports' and info['cur_total'] is None and not info['health']['scope']
            settings=get(base,'/settings')
            assert settings['runs'].startswith(str(state))
            for path,typ in [('/','text/html'),('/app.js','text/javascript'),('/app-model.js','text/javascript'),('/app.css','text/css')]:
                with urllib.request.urlopen(base+path) as r:assert typ in r.headers['Content-Type'] and len(r.read())>100
            request=urllib.request.Request(base+'/settings',data=json.dumps({'runs':str(runs)}).encode(),headers={'Content-Type':'application/json','Origin':base,'X-Argodrive-Token':settings['token']},method='POST')
            with urllib.request.urlopen(request) as r:assert json.load(r)['applied']
            rows=get(base,'/stats')['blocks'][0]['rows'];assert len(rows)==1 and rows[0]['tok_s_steady']==2
            with urllib.request.urlopen(base+'/stats.csv') as r:assert b'control' in r.read()
            assert hashlib.sha256((block/'control.log').read_bytes()).hexdigest()==before
            p.terminate();p.wait(timeout=8)
            p,base=start()
            assert get(base,'/settings')['runs']==str(runs)
            assert get(base,'/stats')['blocks'][0]['rows'][0]['arm']=='control'
            p.terminate();p.wait(timeout=8);p=None
            # A backend whose parent identity is wrong must refuse to start.
            bad=subprocess.run([str(binary),'--reports-only','--port','0','--state-dir',str(state),'--parent-pid','1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,timeout=8)
            assert bad.returncode!=0 and b'launching app is no longer running' in bad.stdout
            print('PASS: frozen launch without external Python, assets, reports-only isolation, source selection, CSV export, source immutability, persisted restart, parent identity refusal, clean termination.')
        finally:
            if p and p.poll() is None:p.terminate();p.wait(timeout=8)
            log.close()

if __name__=='__main__':main()
