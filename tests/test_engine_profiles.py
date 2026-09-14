import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from test_monitor import dashboard
from engine_profiles import draft_shape

FIXTURE = Path(__file__).parent/'fixtures/ds4-draft.json'

class EngineProfilesTests(unittest.TestCase):
    def test_draft_shape_accepts_incomplete_data_not_commands_or_unbounded_sources(self):
        draft=json.loads(FIXTURE.read_text());draft_shape(draft)
        for changed in [dict(draft,command='run'),dict(draft,engine='glm'),dict(draft,sources=[]),
                        dict(draft,cold=1),dict(draft,threads={'value':48}),dict(draft,buildId='a\nb')]:
            with self.assertRaises(ValueError):draft_shape(changed)

    def test_save_preserves_run_folder_and_failure_preserves_previous_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'settings.json';draft=json.loads(FIXTURE.read_text())
            with patch.object(dashboard,'LOCAL_SETTINGS',file),patch.object(dashboard,'SAVED_SETTINGS',{'runs':'existing'}),patch.object(dashboard,'SOAK','existing'):
                result=dashboard.save_engine_draft(draft)
                self.assertFalse(result['applied_to_engine'])
                self.assertEqual(json.loads(file.read_text()),{'runs':'existing','engine_draft':draft})
                self.assertEqual(dashboard.SOAK,'existing')
                before=file.read_bytes()
                with self.assertRaises(ValueError):dashboard.save_engine_draft({'engine':'arbitrary'})
                self.assertEqual(before,file.read_bytes())
                with patch.object(Path,'replace',side_effect=PermissionError('test failure')):
                    with self.assertRaises(PermissionError):dashboard.save_engine_draft(dict(draft,threads='12'))
                self.assertEqual(dashboard.SAVED_SETTINGS['engine_draft'],draft)
                self.assertEqual(before,file.read_bytes())

    def test_http_save_requires_origin_and_token_and_never_starts_an_engine(self):
        server=dashboard.http.server.ThreadingHTTPServer(('127.0.0.1',0),dashboard.H)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.server_address[1];origin=f'http://127.0.0.1:{port}'
        draft=json.loads(FIXTURE.read_text());body=json.dumps({'engine_draft':draft})
        def post(headers,payload=body):
            c=http.client.HTTPConnection('127.0.0.1',port);c.request('POST','/settings',payload,headers)
            r=c.getresponse();result=(r.status,r.read());c.close();return result
        try:
            with tempfile.TemporaryDirectory() as tmp,patch.object(dashboard,'LOCAL_SETTINGS',Path(tmp)/'settings.json'),patch.object(dashboard,'SAVED_SETTINGS',{}),patch.object(dashboard.subprocess,'run',side_effect=AssertionError('No engine may run')) as runner:
                headers={'Origin':origin,'X-Argodrive-Token':dashboard.SETTINGS_TOKEN,'Content-Type':'application/json'}
                self.assertEqual(post({'Origin':origin})[0],403)
                self.assertEqual(post(dict(headers,Origin='https://other.example'))[0],403)
                self.assertEqual(post(headers,json.dumps({'engine_draft':draft,'runs':'anything'}))[0],400)
                status,data=post(headers);self.assertEqual(status,200)
                self.assertFalse(json.loads(data)['applied_to_engine']);runner.assert_not_called()
                self.assertEqual(dashboard.settings_payload()['engine_draft'],draft)
        finally:server.shutdown();server.server_close();thread.join()
