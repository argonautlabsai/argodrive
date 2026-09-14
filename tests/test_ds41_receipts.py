import sys,json,hashlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'monitor'))
import ds41_benchmark as b
class ReceiptTests(unittest.TestCase):
 def test_identity_change_and_missing_entry_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'model';p.write_bytes(b'GGUFsample');sha=hashlib.sha256(p.read_bytes()).hexdigest()
   receipt=root/'receipt.json';data={'source':{'path':str(p),'sha256':sha,'identity':b.file_identity(p)},'replicas':[]};receipt.write_text(json.dumps(data))
   with patch.object(b,'DS41_Q4_BYTES',p.stat().st_size),patch.object(b,'DS41_Q4_SHA256',sha):
    self.assertEqual(len(b.check_verification_receipt(receipt,p,[])),1)
    p.write_bytes(b'GGUFchange')
    with self.assertRaisesRegex(ValueError,'changed'):b.check_verification_receipt(receipt,p,[])
    with self.assertRaisesRegex(ValueError,'No full-checksum'):b.check_verification_receipt(receipt,root/'other',[])
 def test_replica_launch_requires_receipt_before_any_process(self):
  with patch.object(b.subprocess,'Popen') as child:
   with self.assertRaisesRegex(ValueError,'require completed'):b.run({'model_path':'/model'},replicas=['/replica'])
   child.assert_not_called()
if __name__=='__main__':unittest.main()
