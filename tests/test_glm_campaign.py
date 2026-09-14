import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
import glm_campaign as campaign


CHAMPION = '''export DS4_MODEL_PRIMARY_WEIGHT=10
export DS4_METAL_STREAMING_EXPERT_PREAD_THREADS=48
export DS4_GLM_ROUTER_LOOKAHEAD_PREFETCH=2
export DS4_METAL_GLM_STREAM_PRECOMMIT=2
export DS4_MODEL_FD_NOCACHE=1
export DS4_MODEL_REPLICA_PIECES_FD=2,1,1,1
'''


def fixture_plan(directory, phase='screen'):
    root = Path(directory)
    env = root / 'tools/harness/champion.env'
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(CHAMPION)
    result = campaign.plan(root, root / 'engine/ds4', phase)
    result['model_sources'] = []
    for n in range(5):
        path = root / f'model-{n}.gguf'
        path.write_bytes(b'fixture model')
        result['model_sources'].append(str(path))
    return result


def evidence(base, arm, text=b'consistent output', counts=True):
    env = arm['ds4_environment']
    weights = campaign.arm_weights(arm)
    stderr = '\n'.join('ds4: model replica: ' + item.rsplit('*', 1)[0] + ' weight=' + item.rsplit('*', 1)[1]
                       for item in env['DS4_MODEL_REPLICAS'].split(',')) + '\n'
    stderr += f'ds4: expert pread striping across {len(weights)} fds, {sum(weights)} weighted slots (primary weight {weights[0]}, mode=split-read)\n'
    stderr += f'ds4: F_NOCACHE set on {len(weights)} model fd(s)\n'
    stderr += 'ds4: expert pread per-fd sub-pieces: ' + env['DS4_MODEL_REPLICA_PIECES_FD'] + '\n'
    if counts:
        stderr += f'ds4: generation counts: generated={arm["tokens"]} requested={arm["tokens"]} decode_seconds={arm["tokens"]/5.2:.9f} prompt_tokens=6 prefill_seconds=2.000000000\n'
    summary = {'tag': arm['tag'], 'tokens_req': arm['tokens'], 'rc': 0, 'valid': 'ok', 'swap_growth_mb': 0,
               'ds4_gen_tps': 5.2, 'decode_tok_s': 5.15}
    base.with_suffix('.log').write_text(f'ARM {arm["tag"]} END 2026-09-12 12:00:00 rc=0\n' + json.dumps(summary) + '\n')
    base.with_suffix('.err').write_text(stderr)
    base.with_suffix('.txt').write_bytes(text)
    base.with_suffix('.capture.json').write_text(json.dumps({'rc': 0, 'chunks': arm['tokens'], 'first_byte_s': 4.2}))
    base.with_suffix('.sys').write_text('100 90 20 75 0 60 1 19\n100 90 20 75 0 60 1 19\n')
    base.with_suffix('.map').write_text(' '.join(f'{role}=disk{i}' for i,role in enumerate(campaign.ROLES)))
    base.with_suffix('.csv').write_text('t_s,dev,v1,v2,v3\n' + ''.join(f'{t},disk{i},{t*1000},1,0\n' for t in (0,1) for i in range(5)))


