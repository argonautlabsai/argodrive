import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'monitor'))
from campaign_monitor import CampaignMonitor, read_campaign, object_file


def fixture(root, rate=4.2, running=False):
    directory=root/'argodrive-autotune-fixture';directory.mkdir()
    stage=root/(directory.name+'--06-qualification');stage.mkdir()
    arms=[]
    for n in (128,512):
        for pair in (1,2,3):
            for side in ('A4','C5'):
                speed=4. if side=='A4' else rate
                arms.append({'tag':f'{side}-{n}-{pair}','layout':side,'tokens':n,'actual_generated_tokens':n,
                             'pair':pair,'engine_decode_seconds':n/speed,'engine_decode_tok_s':speed,
                             'passed':True,'output_sha256':str(n%10)*64})
    state={'status':'running' if running else 'complete','active_stage':'06-qualification' if running else None,
           'stages':[] if running else [{'name':'06-qualification'}],
           'selection':{'layout':'C5'},'qualification':{'measurement_checks_passed':True},
           'target_5_met':True, # UI must recompute rather than trusting this.
           'historical_output_references':{str(n):{'sha256':str(n%10)*64} for n in (128,512)},
           'candidate_environment':{'DS4_MODEL_PRIMARY_WEIGHT':'10'},'tuning':[]}
    (directory/'autotune.json').write_text(json.dumps(state))
    (stage/'campaign.json').write_text(json.dumps({'status':state['status'],'arms':arms}))
    (stage/'plan.json').write_text(json.dumps({'arms':[{'tag':a['tag']} for a in arms]}))
    (stage/'runtime.json').write_text(json.dumps({'engine_sha256':'a'*64}))
    return directory,stage,arms,state


class CampaignMonitorTests(unittest.TestCase):
    def test_complete_pairs_are_measured_but_not_publication_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,_,_,_=fixture(root)
            before=set(root.rglob('*'))
            c=read_campaign(directory,root)
            self.assertTrue(c['recorded_qualification_passed'])
            self.assertFalse(c['target_5_met'])
            self.assertFalse(c['publication_ready'])
            self.assertEqual(c['completed_arms'],12)
            self.assertAlmostEqual(c['groups'][1]['median_gain_pct'],5.)
            self.assertEqual(set(root.rglob('*')),before)

    def test_running_or_incomplete_campaign_cannot_hit_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,stage,arms,state=fixture(root,rate=5.4,running=True)
            self.assertFalse(read_campaign(directory,root)['target_5_met'])
            state.update(status='complete',active_stage=None,stages=[{'name':'06-qualification'}])
            (directory/'autotune.json').write_text(json.dumps(state))
            self.assertTrue(read_campaign(directory,root)['target_5_met'])
            (stage/'campaign.json').write_text(json.dumps({'arms':arms[:-1]}))
            self.assertFalse(read_campaign(directory,root)['target_5_met'])

    def test_bad_count_hash_order_or_duration_rejects_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,stage,arms,state=fixture(root,rate=5.4)
            for kind in ('count','hash','order','duration','duplicate_pair'):
                changed=copy.deepcopy(arms)
                if kind=='count':changed[-1]['actual_generated_tokens']=511
                elif kind=='hash':changed[-1]['output_sha256']='e'*64
                elif kind=='order':changed.reverse()
                elif kind=='duration':changed[-1]['engine_decode_seconds']=1.
                else:changed[-1]['pair']=2
                (stage/'campaign.json').write_text(json.dumps({'arms':changed}))
                self.assertFalse(read_campaign(directory,root)['recorded_qualification_passed'],kind)

    def test_artifacts_cannot_escape_root_through_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root=Path(tmp);secret=Path(outside)/'external.json';secret.write_text('{"private":true}')
            (root/'link.json').symlink_to(secret)
            with self.assertRaises(ValueError):object_file(root/'link.json',root)

    def test_monitor_handles_malformed_metadata_and_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root=Path(tmp);directory,_,_,_=fixture(root)
            monitor=CampaignMonitor()
            first=monitor.snapshot(root);self.assertEqual(len(first['campaigns']),1)
            self.assertIs(monitor.snapshot(root),first)
            self.assertEqual(monitor.snapshot(other)['campaigns'],[])
            (directory/'autotune.json').write_text('{"selection":[]}')
            bad=monitor.snapshot(root)
            self.assertEqual(bad['campaigns'],[])
            self.assertEqual(len(bad['errors']),1)

    def test_monitor_is_bounded_and_nonfinite_numbers_do_not_serialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory,_,_,state=fixture(root)
            state['tuning']=[{'experiment':'test','median_gain_pct':float('nan')}]
            (directory/'autotune.json').write_text(json.dumps(state))
            data=CampaignMonitor().snapshot(root)
            self.assertIsNone(data['campaigns'][0]['tuning'][0]['gain_pct'])
            json.dumps(data,allow_nan=False)
            (root/'big.json').write_bytes(b' ' * 2_000_001)
            with self.assertRaises(ValueError):object_file(root/'big.json',root)


if __name__ == '__main__':unittest.main()
