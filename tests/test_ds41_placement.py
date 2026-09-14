import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
from ds41_placement import expert_spans, parse_router_trace, recommend_placement, qualify_ab


class PlacementTests(unittest.TestCase):
    def test_parse_ds4_jsonl_routes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'routes.jsonl'
            path.write_text('\n'.join([
                json.dumps({'step': 0, 'layer': 1, 'ids': [2, 3, 2]}),
                json.dumps({'step': 0, 'layer': 2, 'ids': [3, 4]}),
            ]) + '\n')
            result = parse_router_trace(path)
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['steps'], 1)
        self.assertEqual(result['occurrences']['L1-E2'], 2)
        self.assertEqual(result['occurrences']['L2-E4'], 1)

    def test_parse_csv_id_columns(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'routes.csv'
            path.write_text('step,layer,id0,id1\n0,1,2,3\n1,1,2,\n')
            result = parse_router_trace(path)
        self.assertEqual(result['records'], 2)
        self.assertEqual(result['occurrences']['L1-E2'], 2)

    def test_expand_spans_and_rate_balancing(self):
        model = {'tensors': []}
        for component, offset in [('gate', 1000), ('up', 2000), ('down', 3000)]:
            model['tensors'].append({'name': f'blk.1.ffn_{component}_exps.weight',
                                     'experts': 3, 'component_bytes': 100, 'offset': offset})
        spans = expert_spans(model)
        self.assertEqual(spans['L1-E2']['components']['down'], {'offset': 3200, 'bytes': 100})
        placement = recommend_placement(spans, {'L1-E0': 10, 'L1-E1': 1, 'L1-E2': 1},
                                        {'internal': 10, 'Green': 5})
        self.assertEqual(placement['method'], 'Deltafin largest target-traffic deficit')
        self.assertEqual(sum(p['traffic_bytes'] for p in placement['placements']), 3600)
        self.assertGreater(placement['assigned_share']['internal'], placement['assigned_share']['Green'])

    def test_qualification_requires_identity_and_output(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.json'; b = Path(td) / 'b.json'
            base = {'prompt_tokens': 512, 'generated_tokens': 60, 'model_sha256': 'm',
                    'prompt_sha256': 'p', 'engine_sha256': 'e', 'output_sha256': 'o',
                    'generation_tok_s': 10, 'steady_tok_s': 11, 'prefill_tok_s': 20}
            cand = {**base, 'generation_tok_s': 12, 'steady_tok_s': 13}
            a.write_text(json.dumps(base)); b.write_text(json.dumps(cand))
            result = qualify_ab(a, b)
        self.assertTrue(result['qualified'])
        self.assertAlmostEqual(result['gains_percent']['steady_tok_s'], 100 * (13 / 11 - 1))


if __name__ == '__main__':
    unittest.main()
