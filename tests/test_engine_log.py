import os, stat, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
from engine_log import barrier_attribution, prefill_path, dead_knobs, live_knobs

# The four shapes measured on 2026-09-15, verbatim from baseline.engine.txt.
COMMON = (b'ds4: Argodrive primary expert descriptor F_NOCACHE=1; mmap/Engram descriptor unchanged\n'
          b'ds4: memory detail: ctx=4096 prefill_cap=2048 raw_kv_rows=128 compressed_kv_rows=4097 backend=metal\n')

REJECTED_ONE_SOURCE = (COMMON +
    b'ds4: experimental Argodrive expert reader sources=1; Engram unchanged\n'
    b'ds4: Argodrive selective prefill staging for chunks <= 512 tokens\n'
    b'ds4: Argodrive pipelined prefill staging in 256-token chunks\n'
    b'ds4: Argodrive prefill stage rejected: reader invalid=0 sources=1\n'
    b'ds4: V4.1 layer-major prefill failed at layer 0\n')

SELECTIVE_ONE_SOURCE = (COMMON +
    b'ds4: experimental Argodrive expert reader sources=1; Engram unchanged\n'
    b'ds4: Argodrive selective prefill staging for chunks <= 512 tokens\n'
    b'ds4: Argodrive prefill staging lanes=8\n'
    b'ds4: Argodrive prefill staged(selected) experts=295 bytes=5872435200 of 7644119040 (77%) source_bytes=5872435200\n'
    b'ds4: Argodrive prefill staged(selected) experts=245 bytes=4877107200 of 7644119040 (64%) source_bytes=4877107200\n')

SELECTIVE_THREE_SOURCES = (COMMON +
    b'ds4: experimental Argodrive expert reader sources=3; Engram unchanged\n'
    b'ds4: Argodrive selective prefill staging for chunks <= 512 tokens\n'
    b'ds4: Argodrive pipelined prefill staging in 256-token chunks\n'
    b'ds4: Argodrive prefill staging lanes=8\n'
    b'ds4: Argodrive prefill staged(selected) experts=250 bytes=4976640000 of 7644119040 (65%) source_bytes=2514321408\n')

UPSTREAM = (b'ds4:   expert budget before prefill reserve: 4456 (82.61 GiB)\n'
            b'ds4: memory detail: ctx=4096 prefill_cap=2048 raw_kv_rows=128 compressed_kv_rows=4097 backend=metal\n'
            b'ds4-bench: context buffers 1832.21 MiB (ctx=4096, backend=metal, prefill_chunk=2048)\n')


class PrefillPathTests(unittest.TestCase):
    def test_rejected_single_source_is_a_sweep_and_says_why(self):
        p = prefill_path(REJECTED_ONE_SOURCE)
        self.assertEqual(p['path'], 'sweep-after-rejection')
        self.assertFalse(p['engaged'])
        self.assertEqual(p['sources'], 1)
        self.assertTrue(p['selective_requested'])
        self.assertEqual(p['rejections'], ['reader invalid=0 sources=1'])
        self.assertEqual(p['failed_at_layer'], 0)
        self.assertIn('REJECTED', p['verdict'])
        self.assertIn('reader invalid=0 sources=1', p['verdict'])

    def test_selective_single_source_engaged_with_coverage(self):
        p = prefill_path(SELECTIVE_ONE_SOURCE)
        self.assertEqual(p['path'], 'staged-selective')
        self.assertTrue(p['engaged'])
        self.assertEqual(p['sources'], 1)
        self.assertEqual(p['lanes'], 8)
        self.assertEqual(p['staged_layers'], 2)
        self.assertEqual(p['staged_experts'], 295 + 245)
        self.assertEqual(p['staged_bytes'], 5872435200 + 4877107200)
        self.assertEqual(p['full_layer_bytes'], 2 * 7644119040)
        self.assertAlmostEqual(p['coverage'], (5872435200 + 4877107200) / (2 * 7644119040), places=6)
        self.assertEqual(p['rejections'], [])
        self.assertIn('engaged on 1 source', p['verdict'])

    def test_three_sources_reports_source_count_and_pipelining(self):
        p = prefill_path(SELECTIVE_THREE_SOURCES)
        self.assertEqual(p['path'], 'staged-selective')
        self.assertEqual(p['sources'], 3)
        self.assertEqual(p['pipelined_chunk_tokens'], 256)
        self.assertEqual(p['selective_max_tokens'], 512)

    def test_upstream_has_no_argodrive_and_is_a_plain_sweep(self):
        p = prefill_path(UPSTREAM)
        self.assertEqual(p['path'], 'sweep')
        self.assertFalse(p['argodrive_present'])
        self.assertIsNone(p['sources'])
        self.assertFalse(p['engaged'])
        self.assertIn('upstream', p['verdict'])

    def test_accepts_str_input(self):
        self.assertEqual(prefill_path(SELECTIVE_ONE_SOURCE.decode())['path'], 'staged-selective')


