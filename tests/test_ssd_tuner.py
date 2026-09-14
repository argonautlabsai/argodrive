import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'monitor'))
import ssd_tuner as tuner
from optimizer_storage import read_trial, file_identity

class TunerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.request={'paths':['/model.gguf'],'model_family':'deepseek41','engine_build':'upstream','build_identity':''}
        self.sources=[{'disk':'disk0','label':'Internal','path':'/model.gguf','volume_uuid':'uuid'}]
    def test_invalid_requests_and_commands_rejected(self):
        for bad in ({**self.request,'paths':['relative.gguf']},{**self.request,'paths':['/a.gguf','/a.gguf']},
                    {**self.request,'paths':['/dev/disk0']},{**self.request,'command':'rm anything'},
                    {**self.request,'model_family':'invented'},{**self.request,'build_identity':'x\ncommand'}):
            with self.assertRaises(ValueError):tuner.checked_request(bad)
    def test_campaign_lock_refuses_start_without_creating_outputs(self):
        with patch.object(tuner,'LOCK_PATH',self.root/'campaign.lock'):
            fd=tuner.campaign_lock()
            try:
                runner=tuner.SSDTuner(self.root,[])
                with patch.object(tuner,'guard_idle') as guard,patch.object(tuner.subprocess,'Popen') as popen:
                    with self.assertRaisesRegex(ValueError,'campaign lock'):runner.start(self.request)
                    guard.assert_not_called();popen.assert_not_called()
                    self.assertFalse(runner.root.exists())
            finally:os.close(fd)
    def test_stop_never_adopts_another_worker(self):
        runner=tuner.SSDTuner(self.root,[]);runner.folder=self.root/'owned';runner.process=Mock()
        with self.assertRaises(ValueError):runner.stop('other')
        runner.process.send_signal.assert_not_called()
    def test_shared_link_is_tested_together_not_added_as_capacity(self):
        sources=[{'disk':d} for d in ('disk0','disk1','disk2')]
        topology={'shared_uplinks':[{'node_id':'hub','physical_devices':['disk1','disk2']},
                                    {'node_id':'port','physical_devices':['disk1','disk2']}]}
        schedule=tuner.make_schedule(sources,topology)
        shared=[r for r in schedule if r['kind']=='shared_uplink']
        self.assertEqual(len(shared),3)
        self.assertTrue(all(set(r['disks'])=={'disk1','disk2'} for r in shared))
        self.assertEqual(len([r for r in schedule if r['kind']=='all_selected']),3)
        self.assertEqual([r['geometry'] for r in schedule if r['phase']=='compare'],['equal','weighted','equal','weighted'])
    def test_knee_prefers_lower_concurrency_when_near_best(self):
        self.assertEqual(tuner.knee([{'workers':2,'aggregate_gbps':9.7},{'workers':4,'aggregate_gbps':10},
                                    {'workers':8,'aggregate_gbps':8}])['workers'],2)
    def test_counter_window_excludes_boundaries_and_background_writes(self):
        p=self.root/'c.csv';p.write_text('0,disk0,0\n.2,disk0,100000000\n.4,disk0,300000000\n.6,disk0,400000000\n.2,disk0w,900000000\n')
        result=tuner.device_window(p,.1,.5,['disk0'])
        self.assertEqual(result['devices'][0]['bytes'],200000000)
        self.assertAlmostEqual(result['aggregate_gbps'],1)
        self.assertIsNone(tuner.device_window(p,.1,.5,['disk0','disk1']))
    def test_report_does_not_claim_engine_or_token_gain(self):
        r={'workers_per_drive':4,'candidate_weights':[1],'confirmation_range_gbps':[5,5.1],
           'engine_thread_candidate':4,'confidence':'storage candidate; model validation required'}
        report={'recommendation':r,'request':self.request,'sources':self.sources,'trials':[],
                'model':{'name':'test model','header_sha256':'abc','component_sizes':[7077888]},'topology':{},'folder':'/reports/id'}
        text=tuner.report_text(report)
        self.assertIn('Do not delete, overwrite',text)
        self.assertIn('full payload equality NOT verified',text)
        self.assertIn('Multi-drive routing must be implemented',text)
        self.assertIn('No token-speed gain is predicted',text)
        self.assertNotIn('export DS4_MODEL_REPLICAS',text)
    def test_reader_refuses_file_changed_between_inspection_and_open(self):
        p=self.root/'model.gguf';p.write_bytes(b'original');source={**file_identity(p),'disk':'disk0'}
        p.write_bytes(b'different file length')
        with self.assertRaisesRegex(ValueError,'changed before opening'):
            read_trial([source],{'tensors':[]},{},.1,1,threading.Event(),lambda _:None,policy=lambda _:None)
        self.assertEqual(p.read_bytes(),b'different file length')
    def test_short_read_failure_preserves_file(self):
        p=self.root/'model.gguf';p.write_bytes(b'sentinel');source={**file_identity(p),'disk':'disk0'}
        model={'tensors':[{'component_bytes':100,'offset':1000,'experts':1}]}
        with self.assertRaisesRegex(OSError,'Short read'):
            read_trial([source],model,{100:[[[0,100]]]},.1,1,threading.Event(),lambda _:None,policy=lambda _:None)
        self.assertEqual(p.read_bytes(),b'sentinel')
    def test_worker_lifecycle_with_fixture_trials(self):
        out=self.root/'run';out.mkdir();model_file=self.root/'model.gguf';model_file.write_bytes(b'keep this file')
        (self.root/'k3-diskscope').write_text('fixture only')
        q={**self.request,'paths':[str(model_file)]}
        (out/'request.json').write_text(json.dumps({'folder':str(out),'selection':q}))
        (out/'status.json').write_text(json.dumps({'id':'run','status':'preflight'}))
        source={**file_identity(model_file),'disk':'disk0','label':'Internal','volume_uuid':'uuid'}
        model={'name':'fixture','architecture':'fixture','header_sha256':'abc','component_sizes':[4],'bytes':14,'tensors':[]}
        def trial(sources,model,geometry,seconds,workers,cancel,progress):
            start=time.monotonic();progress({'elapsed_s':seconds,'devices':[]})
            return {'start':start,'end':start+seconds,'seconds':seconds,'aggregate_gbps':5,
                    'devices':[{'disk':'disk0','gbps':5,'p95_ms':1,'bytes':10,'reads':2}]}
        sampler=Mock();sampler.poll.return_value=None
        old=[signal.getsignal(signal.SIGTERM),signal.getsignal(signal.SIGINT)]
        try:
            with patch.object(tuner,'LOCK_PATH',self.root/'lock'),patch.object(tuner,'__file__',str(self.root/'ssd_tuner.py')),\
                 patch.object(tuner,'guard_idle'),patch.object(tuner,'sources_for',return_value=([source],model)),\
                 patch.object(tuner,'recheck_sources'),patch.object(tuner,'TopologyInventory') as topo,\
                 patch.object(tuner.subprocess,'Popen',return_value=sampler),patch.object(tuner,'read_trial',side_effect=trial),\
                 patch.object(tuner,'device_window',return_value={'aggregate_gbps':0,'devices':[]}):
                topo.return_value.snapshot.return_value={}
                self.assertEqual(tuner.worker_main(out/'request.json'),0,(out/'status.json').read_text())
            self.assertEqual(json.loads((out/'status.json').read_text())['status'],'complete')
            self.assertTrue((out/'claude-instructions.txt').is_file());sampler.terminate.assert_called_once()
            self.assertEqual(model_file.read_bytes(),b'keep this file')
            report=json.loads((out/'report.json').read_text())
            self.assertIsNone(report['recommendation']['expected_token_gain_percent'])
            self.assertFalse(report['recommendation']['applied'])
        finally:signal.signal(signal.SIGTERM,old[0]);signal.signal(signal.SIGINT,old[1])

if __name__=='__main__':unittest.main()
