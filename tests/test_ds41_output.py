import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'monitor'))
from ds41_benchmark import extract_generated
class OutputTests(unittest.TestCase):
 def test_plain_and_cleanup_preserve_quotes_and_newlines(self):
  text=b'a "quoted" line\nand another\n'
  raw=b'ds4-bench: gen[ctx=512] decoded text: "'+text+b'"\n'
  self.assertEqual(extract_generated(raw,512),text)
  summary=b'ds4: Metal memory at cleanup: runtime 0\nds4:   counters 1\nds4: Argodrive source[0] bytes=99\n'
  self.assertEqual(extract_generated(raw+summary,512),text)
 def test_overlap_counter_footer_keeps_exact_output(self):
  raw=b'ds4-bench: gen[ctx=512] decoded text: "exact output"\n'
  footer=b'ds4: Argodrive resident_gate_layers=123\nds4: Argodrive precommit_layers=117\nds4: Argodrive flat_read_batches=215\nds4: Argodrive whole_requests=256\nds4: Argodrive prefetch issued=4 source_bytes=100 copied_components=1 copied_bytes=20 busy=2 late=3 failed=0\nds4: Argodrive source[0] bytes=99\n'
  self.assertEqual(extract_generated(raw+footer,512),b'exact output')
  with self.assertRaises(ValueError):extract_generated(raw+b'ds4: Argodrive unrecognized=1\n',512)
 def test_repeated_engram_footers_from_prefill_and_decode(self):
  raw=b'ds4-bench: gen[ctx=512] decoded text: "line one\nline two"\n'
  footer=(b'ds4: Argodrive Engram source[0] bytes=12\n'
          b'ds4: Argodrive Engram source[1] bytes=24\n'
          b'ds4: Argodrive Engram source[2] bytes=36\n'
          b'ds4: Argodrive Engram source[0] bytes=48\n'
          b'ds4: Argodrive Engram source[1] bytes=60\n'
          b'ds4: Argodrive Engram source[2] bytes=72\n'
          b'ds4: Argodrive resident_gate_layers=886\n')
  self.assertEqual(extract_generated(raw+footer,512),b'line one\nline two')
 def test_completed_router_observation_footer_only(self):
  raw=b'ds4-bench: gen[ctx=512] decoded text: "exact output"\n'
  footer=b'ds4: Argodrive router observation records=2400 failed=0 write_ok=1\n'
  self.assertEqual(extract_generated(raw+footer,512),b'exact output')
  for bad in [footer.replace(b'failed=0',b'failed=1'),footer.replace(b'write_ok=1',b'write_ok=0'),footer.replace(b'records=2400',b'records=0')]:
   with self.assertRaises(ValueError):extract_generated(raw+bad,512)
 def test_unknown_tail_or_truncated_output_rejected(self):
  raw=b'ds4-bench: gen[ctx=512] decoded text: "test"\n'
  for bad in [raw+b'unknown\n',raw[:-2],raw+b'ds4: Metal memory at cleanup: runtime 0\nunknown\n']:
   with self.assertRaises(ValueError):extract_generated(bad,512)
if __name__=='__main__':unittest.main()
