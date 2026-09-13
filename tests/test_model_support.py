import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'monitor'))
import model_support as m
import ds41_benchmark as b
from test_monitor import dashboard


class ModelSupportTests(unittest.TestCase):
    def test_deltafin_kimi_profile_emits_only_k3_controls_and_maps_roles(self):
        p=m.deltafin_kimi_profile('/models/k3-root', ['/Volumes/Green/k3-experts', '/Volumes/White/k3-hot', '/Volumes/Yellow/k3-c'])
        self.assertFalse(p['enabled'])
        self.assertEqual(p['method'], 'Complete replicas + weighted split-read and per-tier ETA routing')
        self.assertEqual(p['environment']['K3_EXPERT_DIR_B'], '/Volumes/Green/k3-experts')
        self.assertEqual(p['environment']['K3_EXPERT_HOT_DIR'], '/Volumes/White/k3-hot')
        self.assertEqual(p['environment']['K3_EXPERT_DIR_C'], '/Volumes/Yellow/k3-c')
        self.assertEqual(p['environment']['K3_SPLIT_READ'], '2')
        self.assertNotIn('DS4_MODEL_REPLICAS', p['environment'])

    def test_deltafin_kimi_profile_rejects_ambiguous_paths_and_too_many_replicas(self):
        p=m.deltafin_kimi_profile('relative', ['/one', '/two', '/three', '/four'])
        self.assertGreaterEqual(len(p['errors']), 2)
        self.assertIn('absolute', ' '.join(p['errors']))

    def test_existing_replica_space_is_not_double_counted_or_verified(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'DS41_Q4_BYTES', 16):
            p=Path(d)/'model.gguf';p.write_bytes(b'GGUF\x03\0\0\0'+b'\0'*8)
            target=Path(d)/'drive';target.mkdir()
            free=20*m.GIB+8
            with patch.object(m.shutil, 'disk_usage') as usage:
                usage.return_value.free=free
                body={'model_path':str(p),'replica_directories':[str(target)]}
                self.assertFalse(m.readiness(body)['placements'][0]['has_capacity'])
                (target/p.name).write_bytes(p.read_bytes())
                r=m.readiness(body);q=r['placements'][0]
                self.assertTrue(q['has_capacity']);self.assertEqual(q['copy_bytes_required'],0)
                self.assertTrue(q['existing_size_and_header_match']);self.assertFalse(q['checksum_verified'])
                self.assertFalse(r['ready_to_run']);self.assertFalse(r['experimental_profile']['enabled'])
                (target/p.name).unlink();os.mkfifo(target/p.name)
                self.assertIn('regular GGUF',m.readiness(body)['placements'][0]['error'])

    def test_fork_profile_does_not_enable_upstream_or_ambiguous_replica_paths(self):
        p=m.ds41_fork_profile('/model', ['/Green/model','/White/model'])
        self.assertFalse(p['upstream_compatible']);self.assertFalse(p['enabled'])
        self.assertEqual(p['environment']['DS4_ARGODRIVE_REPLICAS'],'/Green/model*1,/White/model*1')
        self.assertEqual(p['environment']['DS4_ARGODRIVE_ENGRAM_READERS'],'8')
        self.assertNotIn('DS4_MODEL_REPLICAS',p['environment'])
        bad=m.ds41_fork_profile('/model', ['/with,separator/model'])
        self.assertTrue(bad['errors']);self.assertNotIn('DS4_ARGODRIVE_REPLICAS',bad['environment'])

    def test_versions_and_engines_are_distinct(self):
        self.assertEqual(m.model_identity('DeepSeek-V4.1-Flash-Q4.gguf')['id'], 'deepseek41')
        self.assertEqual(m.model_identity('DeepSeek-V4-Flash-Q2.gguf')['id'], 'deepseek')
        self.assertEqual(m.model_identity('GLM-5.3-Q4.gguf')['engine'], 'ds4')
        self.assertEqual(m.model_identity('Kimi K3')['engine'], 'deltafin')
        self.assertTrue(m.model_catalog()['models'][-1]['local_qualification'])
        self.assertFalse(m.model_catalog()['models'][-1]['installed_binary_verified'])
        self.assertFalse(m.model_catalog()['models'][-1]['benchmark_evidence']['public_record'])

    def test_incomplete_missing_wrong_header_and_matching_size_never_claim_identity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'model.gguf';body={'model_path':str(p),'replica_directories':[]}
            self.assertFalse(m.readiness(body)['size_and_header_match'])
            with patch.object(m,'DS41_Q4_BYTES',16):
                p.write_bytes(b'bad!'+b'\0'*12)
                self.assertFalse(m.readiness(body)['size_and_header_match'])
                p.write_bytes(b'GGUF\x03\0\0\0'+b'\0'*8)
                r=m.readiness(body);self.assertTrue(r['size_and_header_match'])
                self.assertFalse(r['ready_to_run']);self.assertFalse(r['checksum_verified'])
                self.assertEqual(p.stat().st_size,16)

    def test_space_and_same_filesystem_do_not_masquerade_as_independent_drives(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'model.gguf';p.write_bytes(b'GGUF\x03\0\0\0')
            target=Path(d)/'enclosure';target.mkdir()
            r=m.readiness({'model_path':str(p),'replica_directories':[str(target)]})
            self.assertFalse(r['placements'][0]['distinct_filesystem'])
            self.assertFalse(r['placements'][0]['physical_ssd_verified'])
            with self.assertRaises(ValueError):m.readiness({'model_path':str(p),'replica_directories':[str(target),str(target)]})

    def test_fifo_rejected_without_blocking_and_no_arbitrary_request_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fifo';os.mkfifo(p)
            with self.assertRaises(ValueError):m.readiness({'model_path':str(p),'replica_directories':[]})
        for body in ({'model_path':'relative','replica_directories':[]},{'model_path':'/x','replica_directories':[],'command':'run'}):
            with self.assertRaises(ValueError):m.readiness(body)

    def test_benchmark_counts_and_nonfinite_values_are_rejected(self):
        header='ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,gen_first_ms,gen_steady_tokens,gen_steady_tps,kvcache_bytes\n'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bench.csv'
            p.write_text(header+'512,512,24.5,128,5.12,200,127,5.13,0\n')
            r=b.parse_result(p,512,128)
            self.assertEqual(r['generated_tokens'],128);self.assertIsNone(r['first_response_seconds'])
            self.assertFalse(r['publication_ready'])
            for row in ('512,512,24.5,127,5.12,200,126,5.13,0','512,512,24.5,128,nan,200,127,5.13,0'):
                p.write_text(header+row+'\n')
                with self.assertRaises(ValueError):b.parse_result(p,512,128)

    def test_missing_model_cannot_start_engine(self):
        with tempfile.TemporaryDirectory() as d,patch.object(b.subprocess,'Popen') as spawn:
            p={'model_ready':False,'model_path':str(Path(d)/'missing'),'output_directory':str(Path(d)/'out')}
            with self.assertRaises(ValueError):b.run(p)
            spawn.assert_not_called()

    def test_benchmark_import_retains_actual_counts_without_inventing_ttft(self):
        record={'generated_tokens':128,'steady_tokens':127,'steady_tok_s':5.13,
                'generation_tok_s':5.12,'prefill_tok_s':24.5,'prompt_tokens':512,
                'model_path':'/models/DeepSeek-V4.1-Flash-Q4.gguf',
                'rate_source':'ds4-bench engine tokens','prompt_sha256':'abc','context':4096}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'baseline.log'
            p.write_text('ARM baseline START 2026-09-12T12:00:00Z overrides: tokens=128\nARGODRIVE_BENCH_RESULT '+json.dumps(record)+'\n')
            r=dashboard._parse_arm(str(p))
            self.assertEqual(r['model_identity']['id'],'deepseek41')
            self.assertEqual(r['engine'],'ds4');self.assertEqual(r['generated'],128)
            self.assertEqual(r['tok_s_steady'],5.13)
            self.assertIsNone(r['first_token_s']);self.assertNotIn('chunks',r)
            self.assertFalse(r.get('incomplete',False));self.assertFalse(r['publication_ready'])


if __name__=='__main__':unittest.main()
