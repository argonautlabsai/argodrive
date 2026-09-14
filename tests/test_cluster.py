import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_monitor import dashboard
from cluster_state import cluster_snapshot


class ClusterTests(unittest.TestCase):
    def test_bundled_reference_is_never_live_or_engine_enabled(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);bundle=p/'bundle';bundle.mkdir()
            (bundle/'cluster-evidence.json').write_text(json.dumps({'schema':1,'live':True,'engine_integrated':True,
                'token':'secret','node':{'name':'M1','secret':'secret'},'s1':{'verified':1000,'password':'secret'}}))
            d=cluster_snapshot(p,bundle)
            self.assertEqual(d['source'],'bundled_reference')
            self.assertFalse(d['live']);self.assertFalse(d['engine_integrated'])
            self.assertNotIn('secret',json.dumps(d))
            self.assertEqual(d['node'],{'name':'M1'})

    def test_invalid_local_report_is_visible_and_never_silently_replaced(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'wire').mkdir();local=p/'wire/status.json'
            for text in ('invalid','{"schema":2}','{"schema":1,"s1":{"transfer_gbs":NaN}}'):
                local.write_text(text);d=cluster_snapshot(p,p)
                self.assertEqual(d['source'],'local_session');self.assertIn('error',d)
                self.assertEqual(d['s1'],{})

    def test_local_shapes_are_bounded_and_types_tolerated(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'wire').mkdir()
            (p/'wire/status.json').write_text(json.dumps({'schema':1,'node':False,'gates':'wrong','s0':[],'ssd_probe':None}))
            d=cluster_snapshot(p,p)
            self.assertEqual(d['node'],{});self.assertEqual(d['gates'],[])
            self.assertEqual(d['s0']['scenarios'],[])

    def test_network_and_expert_evidence_excludes_private_fields(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'wire').mkdir()
            (p/'wire/status.json').write_text(json.dumps({'schema':1,
                'network':{'independent_links':2},
                'network_test':{'cases':[{'name':'AB','wall_gbs':9.34,'secret':'private'}]},
                'expert_test':{'cases':[{'verified':2000,'wall_gbs':2.71,'secret':'private'}]},
                'inference_test':{'change_percent':-9.4,'cases':[{'tag':'A1','decode_tok_s':1.97,'secret':'private'}]},
                'pipeline_test':{'verified':25000,'profiles':[{'name':'Cache','cache_hit_rate':1,'secret':'private'}]}}))
            d=cluster_snapshot(p,p)
            self.assertEqual(d['network']['independent_links'],2)
            self.assertEqual(d['network_test']['cases'][0]['wall_gbs'],9.34)
            self.assertEqual(d['expert_test']['cases'][0]['verified'],2000)
            self.assertEqual(d['pipeline_test']['verified'],25000)
            self.assertEqual(d['inference_test']['change_percent'],-9.4)
            self.assertFalse(d['engine_integrated'])
            self.assertNotIn('private',json.dumps(d))


if __name__=='__main__':unittest.main()
