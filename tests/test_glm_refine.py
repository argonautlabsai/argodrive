import copy
import hashlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
import glm_refine as refine


class RefinementTests(unittest.TestCase):
    def base(self):
        sources = ['/internal/model', '/green/model', '/white/model', '/yellow/model', '/blue/model']
        original = {'layout': 'A4', 'tokens': 60, 'ds4_environment': {'DS4_MODEL_PRIMARY_WEIGHT': '10',
                    'DS4_MODEL_REPLICAS': '/green/model*5,/white/model*5,/yellow/model*4',
                    'DS4_MODEL_REPLICA_PIECES_FD': '2,1,1,1', 'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS': '48'}}
        current = refine.replace_layout(original, 'C5', sources)
        current['cache_budget'] = 'auto'
        return {'model_sources': sources, 'arms': [original, current]}, current

    def test_allocation_conserves_bytes_and_shared_hub_budget(self):
        base, current = self.base()
        before = copy.deepcopy(current)
        d = refine.replace_layout(current, 'D5', base['model_sources'])
        e = refine.replace_layout(current, 'E5', base['model_sources'])
        self.assertEqual(d['actual_blocks'], [9, 6, 6, 3, 3])
        self.assertEqual(e['actual_blocks'], [9, 7, 7, 2, 2])
        self.assertEqual(sum(sum(row) for row in d['piece_bytes']), 7077888)
        self.assertEqual(d['piece_bytes'][0], [4*262144, 5*262144])
        self.assertEqual(current, before)

    def test_experiments_change_candidates_only(self):
        base, current = self.base()
        planned, candidate = refine.paired_plan(base, current, refine.EXPERIMENTS[2])
        self.assertEqual([a['setting_role'] for a in planned['arms']], ['control', 'candidate', 'candidate', 'control'])
        self.assertEqual([a['cache_budget'] for a in planned['arms']], ['auto', '70GB', '70GB', 'auto'])
        self.assertEqual({a['tokens'] for a in planned['arms']}, {512})
        self.assertEqual(current['cache_budget'], 'auto')

    def results(self, rates):
        base, current = self.base()
        planned, _ = refine.paired_plan(base, current, refine.EXPERIMENTS[0])
        arms = [{'tag': arm['tag'], 'passed': True, 'actual_generated_tokens': arm['tokens'],
                 'engine_decode_seconds': arm['tokens']/rate, 'output_sha256': 'a'*64}
                for arm, rate in zip(planned['arms'], rates)]
        return planned, {'status': 'complete', 'arms': arms}

    def test_median_gain_cannot_hide_regressing_pair(self):
        planned, result = self.results([4., 4.5, 4.2, 4.3])
        self.assertFalse(refine.decide(planned, result)['adopt'])
        planned, result = self.results([4., 4.2, 4.3, 4.1])
        self.assertTrue(refine.decide(planned, result)['adopt'])

    def test_incomplete_changed_output_and_nonfinite_timers_reject(self):
        planned, result = self.results([4., 5., 5., 4.])
        for edit in ('incomplete', 'text', 'timer', 'order'):
            changed = copy.deepcopy(result)
            if edit == 'incomplete': changed['arms'].pop()
            elif edit == 'text': changed['arms'][1]['output_sha256'] = 'b'*64
            elif edit == 'timer': changed['arms'][1]['engine_decode_seconds'] = float('nan')
            else: changed['arms'].reverse()
            self.assertFalse(refine.decide(planned, changed)['adopt'], edit)

    def test_qualification_restores_original_four_drive_control(self):
        base, current = self.base()
        current = refine.replace_layout(current, 'D5', base['model_sources'])
        current.update(cache_budget='70GB')
        current['ds4_environment']['DS4_METAL_Q8_MV_NSG'] = '2'
        plan = refine.qualification_plan(base, current)
        self.assertEqual(len(plan['arms']), 12)
        for arm in plan['arms']:
            if arm['layout'] == 'A4':
                self.assertNotIn('cache_budget', arm)
                self.assertNotIn('DS4_METAL_Q8_MV_NSG', arm['ds4_environment'])
                self.assertEqual(arm['ds4_environment']['DS4_MODEL_REPLICA_PIECES_FD'], '2,1,1,1')
            else:
                self.assertEqual(arm['layout'], 'D5')
                self.assertEqual(arm['cache_budget'], '70GB')

    def test_bounded_controller_completes_with_synthetic_executor(self):
        # This exercises orchestration only: no sampler, model, GPU or speed
        # measurement is created. All results live in a temporary test folder.
        for changed_text in (False, True):
            with self.subTest(changed_text=changed_text), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); previous = root/'previous'; previous.mkdir()
                for n in (128,512): (previous/f'reference-{n}.txt').write_bytes(b'fixture')
                first = root/'previous--01-layout-screen'; first.mkdir()
                (first/'screen-01-A4-60.txt').write_bytes(b'fixture')
                base, current = self.base()
                state0 = {'input_hashes': {}, 'historical_output_references': {str(n):{'sha256':hashlib.sha256(b'fixture').hexdigest()} for n in (128,512)}}
                seen = []
                def execute(plan, directory, pressure, timeout, allow, deadline, refs):
                    self.assertEqual(pressure,100);self.assertEqual(timeout,600);self.assertTrue(allow)
                    self.assertEqual(set(refs),{60,128,512})
                    directory.mkdir()
                    rows = []
                    for arm in plan['arms']:
                        seen.append(arm)
                        mismatch = changed_text and plan.get('experiment') == 'q8-nsg-2' and arm.get('setting_role') == 'candidate'
                        rate = 4.1
                        rows.append({'tag':arm['tag'],'tokens':arm['tokens'],'actual_generated_tokens':arm['tokens'],
                                     'layout':arm['layout'],'pair':arm['pair'],'passed':not mismatch,
                                     'output_sha256':('b' if mismatch else 'a')*64,'engine_decode_tok_s':rate,
                                     'engine_decode_seconds':arm['tokens']/rate,
                                     'failures':['Generated text differs from the matched control.'] if mismatch else []})
                        if mismatch:break
                    state = {'status':'stopped' if mismatch else 'complete','arms':rows}
                    (directory/'campaign.json').write_text(json.dumps(state))
                    if mismatch:raise RuntimeError('Arm failed: output mismatch')
                with patch.object(refine,'prepare',return_value=(state0,base,current)), \
                     patch.object(refine.campaign,'process_guard'), patch.object(refine.campaign,'execute',side_effect=execute):
                    state = refine.run(previous,root/'out',datetime.now(timezone.utc)+timedelta(hours=3),True)
                self.assertEqual(state['status'],'complete')
                self.assertTrue(state['qualification']['measurement_checks_passed'])
                self.assertFalse(state['publication_ready'])
                self.assertFalse(state['target_5_met'])
                self.assertEqual(len(seen),30 if changed_text else 32)
                self.assertEqual(state['selection']['layout'],'C5')
                self.assertEqual(state['candidate_cache_budget'],'auto')


if __name__ == '__main__': unittest.main()