THREE_SOURCE_FOOTER = (
    b'ds4: Argodrive source[0] bytes=104175796224\n'
    b'ds4: Argodrive source[1] bytes=49741037568\n'
    b'ds4: Argodrive source[2] bytes=48931012608\n'
    b'ds4: Argodrive source[0] lands_last=1203 gap_ns=402113000 reads=7360\n'
    b'ds4: Argodrive source[1] lands_last=2044 gap_ns=1710884000 reads=7360\n'
    b'ds4: Argodrive source[2] lands_last=4113 gap_ns=6012350000 reads=7360\n')


class BarrierAttributionTests(unittest.TestCase):
    def test_single_source_run_has_nothing_to_attribute(self):
        self.assertEqual(barrier_attribution(SELECTIVE_ONE_SOURCE), {})
        self.assertEqual(barrier_attribution(b'ds4: Argodrive source[0] bytes=5\n'), {})

    def test_three_sources_name_the_tail_and_its_cost(self):
        a = barrier_attribution(THREE_SOURCE_FOOTER)
        s = a['sources']
        self.assertEqual(set(s), {'0', '1', '2'})
        self.assertEqual(s['2']['lands_last'], 4113)
        self.assertEqual(s['2']['reads'], 7360)
        self.assertAlmostEqual(s['2']['share'], 4113 / 7360, places=6)
        self.assertAlmostEqual(s['2']['mean_gap_ms'], 6012350000 / 4113 / 1e6, places=6)
        self.assertEqual(a['worst_source'], '2')
        self.assertAlmostEqual(a['total_gap_ms'], (402113000 + 1710884000 + 6012350000) / 1e6, places=6)
        self.assertIn('source 2 landed last on 56%', a['verdict'])
        self.assertIn('74% of all barrier wait', a['verdict'])

    def test_share_and_mean_gap_handle_zero_counts(self):
        a = barrier_attribution(b'ds4: Argodrive source[0] lands_last=0 gap_ns=0 reads=0\n'
                                b'ds4: Argodrive source[1] lands_last=0 gap_ns=0 reads=0\n')
        self.assertIsNone(a['sources']['0']['share'])
        self.assertEqual(a['sources']['0']['mean_gap_ms'], 0.0)
        self.assertIn('nothing to attribute', a['verdict'])

    def test_accepts_str_input(self):
        self.assertEqual(barrier_attribution(THREE_SOURCE_FOOTER.decode())['worst_source'], '2')


class DeadKnobTests(unittest.TestCase):
    def _fake_binary(self, names):
        # A file whose printable strings are exactly `names`; `strings` needs >=4 chars.
        d = tempfile.mkdtemp()
        p = Path(d) / 'ds4-bench'
        p.write_bytes(b'\x00'.join(n.encode() for n in names) + b'\x00')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return str(p)

    def test_flags_knobs_absent_from_binary_and_ignores_harness_vars(self):
        b = self._fake_binary(['DS4_ARGODRIVE_REPLICAS', 'DS4_ARGODRIVE_PREFILL_SPLIT', 'unrelated text'])
        env = {'DS4_ARGODRIVE_REPLICAS': '/a*1', 'DS4_ARGODRIVE_PREFILL_SPLIT': '1',
               'DS4_ARGODRIVE_PRECOMMIT': '1', 'DS4_MODEL_REPLICAS': '/b*1', 'GLM_CTX': '4096'}
        self.assertEqual(dead_knobs(b, env), ['DS4_ARGODRIVE_PRECOMMIT', 'DS4_MODEL_REPLICAS'])
        self.assertEqual(live_knobs(b, env), ['DS4_ARGODRIVE_PREFILL_SPLIT', 'DS4_ARGODRIVE_REPLICAS'])

    def test_missing_binary_reports_nothing_rather_than_everything_dead(self):
        self.assertEqual(dead_knobs('/nonexistent/ds4', {'DS4_ARGODRIVE_REPLICAS': 'x'}), [])


if __name__ == '__main__':
    unittest.main()
