import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from test_monitor import dashboard
from streaming_profile import streaming_profile
from test_product import log


class StreamingTests(unittest.TestCase):
    def test_engine_mode_overrides_request_without_claiming_raid_or_devices(self):
        text='CONFIG model=/model.gguf cache=auto\nDS4_ENV DS4_MODEL_REPLICAS=/model.gguf*1 DS4_MODEL_PRIMARY_WEIGHT=10 DS4_MODEL_FD_NOCACHE=0\n'
        engine='ds4: expert pread striping across 2 fds, 2 weighted slots (primary weight 1, mode=split-read)\nds4: F_NOCACHE set on 2 model fd(s)\n'
        p=streaming_profile(text,engine)
        self.assertEqual((p['method'],p['status']),('Replica split reads','engine'))
        fields={f['label']:f for f in p['fields']}
        self.assertEqual(fields['Primary weight']['value'],'1')
        self.assertEqual(fields['Filesystem RAID']['value'],'Not recorded')
        self.assertEqual(fields['Page-cache policy']['source'],'engine')
        self.assertNotIn('Physical drives',fields)

    def test_requested_replicas_do_not_prove_mode_or_raid(self):
        p=streaming_profile('DS4_ENV DS4_MODEL_REPLICAS=/one*5,/two*4\n')
        self.assertEqual((p['method'],p['status']),('Replicas configured','requested'))
        self.assertEqual(len(p['homes']),2)
        self.assertEqual(streaming_profile('ARM raid0-dual-home-4dr START\n')['status'],'unknown')

    def test_engine_confirmed_prefetch_and_cache(self):
        p=streaming_profile('DS4_ENV DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH=8\n',
          'ds4: router-lookahead prefetch on: up to 2 experts of layer+1 per layer\n'
          'ds4: metal SSD streaming cache target 66.41 GiB; effective 66.41 GiB = 10.12 GiB prefill headroom + 56.28 GiB dynamic cache (2846 experts)\n')
        fields={f['label']:f for f in p['fields']}
        self.assertEqual(fields['Lookahead experts']['value'],'2')
        self.assertEqual(fields['Lookahead experts']['source'],'engine')
        self.assertEqual(fields['RAM expert cache GiB']['value'],'56.28')

    def test_ds41_observed_reader_and_cache_phase(self):
        engine = ('ds4: experimental Argodrive expert reader sources=3; Engram unchanged\n'
          'ds4: Argodrive Engram readers=8\n'
          'ds4: metal SSD streaming cache target 82.61 GiB; effective 82.61 GiB = 14.24 GiB prefill headroom + 68.37 GiB dynamic cache (3688 experts)\n'
          'ds4: Argodrive decode cache requested=4456 effective=4456 reserve_bytes=0; total allowance unchanged\n'
          'ds4: Argodrive source[1] bytes=123456\n')
        p=streaming_profile('',engine);fields={f['label']:f for f in p['fields']}
        self.assertEqual(p['status'],'engine')
        self.assertEqual(fields['Engram reader workers']['value'],'8')
        self.assertEqual(fields['Initial RAM expert cache GiB']['value'],'68.37')
        self.assertEqual(fields['Decode cache expert slots']['value'],'4456')
        self.assertEqual(fields['Source 1 application bytes (whole arm)']['value'],'123456')
        self.assertNotIn('Physical drives',fields)
        self.assertNotIn('RAM expert cache GiB',fields)
        requested=streaming_profile('DS4_ENV DS4_ARGODRIVE_ENGRAM_READERS=8 DS4_ARGODRIVE_DECODE_CACHE_PCT=100\n')
        self.assertFalse(any(f['label']=='Decode cache expert slots' for f in requested['fields']))

    def test_ds41_engine_file_is_bounded_and_err_has_precedence(self):
        from streaming_profile import engine_header
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'baseline.log'
            p.with_suffix('.engine.txt').write_text('x'*200000)
            self.assertEqual(len(engine_header(p)),131072)
            p.with_suffix('.err').write_text('legacy')
            self.assertEqual(engine_header(p),'legacy')

    def test_new_engine_artifact_refreshes_ledger_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'day';p.mkdir();log(p/'test.log')
            with patch.object(dashboard,'SOAK',tmp):
                self.assertEqual(dashboard._stats_blocks()[0]['rows'][0]['streaming_status'],'unknown')
                (p/'test.err').write_text('ds4: expert pread striping across 4 fds, 24 weighted slots (primary weight 10, mode=split-read)\n')
                os.utime(p/'test.err',(100,100))
                row=dashboard._stats_blocks()[0]['rows'][0]
                self.assertEqual(row['streaming_method'],'Replica split reads')
                d=json.loads(dashboard.arm_detail('day','test'))
                self.assertEqual(d['folder'],str(p))
                self.assertTrue(any(f['name']=='test.err' for f in d['files']))
                self.assertEqual(d['streaming']['status'],'engine')
                csv=dashboard.stats_csv().decode()
                self.assertIn('req_DS4_STREAM_THREADS',csv)
                self.assertIn('Replica split reads',csv)

    def test_ds41_engine_artifact_refreshes_cached_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'day';p.mkdir();log(p/'test.log')
            with patch.object(dashboard,'SOAK',tmp):
                self.assertEqual(dashboard._stats_blocks()[0]['rows'][0]['streaming_status'],'unknown')
                (p/'test.engine.txt').write_text('ds4: experimental Argodrive expert reader sources=3; Engram unchanged\n')
                row=dashboard._stats_blocks()[0]['rows'][0]
                self.assertEqual(row['streaming_status'],'engine')
                self.assertEqual(row['streaming_method'],'Argodrive expert reads')
                d=json.loads(dashboard.arm_detail('day','test'))
                self.assertTrue(any(f['name']=='test.engine.txt' for f in d['files']))

    def test_read_windows_preserve_actual_durations(self):
        with patch.object(dashboard,'read_windows',{'TOTAL':[(100.2,10,.2),(100.5,2,.3)]}):
            d=json.loads(dashboard.payload())
        self.assertEqual(d['read_windows']['TOTAL'][1][2],.3)

    def test_process_failure_is_unavailable_not_zero_engine_memory(self):
        with patch.object(dashboard,'_procs_cache',{'t':0,'val':b''}), \
             patch.object(dashboard.subprocess,'run',side_effect=PermissionError('not permitted')):
            d=json.loads(dashboard.procs_payload())
        self.assertFalse(d['available'])
        self.assertIsNone(d['engine_rss'])
        self.assertIsNone(d['unattributed'])
        self.assertIn('not permitted',d['error'])

if __name__=='__main__':unittest.main()