class CampaignTests(unittest.TestCase):
    def test_custom_geometry_and_cache_are_bound_to_launch_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = fixture_plan(tmp)
            arm = copy.deepcopy(plan['arms'][2])
            arm.update(layout='D5', weights=[9,6,6,3,3], cache_budget='70GB')
            env = arm['ds4_environment']
            env['DS4_MODEL_PRIMARY_WEIGHT'] = '9'
            env['DS4_MODEL_REPLICAS'] = ','.join(f'{item.rsplit("*", 1)[0]}*{w}' for item,w in zip(env['DS4_MODEL_REPLICAS'].split(','),[6,6,3,3]))
            runtime = {**plan, 'engine_sha256':'abc', 'engine_revision':'rev'}
            self.assertEqual(campaign.clean_environment(arm,runtime,Path(tmp)/'ds4',100)['GLM_CACHE'], '70GB')
            base = Path(tmp)/arm['tag']; evidence(base,arm)
            self.assertFalse(campaign.parse_verdict(base,arm,b'consistent output')['passed'])
            log = base.with_suffix('.log');log.write_text('CONFIG model=/model ctx=4096 cache=70GB full_layers=auto\n'+log.read_text())
            self.assertTrue(campaign.parse_verdict(base,arm,b'consistent output')['passed'])
            arm['weights'][0] = 10
            with self.assertRaises(ValueError):campaign.clean_environment(arm,runtime,Path(tmp)/'ds4',100)

    def test_unreviewed_weights_or_cache_cannot_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = fixture_plan(tmp)
            runtime = {**plan, 'engine_sha256':'abc', 'engine_revision':'rev'}
            arm = copy.deepcopy(plan['arms'][2]);arm['cache_budget']='110GB'
            with self.assertRaises(ValueError):campaign.clean_environment(arm,runtime,Path(tmp)/'ds4',100)
            for weights in ([0,5,5,2,2], [True,5,5,2,2], [60,5,5,2,2]):
                arm['weights']=weights
                with self.assertRaises(ValueError):campaign.arm_weights(arm)

    def test_plan_is_data_only_and_preserves_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = fixture_plan(tmp)
            planned = campaign.plan(tmp, Path(tmp) / 'engine/ds4', 'qualify', 'C5', 36, 4)
            self.assertEqual(len(planned['arms']), 12)
            self.assertEqual([a['layout'] for a in planned['arms'][:6]], ['A4','C5','C5','A4','A4','C5'])
            for arm in planned['arms']:
                env = arm['ds4_environment']
                self.assertEqual(env['DS4_METAL_STREAMING_EXPERT_PREAD_THREADS'], '48' if arm['layout']=='A4' else '36')
                self.assertEqual(env.get('DS4_MODEL_REPLICA_INFLIGHT'), None if arm['layout']=='A4' else '0,0,0,4,4')
            self.assertFalse((Path(tmp) / 'engine').exists())
            self.assertEqual([a['tokens'] for a in base['arms']], [60]*6)

    def test_literal_settings_never_execute_substitutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.env'
            for text in ['export DS4_X=$(touch /tmp/not-allowed)', 'export DS4_X="`command`"', 'export DS4_X=1; command']:
                path.write_text(text)
                with self.assertRaises(ValueError):
                    campaign.read_exports(path)

    def test_inherited_experiments_cannot_contaminate_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = fixture_plan(tmp)
            runtime = {**plan, 'engine_sha256': 'abc', 'engine_revision': 'rev'}
            with patch.dict(os.environ, {'DS4_ARGODRIVE_MAP': '/remote', 'DS4_METAL_DENSE_SOURCE': '/override', 'GLM_MTP':'1'}):
                env = campaign.clean_environment(plan['arms'][0], runtime, Path(tmp)/'ds4', 100)
            self.assertNotIn('DS4_ARGODRIVE_MAP', env)
            self.assertNotIn('DS4_METAL_DENSE_SOURCE', env)
            self.assertEqual(env['GLM_MTP'], '0')
            self.assertEqual(env['GLM_SKIP_MAP'], '0')

    def test_output_mismatch_is_a_hard_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm = fixture_plan(tmp)['arms'][1]
            base = Path(tmp)/arm['tag']
            evidence(base, arm, b'corrupted output')
            got = campaign.parse_verdict(base, arm, b'consistent output')
            self.assertFalse(got['passed'])
            self.assertIn('Generated text differs from the matched control.', got['failures'])

    def test_counts_missing_cannot_qualify_even_with_equal_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm = fixture_plan(tmp)['arms'][1]
            base = Path(tmp)/arm['tag']
            evidence(base, arm, counts=False)
            got = campaign.parse_verdict(base, arm, b'consistent output')
            self.assertTrue(got['passed'])
            self.assertFalse(got['publication_ready'])
            self.assertIsNone(got['actual_generated_tokens'])

    def test_precise_duration_cannot_round_a_sub_five_result_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm=fixture_plan(tmp)['arms'][1];base=Path(tmp)/arm['tag'];evidence(base,arm)
            log=base.with_suffix('.log')
            log.write_text(log.read_text().replace('"ds4_gen_tps": 5.2','"ds4_gen_tps": 5.0'))
            err=base.with_suffix('.err')
            err.write_text(err.read_text().replace(f'decode_seconds={arm["tokens"]/5.2:.9f}',f'decode_seconds={arm["tokens"]/4.999:.9f}'))
            got=campaign.parse_verdict(base,arm,b'consistent output')
            self.assertTrue(got['passed'])
            self.assertEqual(got['engine_reported_tok_s'],5.0)
            self.assertLess(got['engine_decode_tok_s'],5.0)

    def test_partial_replica_or_wrong_weights_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm = fixture_plan(tmp)['arms'][1]
            base = Path(tmp)/arm['tag']
            for before, after in [('across 5 fds','across 4 fds'), ('weight=4','weight=2'), ('on 5 model fd','on 4 model fd')]:
                evidence(base, arm)
                path = base.with_suffix('.err')
                path.write_text(path.read_text().replace(before, after))
                self.assertFalse(campaign.parse_verdict(base, arm, b'consistent output')['passed'])

    def test_summary_cannot_hide_missing_samples_or_swap_growth(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm = fixture_plan(tmp)['arms'][1]
            base = Path(tmp)/arm['tag']
            for extension in ('.sys','.map','.csv'):
                evidence(base, arm)
                base.with_suffix(extension).unlink()
                self.assertFalse(campaign.parse_verdict(base, arm, b'consistent output')['passed'])
            evidence(base, arm)
            base.with_suffix('.sys').write_text('100 90 20 75 0 60 1 19\n100 90 20 75 2 60 1 19\n')
            self.assertFalse(campaign.parse_verdict(base, arm, b'consistent output')['passed'])

    def test_three_reversed_pairs_required_and_target_is_not_rounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = fixture_plan(tmp, 'qualify')
            results = []
            for arm in plan['arms']:
                base = Path(tmp)/arm['tag']
                evidence(base, arm)
                result = campaign.parse_verdict(base, arm, b'consistent output')
                result['engine_decode_tok_s'] = 4 if arm['layout']=='A4' else 4.99
                results.append(result)
            report = campaign.qualification(plan, results)
            self.assertTrue(report['measurement_checks_passed'])
            self.assertFalse(report['publication_ready'])
            self.assertFalse(report['groups'][0]['target_5_met'])
            self.assertFalse(campaign.qualification(plan, results[:-1])['measurement_checks_passed'])
            for key, value in [('actual_generated_tokens',None), ('output_sha256',None), ('engine_decode_tok_s',float('nan')), ('pair',99)]:
                changed = copy.deepcopy(results)
                changed[1][key] = value
                self.assertFalse(campaign.qualification(plan, changed)['measurement_checks_passed'], key)

    def test_process_guard_fails_closed_and_recognizes_server(self):
        for code, text in [(2, ''), (0, ''), (0, '33 /path/ds4-server\n')]:
            with patch.object(campaign.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, text, '')):
                with self.assertRaises(RuntimeError):
                    campaign.process_guard()
        with patch.object(campaign.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '55 /usr/bin/python3\n', '')):
            campaign.process_guard()
        with patch.object(campaign.subprocess, 'run', side_effect=PermissionError('sandbox')):
            with self.assertRaisesRegex(RuntimeError, 'local Terminal'):
                campaign.process_guard()

    def test_live_guard_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = fixture_plan(tmp)
            out = Path(tmp)/'campaign'
            with patch.object(campaign, 'process_guard', side_effect=RuntimeError('engine active')):
                with self.assertRaisesRegex(RuntimeError, 'engine active'):
                    campaign.execute(plan, out)
            self.assertFalse(out.exists())

    def test_real_child_failure_stops_campaign_before_later_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = fixture_plan(tmp)
            out = Path(tmp)/'campaign'
            def snapshot(data, target):
                runtime_dir = target/'runtime'
                runtime_dir.mkdir()
                # This real child emits an engine failure rather than launching
                # inference. The wrapper must stop and retain its evidence.
                harness = runtime_dir/'harness.sh'
                harness.write_text('#!/bin/sh\nprintf "read failure\\n"\nexit 7\n')
                identity = {**data, 'engine_sha256':'fixture', 'engine_revision':'fixture',
                            'runtime_hashes':{'harness.sh':campaign.sha256(harness)}}
                return identity, harness, runtime_dir/'ds4'
            with patch.object(campaign, 'process_guard'), patch.object(campaign, 'snapshot', side_effect=snapshot):
                with self.assertRaisesRegex(RuntimeError, 'Arm failed'):
                    campaign.execute(plan, out, pressure_gb=0)
            state = json.loads((out/'campaign.json').read_text())
            self.assertEqual(state['status'], 'stopped')
            self.assertEqual(len(state['arms']), 1)
            self.assertFalse(state['arms'][0]['passed'])
            self.assertIn('read failure', (out/(plan['arms'][0]['tag']+'.runner.out')).read_text())
            self.assertFalse((out/(plan['arms'][1]['tag']+'.request.json')).exists())


if __name__ == '__main__':
    unittest.main()
