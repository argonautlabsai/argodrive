"""No inference: sentinel-file checks for app-owned output and sampler lifecycle."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'monitor'))
from test_runner import atomic, verify_harness, HARNESS_FILES, digest

class FileSafetyTests(unittest.TestCase):
    def test_settings_aliases_leave_user_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); user=root/'user-document';user.write_bytes(b'keep exactly')
            alias=root/'settings.json';alias.symlink_to(user)
            with self.assertRaises(ValueError): atomic(alias, {'changed':True})
            self.assertEqual(user.read_bytes(),b'keep exactly');self.assertTrue(alias.is_symlink())
            hard=root/'hard.json';os.link(user,hard)
            with self.assertRaises(ValueError): atomic(hard, {'changed':True})
            self.assertEqual(user.read_bytes(),b'keep exactly')
    def test_old_temporary_name_is_never_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'settings.json';other=root/'user';other.write_text('untouched')
            (root/'settings.tmp').symlink_to(other)
            (root/'settings.json.tmp').symlink_to(other)
            atomic(target,{'version':1});atomic(target,{'version':2})
            self.assertEqual(json.loads(target.read_text()),{'version':2})
            self.assertEqual(other.read_text(),'untouched')
    def test_sampler_restart_does_not_stop_inference(self):
        tree=ast.parse((ROOT/'monitor/k3-live.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_reap_scopes','_shutdown_children')]
        runner=Mock();sampler=Mock();sampler.poll.return_value=None
        ns={'_SCOPE':[sampler],'test_runner':runner,'subprocess':subprocess}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<lifecycle>','exec'),ns)
        ns['_reap_scopes']();sampler.terminate.assert_called_once();runner.shutdown.assert_not_called()
        ns['_shutdown_children']();runner.shutdown.assert_called_once()
    def test_changed_helper_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for rel in HARNESS_FILES:
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reviewed helper')
            profile={'project':str(root),'harness_sha256':{rel:digest(root/rel) for rel in HARNESS_FILES}}
            verify_harness(profile)
            (root/HARNESS_FILES[0]).write_text('changed helper')
            with self.assertRaisesRegex(ValueError,'helper changed'):verify_harness(profile)
    @unittest.skipUnless(os.environ.get('ARGODRIVE_SAFETY_SAMPLER'), 'Set compiled sampler path for native sentinel checks')
    def test_native_sampler_refuses_existing_file_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'user-document';target.write_bytes(b'preserve this document')
            alias=root/'alias.csv';alias.symlink_to(target)
            binary=os.environ['ARGODRIVE_SAFETY_SAMPLER']
            def run(path):return subprocess.run([binary,'200','1',str(path),'disk0'],capture_output=True,text=True,timeout=10)
            for path in (target,alias):
                result=run(path)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('File exists',result.stderr)
                self.assertEqual(target.read_bytes(),b'preserve this document')
            self.assertTrue(alias.is_symlink())
            fresh=root/'new.csv';result=run(fresh)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(fresh.read_text().startswith('t_s,dev,v1,v2,v3\n'))
            self.assertEqual(fresh.stat().st_mode & 0o777,0o600)

if __name__=='__main__':unittest.main()
