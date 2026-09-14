import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'monitor'))
from harness_monitor import HarnessMonitor


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root/'campaign'
        self.folder.mkdir()
        self.base = self.folder/'A.1'
        self.write('log', 'ARM A.1 START 2026-09-11 10:00:00 tokens=100\n')
        self.write('map', '[glm-arm] device map: internal=disk0 Green=disk4\n')
        self.write('csv', 't_s,dev,v1,v2,v3\n0,disk0,0,0,0\n0,disk4,0,0,0\n.2,disk0,200000000,1,0\n.2,disk4,100000000,1,0\n')
        self.monitor = HarnessMonitor()

    def write(self, ext, value):
        p = Path(str(self.base)+'.'+ext)
        p.write_text(value)
        os.utime(p, (100, 100))
        return p

    def test_follow_increments_once_and_handles_partial_line(self):
        chunks = self.write('chunks', '1 99 10\n2 100 15\n3 101')
        first = self.monitor.snapshot(self.root, now=101)
        self.assertEqual(first['state'], 'Receiving responses')
        self.assertEqual(first['chunks'], 2)
        self.assertEqual(first['response_rate'], 1)
        self.assertEqual(first['summary']['ds4_gen_tps'], None)
        self.assertAlmostEqual(first['read_windows']['TOTAL'][0][1], 1.5)
        self.assertEqual(self.monitor.snapshot(self.root, now=102)['chunks'], 2)
        with chunks.open('a') as f:
            f.write(' 3\n')
        self.assertEqual(self.monitor.snapshot(self.root, now=102)['chunks'], 3)

    def test_completed_arm_never_claims_live_even_with_recent_files(self):
        self.write('log', 'ARM A.1 START tokens=100\nARM A.1 END now rc=0\n'+json.dumps({'tag':'A.1', 'ds4_gen_tps':2.1})+'\n')
        self.write('chunks', '1 99 10\n2 100 15\n')
        result = self.monitor.snapshot(self.root, now=101)
        self.assertEqual(result['state'], 'Completed')
        self.assertFalse(result['fresh'])
        self.assertIsNone(result['response_rate'])
        self.assertEqual(result['summary']['ds4_gen_tps'], 2.1)
        self.assertTrue(result['read_windows']['TOTAL'])

    def test_stale_or_missing_telemetry_is_not_zero_or_live(self):
        result = self.monitor.snapshot(self.root, now=120)
        self.assertEqual(result['state'], 'Waiting / stale')
        self.assertFalse(result['fresh'])
        self.assertIsNone(result['response_rate'])
        self.assertIsNone(result['remote']['physical_disk_read_mb'])
        self.assertIsNone(result['memory'])

    def test_truncation_resets_history_and_chunk_count(self):
        self.write('chunks', '1 99 10\n2 100 15\n')
        self.monitor.snapshot(self.root, now=101)
        self.write('csv', '0,disk0,0,0,0\n')
        self.write('chunks', '1 100 1\n')
        result = self.monitor.snapshot(self.root, now=101)
        self.assertEqual(result['chunks'], 1)
        self.assertFalse(result['read_windows'])

    def test_missing_disk_cannot_form_aggregate_and_counter_reset_is_safe(self):
        self.write('csv', '0,disk0,0\n.2,disk0,200000000\n.4,disk0,1\n.6,disk0,200000001\n')
        result = self.monitor.snapshot(self.root, now=101)
        self.assertNotIn('TOTAL', result['read_windows'])
        self.assertEqual(len(result['read_windows']['internal']), 1)
        self.assertAlmostEqual(result['read_windows']['internal'][0][1], 1)

    def test_selection_stays_in_root_and_source_switch_clears_history(self):
        self.monitor.snapshot(self.root, now=101)
        self.assertIsNone(self.monitor.snapshot(self.root, '../private.log', now=101)['arm'])
        other = self.root/'other'
        other.mkdir()
        self.assertIsNone(self.monitor.snapshot(other, now=101)['arm'])

    def test_remote_application_bytes_do_not_imply_physical_reads(self):
        (self.folder/'report.json').write_text(json.dumps({'arms':[{'tag':'A.1', 'mode':'remote',
            'node_delta':{'read_bytes':500, 'bytes_sent':500, 'cache_hits':0, 'cache_misses':2},
            'm1_iface_tx_delta':{'en1':255,'en2':256}, 'm1_disk_read_mb_delta':123.4}], 'node_stopped':True}))
        r=self.monitor.snapshot(self.root, now=101)['remote']
        self.assertEqual(r['read_bytes'], 500)
        self.assertIsNone(r['physical_disk_read_mb'])
        self.assertEqual(r['device_io_mb'], 123.4)
        self.assertTrue(r['node_stopped'])
        self.assertEqual(r['interface_tx_bytes']['en2'],256)

    def test_engine_stage_progress_and_final_timers(self):
        err=self.write('err','ds4: Metal device Apple M5 Max, 128.00 GiB RAM\n')
        self.assertEqual(self.monitor.snapshot(self.root,now=101)['stage']['current'],'setup')
        with err.open('a') as f:f.write('processing 512 input tokens: 128/512 (25.0%)\n')
        stage=self.monitor.snapshot(self.root,now=101)['stage']
        self.assertEqual(stage['current'],'prefill');self.assertEqual(stage['progress'],{'processed':128,'total':512})
        self.write('chunks','1 99 10\n')
        self.assertEqual(self.monitor.snapshot(self.root,now=101)['stage']['current'],'decode')
        with err.open('a') as f:f.write('ds4: generation counts: generated=100 requested=100 decode_seconds=30.25 prompt_tokens=512 prefill_seconds=12.5\n')
        self.assertEqual(self.monitor.snapshot(self.root,now=101)['stage']['current'],'finalizing')
        self.write('log','ARM A.1 START tokens=100\nARM A.1 END now rc=0\n')
        stage=self.monitor.snapshot(self.root,now=101)['stage']
        self.assertEqual(stage['current'],'complete');self.assertFalse(stage['live'])
        self.assertEqual(stage['timings']['prefill_seconds'],12.5)
        self.assertEqual(stage['timings']['decode_seconds'],30.25)
    def test_stage_missing_stale_and_failed_are_not_live_inference(self):
        stage=self.monitor.snapshot(self.root,now=101)['stage']
        self.assertEqual(stage['current'],'unknown')
        self.write('err','processing 6 input tokens: 6/6 (100%)\n')
        stage=self.monitor.snapshot(self.root,now=120)['stage']
        self.assertEqual(stage['current'],'prefill');self.assertFalse(stage['live'])
        self.write('log','ARM A.1 START tokens=100\nARM A.1 END now rc=1\n')
        self.assertEqual(self.monitor.snapshot(self.root,now=101)['stage']['current'],'failed')
    def test_stderr_truncation_resets_stage_and_bad_progress_is_ignored(self):
        self.write('err','processing 512 input tokens: 128/512 (25%)\n')
        self.monitor.snapshot(self.root,now=101)
        self.write('err','bad log\n')
        self.assertEqual(self.monitor.snapshot(self.root,now=101)['stage']['current'],'unknown')
        self.write('err','processing 6 input tokens: 10/6 (166%)\n')
        self.assertIsNone(self.monitor.snapshot(self.root,now=101)['stage']['progress'])

    def test_benchmark_without_sampler_is_visible_and_not_chunks(self):
        Path(str(self.base)+'.map').unlink()
        Path(str(self.base)+'.csv').unlink()
        record={'prompt_tokens':512,'generated_tokens':512,'prefill_tok_s':15.13,
                'generation_tok_s':9.23,'steady_tok_s':9.46,'first_decode_step_ms':1447.65}
        self.write('log','ARM A.1 START tokens=512\nARGODRIVE_BENCH_RESULT '+json.dumps(record)+'\n')
        result=self.monitor.snapshot(self.root,now=101)
        self.assertEqual(result['kind'],'ds4-bench')
        self.assertTrue(result['done']);self.assertFalse(result['fresh'])
        self.assertEqual(result['summary']['ds4_gen_tps'],9.23)
        self.assertEqual(result['summary']['ds4_steady_tps'],9.46)
        self.assertIsNone(result['chunks']);self.assertIsNone(result['response_rate'])
        self.assertFalse(result['devices']);self.assertFalse(result['read_windows'])
        self.assertIn('No SSD timeline',result['timeline_note'])
        self.assertNotIn('decode_seconds',result['stage']['timings'])
        self.assertEqual(result['stage']['current'],'complete')

    def test_benchmark_start_and_invalid_result(self):
        Path(str(self.base)+'.map').unlink()
        self.write('log','ARM A.1 START\nARGODRIVE_BENCH_START '+json.dumps({'prompt_tokens':512,'generated_tokens':128})+'\n')
        result=self.monitor.snapshot(self.root,now=101)
        self.assertFalse(result['done']);self.assertIsNone(result['generated_tokens'])
        self.assertIsNone(result['summary']['ds4_gen_tps'])
        self.write('log','ARM A.1 START\nARGODRIVE_BENCH_RESULT {"prompt_tokens":512,"generated_tokens":128,"generation_tok_s":NaN}\n')
        self.assertIsNone(self.monitor.snapshot(self.root,now=101)['arm'])


if __name__ == '__main__':
    unittest.main()
