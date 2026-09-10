"""Product contracts: data provenance, source switching and local API boundaries."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from test_monitor import dashboard


def log(path, tag='test'):
    path.write_text(f'ARM {tag} START 2026-09-10 10:00:00 tokens=11\n'
                    'CONFIG model=glm.gguf ctx=4096 raw=1 temp=0 think=nothink\n'
                    'DS4_ENV DS4_STREAM_THREADS=48 DS4_STREAM_PREFETCH=2\n'
                    'ENGINE /engine/ds4 abc123\nPROMPT [A test prompt]\n'+
                    json.dumps({'tag':tag,'decode_tok_s':2,'chunks':11,'first_byte_s':6,'gen_s':5,
                                'gen_window_gb_per_tok':4.7,'swap_growth_mb':0,'hit_rate':.6,'valid':'ok','rc':0})+'\n')


class ProductTests(unittest.TestCase):
    def test_ds4_detail_preserves_settings_and_measurement_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'block';p.mkdir();log(p/'test.log')
            with patch.object(dashboard,'SOAK',tmp):
                d=json.loads(dashboard.arm_detail('block','test'))
            self.assertEqual(d['config']['ctx'],'4096')
            self.assertEqual(d['environment']['DS4_STREAM_THREADS'],'48')
            self.assertEqual(d['engine_build'],'/engine/ds4 abc123')
            self.assertEqual(d['prompt'],'A test prompt')
            self.assertEqual(d['row']['summary']['gen_window_gb_per_tok'],4.7)
            self.assertEqual(d['row']['summary']['swap_growth_mb'],0)
            self.assertFalse(d['row']['artifacts']['storage'])
            self.assertNotIn('ssd_tot_mean',d['row'])

    def test_new_artifact_invalidates_cached_row_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'block';p.mkdir();log(p/'test.log')
            with patch.object(dashboard,'SOAK',tmp):
                first=dashboard._stats_blocks()[0]['rows'][0]
                self.assertNotIn('output_hash',first)
                (p/'test.md5').write_text('abcdefabcdefabcdefabcdefabcdefab\n')
                # Copied artifacts can retain timestamps older than the run log.
                os.utime(p/'test.md5',(100,100))
                second=dashboard._stats_blocks()[0]['rows'][0]
            self.assertEqual(second['output_hash'],'abcdefabcdefabcdefabcdefabcdefab')
            self.assertTrue(second['artifacts']['output_hash'])

    def test_validation_does_not_change_source_and_apply_persists_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve();(p/'runs'/'block').mkdir(parents=True);log(p/'runs'/'block'/'test.log')
            with patch.object(dashboard,'LOCAL_SETTINGS',p/'argodrive.local.json'), patch.object(dashboard,'SOAK','previous'), patch.object(dashboard,'SAVED_SETTINGS',{}):
                result=dashboard.apply_source(str(p/'runs'),True)
                self.assertEqual(result['logs'],1)
                self.assertFalse((p/'argodrive.local.json').exists())
                self.assertEqual(dashboard.SOAK,'previous')
                dashboard.apply_source(str(p/'runs'))
                self.assertEqual(dashboard.SOAK,str(p/'runs'))
                self.assertEqual(json.loads((p/'argodrive.local.json').read_text())['runs'],str(p/'runs'))
                self.assertIsNone(dashboard._stats_cache['key'])
                with self.assertRaises(ValueError): dashboard.apply_source(str(p/'missing'))
                self.assertEqual(dashboard.SOAK,str(p/'runs'))

    def test_source_folder_mistake_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'block';p.mkdir();log(p/'test.log')
            with self.assertRaisesRegex(ValueError,'Choose its parent'):dashboard.source_info(str(p))

    def test_local_settings_failure_paths_through_http(self):
        server=dashboard.http.server.ThreadingHTTPServer(('127.0.0.1',0),dashboard.H)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.server_address[1]
        def post(headers,body):
            c=http.client.HTTPConnection('127.0.0.1',port)
            c.request('POST','/settings',body,headers)
            r=c.getresponse();r.read();status=r.status;c.close();return status
        try:
            with tempfile.TemporaryDirectory() as tmp:
                origin=f'http://127.0.0.1:{port}'
                token=dashboard.SETTINGS_TOKEN
                with patch.object(dashboard,'LOCAL_SETTINGS',Path(tmp)/'local.json'),patch.object(dashboard,'SOAK','before'),patch.object(dashboard,'SAVED_SETTINGS',{}):
                    self.assertEqual(post({'Origin':origin},json.dumps({'runs':tmp})),403)
                    self.assertEqual(post({'Host':'untrusted.example','Origin':'http://untrusted.example','X-Argodrive-Token':token},json.dumps({'runs':tmp})),403)
                    self.assertEqual(post({'Origin':'https://other.example','X-Argodrive-Token':token},json.dumps({'runs':tmp})),403)
                    headers={'Origin':origin,'X-Argodrive-Token':token,'Content-Type':'application/json'}
                    self.assertEqual(post(headers,'bad JSON'),400)
                    self.assertEqual(dashboard.SOAK,'before')
                    self.assertEqual(post(headers,json.dumps({'runs':tmp,'validate_only':True})),200)
                    self.assertEqual(dashboard.SOAK,'before')
                    self.assertEqual(post(headers,json.dumps({'runs':tmp})),200)
                    self.assertEqual(dashboard.SOAK,str(Path(tmp).resolve()))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
