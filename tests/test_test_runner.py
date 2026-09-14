import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'monitor'))
from test_runner import TestRunner, validate_request, stop_group, worker_main

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.lock_patch=patch('test_runner.CAMPAIGN_LOCK',self.root/'campaign.lock');self.lock_patch.start();self.addCleanup(self.lock_patch.stop)
        self.engine=self.root/'ds4';self.engine.write_text('not an engine')
        self.model=self.root/'model.gguf';self.model.write_text('GGUF')
        self.profile={'id':'glm','label':'GLM','enabled':True,'engine':str(self.engine),'engine_sha256':'wrong',
                      'drives':[{'id':'internal','label':'Internal','path':str(self.model),'volume_uuid':'uuid','weight':2}]}
        (self.root/'test-profiles.json').write_text(json.dumps({'profiles':[self.profile]}))
        self.body={'model':'glm','drives':['internal'],'context':4096,'tokens':100}
    def tearDown(self):self.tmp.cleanup()
    def test_reject_invalid_selection(self):
        for key,value in [('drives',[]),('drives',['internal','internal']),('drives',['; rm -rf /']),('tokens',True),('tokens',999999),('context',0),('model','unknown')]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_request({**self.body,key:value},[self.profile])
    def test_no_shell_parameters(self):
        with self.assertRaises(ValueError):validate_request({**self.body,'command':'echo test'},[self.profile])
    def test_options_does_not_start_process(self):
        r=TestRunner(self.root,[])
        with patch('test_runner.subprocess.Popen') as popen:
            self.assertTrue(r.options()['models'][0]['ready']);popen.assert_not_called()
            self.assertEqual(r.options()['default_model'],'glm')

    def test_deepseek_is_preferred_when_ready(self):
        deep_engine=self.root/'ds4-deepseek';deep_engine.write_text('deepseek engine')
        deep_model=self.root/'deepseek.gguf';deep_model.write_text('GGUF')
        deep={
            'id':'deepseek41','label':'DeepSeek V4.1 Flash','enabled':True,
            'engine':str(deep_engine),'drives':[{'id':'internal','label':'Internal',
            'path':str(deep_model),'volume_uuid':'uuid','weight':2}],
        }
        (self.root/'test-profiles.json').write_text(json.dumps({'profiles':[self.profile,deep]}))
        r=TestRunner(self.root,[])
        self.assertEqual(r.options()['default_model'],'deepseek41')
        self.assertEqual(r.options()['models'][0]['id'],'deepseek41')

    def test_options_keeps_detected_but_unconfigured_drives_visible(self):
        r=TestRunner(self.root,[],inventory=lambda:[{'id':'internal','label':'Internal'},
                                                       {'id':'Green','label':'Green'},
                                                       {'id':'White','label':'White'}])
        drives=r.options()['models'][0]['drives']
        self.assertEqual([d['id'] for d in drives],['internal','Green','White'])
        self.assertFalse(drives[1]['ready'])
        self.assertEqual(drives[1]['reason'],'No matching model replica registered')
    def test_unconfigured_model_disabled(self):
        r=TestRunner(self.root,[]);self.assertFalse(next(m for m in r.options()['models'] if m['id']=='deepseek41')['ready'])
    def test_start_rejects_identity_drift(self):
        r=TestRunner(self.root,[])
        with patch('test_runner.disk_identity',return_value={'physical_device':'disk0','volume_uuid':'wrong','size':4}),patch('test_runner.subprocess.Popen') as popen:
            with self.assertRaises(ValueError):r.start(self.body,self.root)
            popen.assert_not_called()
    def test_stop_cannot_target_other_process(self):
        r=TestRunner(self.root,[]);r.state={'id':'owned','status':'running'}
        with self.assertRaises(ValueError):r.stop('unrelated')
    def test_worker_failure_records_no_launch(self):
        out=self.root/'arm';out.mkdir();r={'folder':str(out),'profile':self.profile,'selection':self.body,'drives':self.profile['drives'],'identities':[]}
        req=out/'test-request.json';req.write_text(json.dumps(r));(out/'test-status.json').write_text('{"status":"preflight"}')
        oldterm=signal.getsignal(signal.SIGTERM);oldint=signal.getsignal(signal.SIGINT)
        try:
            with patch('test_runner.guard_idle'),patch('test_runner.subprocess.Popen') as popen:
                self.assertEqual(worker_main(req),1);popen.assert_not_called()
            state=json.loads((out/'test-status.json').read_text());self.assertEqual(state['status'],'failed');self.assertIn('Engine binary changed',state['error'])
        finally:signal.signal(signal.SIGTERM,oldterm);signal.signal(signal.SIGINT,oldint)
    def test_owned_stop_end_to_end(self):
        fake=self.root/'fake.py'
        fake.write_text('''import sys,json,signal,time\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text());p=Path(r['folder'])/'test-status.json'\ns=json.loads(p.read_text());s['status']='running';p.write_text(json.dumps(s))\ndef stop(*a):\n s['status']='stopped';s['owned']=False;p.write_text(json.dumps(s));sys.exit(0)\nsignal.signal(signal.SIGTERM,stop)\nwhile True:time.sleep(.02)\n''')
        r=TestRunner(self.root,[sys.executable,str(fake)])
        with patch('test_runner.disk_identity',return_value={'physical_device':'disk0','volume_uuid':'uuid','size':4}),patch('test_runner.guard_idle'):
            result=r.start(self.body,self.root)
            try:
                deadline=time.time()+3
                while time.time()<deadline and r.status()['status']!='running':time.sleep(.02)
                self.assertEqual(r.status()['status'],'running')
                with self.assertRaises(ValueError):r.start(self.body,self.root)
                r.stop(result['id']);r.process.wait(timeout=3)
                self.assertEqual(r.status()['status'],'stopped');self.assertFalse(r.status()['owned'])
            finally:r.shutdown()
    def test_worker_passes_context_and_single_selected_drive_to_harness(self):
        import hashlib
        from test_runner import HARNESS_FILES
        project=self.root/'project'
        for rel in HARNESS_FILES:
            f=project/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('stub')
        (self.engine.parent/'metal').mkdir()
        harness=project/'tools/harness/glm-arm.sh'
        harness.write_text('''#!/bin/sh
# $(cd "$(dirname "$GLM_DS4")" && git rev-parse --short HEAD 2>/dev/null)
python3 - "$1" "$2" "$3" <<'FIXTURE'
import json,os,sys
from pathlib import Path
out=Path(sys.argv[1]);tag=sys.argv[2];n=int(sys.argv[3])
(out/'observed.json').write_text(json.dumps(dict(os.environ)))
(out/(tag+'.txt')).write_text('fixture output')
(out/(tag+'.err')).write_text(f'ds4: generation counts: generated={n} requested={n} decode_seconds=10.0\\nF_NOCACHE set on 1 model fd\\n')
(out/(tag+'.log')).write_text(json.dumps({'tag':tag,'valid':'ok','swap_growth_mb':0})+'\\n')
FIXTURE
''')
        profile={**self.profile,'project':str(project),'prompt':'fixed','environment':{'DS4_MODEL_REPLICAS':'must-be-removed'},'engine_sha256':hashlib.sha256(self.engine.read_bytes()).hexdigest()}
        profile['harness_sha256']={rel:hashlib.sha256((project/rel).read_bytes()).hexdigest() for rel in HARNESS_FILES}
        profile['drives']=[{**self.profile['drives'][0],'id':'Green','label':'Green','weight':1}]
        out=self.root/'arm';out.mkdir();identity={'path':str(self.model),'physical_device':'disk2','volume_uuid':'uuid','size':4}
        req=out/'test-request.json';req.write_text(json.dumps({'folder':str(out),'profile':profile,'selection':{**self.body,'drives':['Green'],'context':8192},'drives':profile['drives'],'identities':[identity]}))
        (out/'test-status.json').write_text('{"status":"preflight"}')
        oldterm=signal.getsignal(signal.SIGTERM);oldint=signal.getsignal(signal.SIGINT)
        try:
            with patch('test_runner.guard_idle'),patch('test_runner.disk_identity',return_value=identity):
                self.assertEqual(worker_main(req),0,json.loads((out/'test-status.json').read_text()))
            observed=json.loads((out/'observed.json').read_text())
            self.assertEqual(observed['GLM_CTX'],'8192');self.assertEqual(observed['GLM_CACHE'],'auto')
            self.assertEqual(observed['GLM_MODEL'],str(self.model));self.assertNotIn('DS4_MODEL_REPLICAS',observed)
            self.assertEqual(observed['DS4_MODEL_PRIMARY_WEIGHT'],'1');self.assertEqual(observed['DS4_MODEL_REPLICA_PIECES_FD'],'1')
        finally:signal.signal(signal.SIGTERM,oldterm);signal.signal(signal.SIGINT,oldint)
    def test_group_cleanup_leaves_unrelated_process_running(self):
        own=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:stop_group(own);self.assertIsNotNone(own.poll());self.assertIsNone(other.poll())
        finally:stop_group(other)

if __name__=='__main__':unittest.main()
