import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'monitor'))
import ds41_benchmark as b


class ShaderIdentityTests(unittest.TestCase):
    def test_shader_edit_after_plan_rejects_before_any_child(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            engine = root/'ds4-bench'
            engine.write_bytes(b'fixture binary')
            engine.chmod(0o700)
            prompt = root/'prompt.txt'
            prompt.write_text('fixture prompt')
            model = root/'model.gguf'
            model.write_bytes(b'fixture model')
            shader = root/'metal/subdir/test.metal'
            shader.parent.mkdir(parents=True)
            shader.write_text('original shader')
            model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
            with patch.object(b, 'DS41_Q4_BYTES', model.stat().st_size), \
                 patch.object(b, 'DS41_Q4_SHA256', model_hash):
                p = b.plan(engine, model, prompt, root/'arm')
                shader.write_text('changed shader')
                # This fixture never launches a child. Avoid taking the real
                # inference campaign lock during a pure admission-path test.
                with patch.object(b.fcntl, 'flock'), \
                     patch.object(b, 'process_guard'), \
                     patch.object(b.subprocess, 'Popen') as child:
                    with self.assertRaisesRegex(ValueError, 'Runtime Metal sources changed'):
                        b.run(p)
                    child.assert_not_called()
                    self.assertFalse((root/'arm').exists())

    def test_new_and_removed_runtime_sources_change_identity(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'metal').mkdir()
            engine = root/'engine'
            original = b.shader_identity(engine)
            shader = root/'metal/added.metal'
            shader.write_text('fixture')
            added = b.shader_identity(engine)
            self.assertNotEqual(original, added)
            self.assertEqual(set(added), {'added.metal'})
            shader.unlink()
            self.assertEqual(original, b.shader_identity(engine))


if __name__ == '__main__':
    unittest.main()
