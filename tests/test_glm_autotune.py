import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_glm_campaign import fixture_plan
import glm_autotune as tune


def results(plan, rates):
    return {'status':'complete','arms':[
        {'tag':a['tag'],'layout':a['layout'],'tokens':a['tokens'],'pair':a['pair'],
         'passed':True,'actual_generated_tokens':a['tokens'],
         'engine_decode_seconds':a['tokens']/rate,
         'engine_decode_tok_s':rate,'output_sha256':'a'*64}
        for a,rate in zip(plan['arms'],rates)]}


class AutotuneTests(unittest.TestCase):
    def test_layout_tie_preserves_existing_hub_share(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder)
            d=tune.choose_layout(p,results(p,[5,4,3.99,4.01,4.01,5]))
            self.assertEqual(d['layout'],'C5')
            self.assertLess(d['speedup_vs_A4_pct'],0)
            self.assertFalse(d['promoted'])

    def test_screening_failures_cannot_choose_a_winner(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder)
            for field,value in [('passed',False),('engine_decode_tok_s',float('nan')),
                                ('actual_generated_tokens',None),('output_sha256','b'*64)]:
                state=results(p,[5]*6);state['arms'][1][field]=value
                with self.assertRaises(ValueError):tune.choose_layout(p,state)

    def test_tuning_changes_only_candidate_dimension_and_reverses_order(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder);old=copy.deepcopy(p)
            settings=p['arms'][2]['ds4_environment']
            planned=tune.tuning_plan(p,'C5',settings,'workers-36',{'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS':'36'})
            self.assertEqual([a['setting_role'] for a in planned['arms']],['control','candidate','candidate','control'])
            for arm in planned['arms']:
                expected={**settings,**({'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS':'36'} if arm['setting_role']=='candidate' else {})}
                self.assertEqual(arm['ds4_environment'],expected)
            self.assertEqual(p,old)

    def test_noise_and_any_pair_regression_reject_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder);p=tune.tuning_plan(p,'B5',p['arms'][1]['ds4_environment'],'test',{})
            for rates,adopt in [([5,5.05,5.05,5],False),([5,5.5,4.9,5],False),([5,5.2,5.2,5],True)]:
                got=tune.tuning_decision(p,results(p,rates))
                self.assertEqual(got['adopt_for_next_test'],adopt)
                self.assertFalse(got['promoted'])

    def test_final_keeps_a4_and_qualifies_both_lengths(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder);settings={**p['arms'][1]['ds4_environment'],'DS4_METAL_STREAMING_EXPERT_PREAD_THREADS':'36'}
            q=tune.final_plan(p,'B5',settings)
            self.assertEqual(len(q['arms']),12)
            for arm in q['arms']:
                self.assertEqual(arm['ds4_environment'],p['arms'][0]['ds4_environment'] if arm['layout']=='A4' else settings)
            state=results(q,[4.8 if a['layout']=='A4' else 4.99 for a in q['arms']])
            got=tune.campaign.qualification(q,state['arms'])
            self.assertTrue(got['measurement_checks_passed'])
            self.assertFalse(any(g['target_5_met'] for g in got['groups']))

    def test_deadline_stops_without_starting_an_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder);out=Path(folder)/'out'
            with patch.object(tune.campaign,'process_guard'),patch.object(tune.campaign,'sha256',return_value='fixture'),patch.object(tune.campaign,'execute') as execute:
                with self.assertRaisesRegex(RuntimeError,'Insufficient time'):
                    tune.run(p,out,datetime.now(timezone.utc)+timedelta(seconds=30))
                execute.assert_not_called()
            state=json.loads((out/'autotune.json').read_text())
            self.assertEqual(state['status'],'stopped')
            self.assertFalse(state['target_5_met'])
            self.assertTrue((out/'REPORT.md').exists())

    def test_complete_controller_keeps_control_and_stores_all_34_arms(self):
        with tempfile.TemporaryDirectory() as folder:
            p=fixture_plan(folder);out=Path(folder)/'results'
            for name in [p['engine'],*[str(Path(p['project'])/f) for f in tune.campaign.HARNESS_FILES]]:
                path=Path(name);path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_text('fixture only')
            calls=[]
            def execute(planned,directory,*args):
                directory.mkdir()
                if planned['phase']=='screen':rates=[{'A4':4,'B5':4.1,'C5':4.3}[a['layout']] for a in planned['arms']]
                elif planned['phase']=='tune':rates=[4.3 if a['setting_role']=='control' else 4.5 for a in planned['arms']]
                else:rates=[4 if a['layout']=='A4' else 5.1 for a in planned['arms']]
                (directory/'campaign.json').write_text(json.dumps(results(planned,rates)))
                calls.append(copy.deepcopy(planned))
            with patch.object(tune.campaign,'process_guard'),patch.object(tune.campaign,'execute',side_effect=execute):
                state=tune.run(p,out,datetime.now(timezone.utc)+timedelta(hours=2))
            self.assertEqual(sum(len(c['arms']) for c in calls),34)
            self.assertEqual(state['status'],'complete')
            self.assertTrue(state['target_5_met'])
            self.assertFalse(state['target_6_met'])
            self.assertFalse(state['publication_ready'])
            self.assertTrue(all(Path(stage['path']).parent==out.parent.resolve() for stage in state['stages']))
            for arm in calls[-1]['arms']:
                if arm['layout']=='A4':self.assertEqual(arm['ds4_environment'],p['arms'][0]['ds4_environment'])


if __name__=='__main__':unittest.main()
