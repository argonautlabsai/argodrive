import json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'monitor'))
from task_recorder import TaskRecorder
from harness_monitor import HarnessMonitor
class TaskTests(unittest.TestCase):
 def test_no_sampler_task_is_visible_without_fake_tokens(self):
  with tempfile.TemporaryDirectory() as t:
   r=TaskRecorder(Path(t)/'build','Compile engine','build');r.update(phase='Compiling')
   data=HarnessMonitor().snapshot(t)
   self.assertEqual(data['kind'],'task');self.assertEqual(data['state'],'Build · running')
   self.assertIsNone(data['response_rate']);self.assertEqual(data['summary'],{})
   self.assertEqual(data['arm'],'Compile engine');self.assertEqual(data['devices'],[])
   r.finish('failed','compiler exit 1');data=HarnessMonitor().snapshot(t)
   self.assertTrue(data['done']);self.assertEqual(data['state'],'Build · failed')
   self.assertEqual(data['task']['detail'],'compiler exit 1')
 def test_samples_survive_completion_and_keep_actual_intervals(self):
  with tempfile.TemporaryDirectory() as t:
   r=TaskRecorder(Path(t)/'check','Recovery','correctness')
   r.base.with_suffix('.map').write_text('internal=disk0 Green=disk2\n')
   r.base.with_suffix('.csv').write_text('t_s,dev,v1,v2,v3\n0,disk0,0,0,0\n0,disk2,0,0,0\n0.2,disk0,200000000,0,0\n0.2,disk2,100000000,0,0\n')
   r.finish('completed');d=HarnessMonitor().snapshot(t)
   self.assertEqual(d['read_windows']['TOTAL'][0][1],1.5)
   self.assertEqual(d['read_windows']['internal'][0][2],.2)
   self.assertFalse(d['fresh']);self.assertIsNone(d['generated_tokens'])
 def test_close_only_terminates_owned_sampler(self):
  with tempfile.TemporaryDirectory() as t:
   r=TaskRecorder(Path(t)/'check','Recovery');proc=Mock();proc.poll.return_value=None
   with patch('task_recorder.subprocess.Popen',return_value=proc) as popen:
    r.start_sampler('/trusted/sampler',{'internal':'disk0'},20);r.close()
    self.assertEqual(popen.call_args.args[0][0],'/trusted/sampler');proc.terminate.assert_called_once();proc.wait.assert_called_once()
 def test_sampler_failure_keeps_terminal_task_record(self):
  with tempfile.TemporaryDirectory() as t:
   r=TaskRecorder(Path(t)/'check','Recovery')
   with patch('task_recorder.subprocess.Popen',side_effect=OSError('unavailable')):
    with self.assertRaises(OSError):r.start_sampler('/missing',{'internal':'disk0'})
   r.finish('failed','read error');self.assertEqual(json.loads(r.base.with_suffix('.task.json').read_text())['status'],'failed')
 def test_stale_running_record_is_not_claimed_live(self):
  with tempfile.TemporaryDirectory() as t:
   r=TaskRecorder(Path(t)/'check','Recovery');old=time.time()-20
   os.utime(r.base.with_suffix('.task.json'),(old,old))
   d=HarnessMonitor().snapshot(t)
   self.assertIn('status unconfirmed',d['state']);self.assertFalse(d['stage']['live'])
 def test_command_wrapper_records_real_failure(self):
  import subprocess
  with tempfile.TemporaryDirectory() as t:
   root=Path(__file__).resolve().parents[1];out=Path(t)/'failure'
   p=subprocess.run([sys.executable,str(root/'scripts/record-task.py'),'--output',str(out),'--label','Expected failure','--kind','build','--',sys.executable,'-c','raise SystemExit(7)'],capture_output=True)
   self.assertEqual(p.returncode,7)
   d=HarnessMonitor().snapshot(t);self.assertEqual(d['state'],'Build · failed')
   self.assertEqual(d['task']['detail'],'Exit status 7')
 def test_invalid_task_record_not_discovered(self):
  with tempfile.TemporaryDirectory() as t:
   r=TaskRecorder(Path(t)/'check','Recovery');r.base.with_suffix('.task.json').write_text('{"schema":99}')
   self.assertEqual(HarnessMonitor().snapshot(t)['choices'],[])
if __name__=='__main__':unittest.main()
