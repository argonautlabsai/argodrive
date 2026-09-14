"""Reporting must not turn incomplete or mismatched evidence into a speed claim."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
import glm_evidence as evidence


class EvidenceTests(unittest.TestCase):
    def fixture(self, directory):
        base = Path(directory) / 'qualify-01-A4-128'
        output, log = b'unchanged output', b'finished log'
        base.with_suffix('.txt').write_bytes(output)
        base.with_suffix('.log').write_bytes(log)
        base.with_suffix('.err').write_text(
            'ds4: generation counts: generated=128 requested=128 decode_seconds=32.000000000 prompt_tokens=6 prefill_seconds=3.000000000\n'
            'ds4:   replica telemetry: fd0[pieces=20 read_ms=1.23 wait_ms=0.98 lands_last=75%]\n')
        base.with_suffix('.capture.json').write_text(json.dumps({'gen_window_s': 31.9, 'first_byte_s': 6.5}))
        result = {'tag': base.name, 'layout': 'A4', 'tokens': 128, 'pair': 1, 'passed': True,
                  'actual_generated_tokens': 128, 'engine_decode_tok_s': 4.,
                  'output_sha256': hashlib.sha256(output).hexdigest(), 'log_sha256': hashlib.sha256(log).hexdigest(),
                  'summary': {'gen_window_gb_by_drive': {'internal': 319.}, 'swap_growth_mb': 0}}
        return base, result

    def test_rate_convention_is_explicit_and_windows_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.fixture(directory)
            plain = evidence.arm_evidence(directory, result)
            derived = evidence.arm_evidence(directory, result, True)
            self.assertTrue(plain['checks_passed'])
            self.assertEqual(plain['engine_generation_tok_s'], 4.)
            self.assertIsNone(plain['derived_decode_evals_per_s'])
            self.assertEqual(derived['derived_decode_evals_per_s'], 127/32)
            self.assertAlmostEqual(plain['decode_window_gb_s_by_drive']['internal'], 10.)
            self.assertEqual(plain['whole_arm_replica_telemetry'][0]['last_demand_batch_pct'], 75)

    def test_raw_output_and_log_tampering_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            base, result = self.fixture(directory)
            base.with_suffix('.txt').write_bytes(b'different')
            base.with_suffix('.log').write_bytes(b'different log')
            row = evidence.arm_evidence(directory, result)
            self.assertFalse(row['checks_passed'])
            self.assertEqual(len(row['failures']), 2)

    def test_wrong_rate_and_actual_count_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.fixture(directory)
            result.update(engine_decode_tok_s=5.1, actual_generated_tokens=127)
            row = evidence.arm_evidence(directory, result)
            self.assertFalse(row['checks_passed'])
            self.assertEqual(len(row['failures']), 2)

    def test_three_complete_reversed_pairs_required(self):
        rows = []
        for pair in (1, 2, 3):
            for layout, rate in (('A4', 4.), ('C5', 4.9)):
                rows.append({'generated_tokens': 128, 'pair': pair, 'layout': layout, 'checks_passed': True,
                             'output_sha256': 'a'*64, 'engine_generation_tok_s': rate, 'derived_decode_evals_per_s': rate*127/128})
        full = evidence.comparisons(rows, 'C5')[0]
        self.assertTrue(full['three_pairs_complete'])
        self.assertAlmostEqual(full['median_gain_pct'], 22.5)
        self.assertFalse(evidence.comparisons(rows[:-1], 'C5')[0]['three_pairs_complete'])
        changed = copy.deepcopy(rows)
        changed[-1]['output_sha256'] = 'b'*64
        self.assertFalse(evidence.comparisons(changed, 'C5')[0]['three_pairs_complete'])
        duplicate = copy.deepcopy(rows)
        duplicate[-1]['pair'] = 2
        self.assertFalse(evidence.comparisons(duplicate, 'C5')[0]['three_pairs_complete'])

    def test_failed_pair_cannot_be_hidden_by_faster_median(self):
        rows = [{'generated_tokens': 512, 'pair': 1, 'layout': layout, 'checks_passed': okay,
                 'output_sha256': 'a'*64, 'engine_generation_tok_s': rate, 'derived_decode_evals_per_s': None}
                for layout, rate, okay in [('A4', 4., True), ('C5', 6., False)]]
        group = evidence.comparisons(rows, 'C5')[0]
        self.assertFalse(group['three_pairs_complete'])
        self.assertIsNone(group['candidate']['median'])
        self.assertEqual(group['paired_gains_pct'], [])


if __name__ == '__main__':
    unittest.main()
