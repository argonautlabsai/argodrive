"""Decode budget: per-token means from a DeepSeek V4.1 timeline, never a budget of zeros."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MONITOR = Path(__file__).resolve().parents[1] / 'monitor'
sys.path.insert(0, str(MONITOR))
from decode_budget import SKIP_ROWS, decode_budget, format_budget

HEADER = ('pos,total_ms,engram_ms,layer_body_ms,command_end_ms,logits_ms,gpu_cb_span_ms,cpu_cb_wait_ms,'
          'cb_count,expert_pread_ms,expert_prepare_ms,expert_install_ms,selected_sync_ms,missing_experts,resident_experts')
BENCH = ('ctx_tokens,prefill_tokens,prefill_tps,gen_tokens,gen_tps,gen_first_ms,gen_steady_tokens,gen_steady_tps,kvcache_bytes\n'
         '512,512,44.43,40,15.00,453.929,39,17.64,0\n')


def row(pos, total, engram, logits, gpu, cb, pread, prepare, install, missing):
    # layer_body, command_end, cpu_cb_wait, selected_sync and resident are not budget inputs: fixed fillers.
    return f'{pos},{total},{engram},1.0,1.0,{logits},{gpu},1.0,{cb},{pread},{prepare},{install},1.0,{missing},230'


PREFILL = row(512, 450.0, 0.006, 2.9, 65.4, 54, 22.6, 0.8, 0.03, 11)
WARMUP = [row(513, 67.0, 0.003, 1.4, 41.1, 58, 16.6, 0.9, 0.02, 17), row(514, 51.1, 0.003, 1.4, 34.3, 50, 6.8, 0.4, 0.01, 7)]
MALFORMED = ['515,abc,0.002,1.0,1.0,1.4,34.7,1.0,52,9.9,0.5,0.01,1.0,10,230', '516,50.1']
STEADY = [row(517, 50.0, 0.5, 1.5, 34.0, 50, 8.0, 0.4, 0.1, 8),
          row(518, 54.0, 0.7, 1.3, 36.0, 52, 10.0, 0.6, 0.2, 10),
          row(519, 52.0, 0.6, 1.4, 35.0, 48, 9.0, 0.5, 0.0, 9)]
TIMELINE = '\n'.join(['# engine timeline', HEADER, PREFILL, *WARMUP, *MALFORMED, '', *STEADY, '# dropped=0']) + '\n'


def write_run(root, timeline=TIMELINE, bench=BENCH):
    run = Path(root) / 'prof2-C512g40'
    run.mkdir()
    if timeline is not None:
        (run / 'timeline.csv').write_text(timeline)
    if bench is not None:
        (run / 'bench.csv').write_text(bench)
    return run


class DecodeBudgetTests(unittest.TestCase):
    def test_means_skip_prefill_warmup_comments_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = decode_budget(write_run(tmp))
        self.assertIsNotNone(b)
        self.assertEqual(b['tokens'], 3)
        expected = {'gpu': 35.0, 'misses': 9.0, 'head': 1.4, 'engram': 0.6, 'prepare': 0.6,
                    'other': 52.0 - 35.0 - 9.0 - 1.4 - 0.6 - 0.6, 'total': 52.0,
                    'cbs_per_token': 50.0, 'misses_per_token': 9.0}
        for key, value in expected.items():
            self.assertAlmostEqual(b[key], value, places=3, msg=key)
        self.assertAlmostEqual(sum(b[k] for k in ('gpu', 'misses', 'head', 'engram', 'prepare', 'other')), b['total'], places=2)
        self.assertEqual((b['steady_tok_s'], b['prefill_tok_s']), (17.64, 44.43))
        json.dumps(b, allow_nan=False)   # the /arm payload must stay serialisable

    def test_missing_timeline_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(decode_budget(write_run(tmp, timeline=None)))
            self.assertIsNone(decode_budget(Path(tmp) / 'no-such-run'))

    def test_no_usable_rows_is_none(self):
        cases = {
            'header only': HEADER + '\n',
            'comments only': '# nothing\n# dropped=0\n',
            'wrong columns': 'pos,total_ms\n512,450\n513,50\n514,50\n515,50\n',
            'prefill and warm-up only': '\n'.join([HEADER, PREFILL, *WARMUP]) + '\n',
            'steady rows all malformed': '\n'.join([HEADER, PREFILL, *WARMUP, *MALFORMED]) + '\n',
            'empty file': '',
        }
        self.assertEqual(len(WARMUP) + 1, SKIP_ROWS)
        for name, text in cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(decode_budget(write_run(tmp, timeline=text)), name)

    def test_one_usable_token_is_enough(self):
        text = '\n'.join([HEADER, PREFILL, *WARMUP, STEADY[0]]) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            b = decode_budget(write_run(tmp, timeline=text))
        self.assertEqual((b['tokens'], b['total'], b['gpu']), (1, 50.0, 34.0))

    def test_bench_rates_are_none_without_a_readable_bench_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = decode_budget(write_run(tmp, bench=None))
            self.assertEqual((b['steady_tok_s'], b['prefill_tok_s']), (None, None))
            self.assertEqual(b['total'], 52.0)
        with tempfile.TemporaryDirectory() as tmp:
            b = decode_budget(write_run(tmp, bench='ctx_tokens,gen_steady_tps\n512,nan\n'))
            self.assertEqual((b['steady_tok_s'], b['prefill_tok_s']), (None, None))

    def test_other_is_clamped_at_zero_when_parts_exceed_total(self):
        rows = [row(517 + i, 40.0, 1.0, 2.0, 30.0, 50, 10.0, 1.0, 1.0, 8) for i in range(3)]
        text = '\n'.join([HEADER, PREFILL, *WARMUP, *rows]) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            b = decode_budget(write_run(tmp, timeline=text))
        self.assertEqual(b['other'], 0.0)
        self.assertEqual(b['total'], 40.0)

    def test_format_budget_is_one_line_with_rates_and_shares(self):
        with tempfile.TemporaryDirectory() as tmp:
            line = format_budget(decode_budget(write_run(tmp)))
        self.assertNotIn('\n', line)
        self.assertTrue(line.startswith('17.64 tok/s steady · 52.0 ms/token · 50.0 command buffers/token · 9.0 misses/token'), line)
        self.assertIn('GPU 35.0 ms 67%', line)
        self.assertIn('other 5.4 ms 10%', line)
        self.assertTrue(line.endswith('3 tokens'))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn('steady rate not recorded', format_budget(decode_budget(write_run(tmp, bench=None))))


class ArmDetailBudgetTests(unittest.TestCase):
    """The saved-run detail endpoint carries the budget, and a run without a timeline is not an error."""

    def test_arm_detail_includes_budget_only_when_the_timeline_exists(self):
        from test_monitor import dashboard
        with tempfile.TemporaryDirectory() as tmp:
            block = Path(tmp) / 'prof2-C512g40'
            block.mkdir()
            (block / 'baseline.log').write_text('ARGODRIVE_BENCH_START {}\n')
            with patch.object(dashboard, 'SOAK', tmp):
                self.assertIsNone(json.loads(dashboard.arm_detail('prof2-C512g40', 'baseline'))['decode_budget'])
                (block / 'timeline.csv').write_text(TIMELINE)
                (block / 'bench.csv').write_text(BENCH)
                detail = json.loads(dashboard.arm_detail('prof2-C512g40', 'baseline'))
        self.assertEqual((detail['decode_budget']['tokens'], detail['decode_budget']['total'], detail['decode_budget']['steady_tok_s']), (3, 52.0, 17.64))
        self.assertEqual(detail['block'], 'prof2-C512g40')

    def test_arm_detail_never_reads_a_timeline_outside_the_runs_folder(self):
        from test_monitor import dashboard
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as elsewhere:
            write_run(elsewhere)
            with patch.object(dashboard, 'SOAK', tmp):
                detail = json.loads(dashboard.arm_detail(os.path.join(elsewhere, 'prof2-C512g40'), 'baseline'))
        self.assertIn('error', detail)
        self.assertNotIn('decode_budget', detail)


if __name__ == '__main__':
    unittest.main()
