import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MONITOR = Path(__file__).resolve().parents[1]/'monitor'
sys.path.insert(0, str(MONITOR))
from argodrive_core import CounterBuckets, discover_drives, engine_processes, load_config, chunk_progress
spec = importlib.util.spec_from_file_location('dashboard', MONITOR/'k3-live.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


class MeasurementTests(unittest.TestCase):
    def test_aggregate_requires_same_window_not_sum_of_peaks(self):
        b = CounterBuckets(['disk0','disk4'])
        for d in ['disk0','disk4']: b.add(d, 0, 0)
        self.assertIsNone(b.add('disk0', .2, 2_000_000_000)['total'])
        self.assertAlmostEqual(b.add('disk4', .2, 1_000_000_000)['total'], 15)
        b.add('disk0', .4, 2_200_000_000)
        self.assertAlmostEqual(b.add('disk4', .4, 3_000_000_000)['total'], 11)

    def test_missing_device_never_means_zero_or_index_alignment(self):
        b = CounterBuckets(['a','b'])
        b.add('a', 0, 0); b.add('b', .1, 0)
        self.assertIsNone(b.add('a', .2, 2_000_000_000)['total'])
        self.assertIsNone(b.add('b', .3, 1_000_000_000)['total'])

    def test_elapsed_weighting_handles_jitter(self):
        b = CounterBuckets(['a'])
        b.add('a',0,0); b.add('a',.05,1_000_000_000)
        self.assertAlmostEqual(b.add('a',.25,2_000_000_000)['rate'],8)

    def test_counter_reset_drops_negative_rate(self):
        b = CounterBuckets(['a'])
        b.add('a',0,1000)
        self.assertIsNone(b.add('a',.2,50))
        self.assertAlmostEqual(b.add('a',.4,200_000_050)['total'],1)

    def test_discovery_dedupes_physical_disk_and_keeps_unknown_ceiling(self):
        info = lambda p: {'APFSPhysicalStores':[{'DeviceIdentifier':'disk0s2' if p in ('/','/alias') else 'disk4s2'}]}
        cfg={'drives':[{'id':'internal','path':'/'},{'id':'alias','path':'/alias'},{'id':'fast','path':'/fast','label':'My SSD'}]}
        devices, desc, errors = discover_drives(cfg, info_fn=info)
        self.assertEqual(devices,{'disk0':'internal','disk4':'fast'})
        self.assertIsNone(desc[1]['ceiling_gbps'])
        self.assertFalse(errors)

    def test_missing_mount_is_explicit(self):
        def missing(p): raise OSError('not mounted')
        devices, desc, errors = discover_drives({'drives':[{'id':'fast','path':'/missing'}]}, info_fn=missing)
        self.assertEqual(devices,{})
        self.assertFalse(desc[0]['present']); self.assertTrue(errors)

    def test_process_detection_uses_executable_not_prompt_text(self):
        rows=engine_processes('12 1024 /a/ds4\n13 2048 /b/deltafin\n14 20 /a/ds4-arm.py\n15 20 /bin/python')
        self.assertEqual([r['engine'] for r in rows],['ds4','deltafin'])

    def test_config_rejects_unsafe_duplicate_ids_and_invalid_ceiling(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json'
            for drive in ({'id':'<bad>','path':'/'},{'id':'TOTAL','path':'/'},{'id':'ok','path':'/','ceiling_gbps':-1}):
                p.write_text(json.dumps({'drives':[drive]}))
                with self.assertRaises(ValueError): load_config(p)

    def test_chunk_rate_is_not_mislabelled_token_rate(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.chunks';p.write_text('# header\n1 100 12\n2 101 5\n3 102 9\n')
            got=chunk_progress(p)
            self.assertEqual(got['rate'],1);self.assertEqual(got['unit'],'chunks/s')

    def test_glm_trace_uses_lengths_and_colour_devices(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.readtrace.csv'
            p.write_text('t_us,barrier,layer,expert,src,dur_ns,prio,offset,len\n'
                         '10000,1,2,7,internal,2000000,-,4000000000,18000000\n'
                         '11000,1,2,7,Green,3000000,-,4018000000,4000000\n')
            got=dashboard._bylayer_parse(str(p))
            self.assertEqual(got['records'],2)
            self.assertEqual(sum(r[6] for r in dashboard._BYLAYER_CACHE['raw'][1]['recs']),22_000_000)
            self.assertEqual(got['barriers'][0]['last'],'Green')
            self.assertEqual(got['byte_source'],'trace lengths')

    def test_target_trace_does_not_clamp_against_k3_size(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.readtrace.csv'
            p.write_text('t_us,barrier,layer,expert,src,dur_ns,prio,target_barrier,part,offset,len\n'
                         '10000,1,2,7,K3B,2000000,D,3,0,18000000,4000000\n')
            dashboard._bylayer_parse(str(p))
            self.assertEqual(dashboard._BYLAYER_CACHE['raw'][3]['recs'][0][6],4_000_000)

    def test_expert_summary_uses_glm_size_everywhere(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.hotlist';p.write_text('# layers 75\n# experts 256\n0 4 100\n')
            with patch.object(dashboard,'_resolve_trace',return_value=str(p)):
                got=json.loads(dashboard.experts_payload('run.hotlist'))
            self.assertEqual(got['rec_bytes'],20.25*2**20)
            # 100 uses of one expert = 1.9775 GiB, not K3's 1.634 GiB.
            self.assertAlmostEqual(got['over']['1']['traffic_gib'],2.0)

    def test_historical_rates_do_not_sum_unaligned_samples(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'arm.csv'; mp=Path(d)/'arm.map'
            mp.write_text('internal=disk0 Green=disk4')
            p.write_text('t_s,dev,v1\n0,disk0,0\n0.2,disk0,2000000000\n0.1,disk4,0\n0.3,disk4,1000000000\n')
            got=dashboard._parse_csv(str(p),str(mp))
            self.assertIsNone(got['ssd_tot_peak'])
            self.assertEqual(got['ssd_internal_mean'],10)
            self.assertEqual(got['ssd_Green_mean'],5)

    def test_ds4_inclusive_and_steady_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'arm.log'
            p.write_text('ARM trial START 2026-09-10 10:00:00 tokens=11\nCONFIG model=glm.gguf raw=1 ctx=4096 temp=0\nPROMPT [public test]\nARM trial END 2026-09-10 10:01:00 rc=0\n'+json.dumps({'tag':'trial','decode_tok_s':2,'chunks':11,'gen_s':5,'first_byte_s':6,'rc':0})+'\n')
            got=dashboard._parse_arm(str(p))
            self.assertEqual(got['tok_s'],1)
            self.assertEqual(got['tok_s_steady'],2)
            self.assertEqual(got['ran'],'2026-09-10 10:01:00')

    def test_reports_payload_has_no_synthetic_live_measurements(self):
        got=json.loads(dashboard.payload())
        self.assertEqual(got['mode'],'reports')
        self.assertIsNone(got['cur_total']); self.assertIsNone(got['cap_total'])
        self.assertEqual(got['live_progress']['state'],'unknown')
        self.assertFalse(got['system_fresh'])


if __name__ == '__main__': unittest.main()
