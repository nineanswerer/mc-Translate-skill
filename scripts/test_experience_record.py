import contextlib,hashlib,shutil,unittest,uuid
from pathlib import Path
import experience_record

@contextlib.contextmanager
def fixture():
 root=(Path.cwd()/'test-data-tmp').resolve();root.mkdir(exist_ok=True);p=root/uuid.uuid4().hex;p.mkdir()
 try:yield p
 finally:
  assert p.resolve().is_relative_to(root) and p.parent==root and not p.is_symlink();shutil.rmtree(p)

class ExperienceTests(unittest.TestCase):
 def entry(self,root):
  p=root/'proof.json';p.write_text('{"verified":true}',encoding='utf-8')
  return {'id':'scoped-finding','status':'observed','applicability':'project_only','scope':{'adapter':'demo-v1'},'finding':'A scoped observation','evidence':[{'path':'proof.json','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}
 def test_record_is_immutable_and_does_not_promote_rule(self):
  with fixture() as root:
   entry=self.entry(root);result=experience_record.record(entry,root,root/'lessons')
   self.assertFalse(result['rules_modified'])
   with self.assertRaises(FileExistsError):experience_record.record(entry,root,root/'lessons')
 def test_changed_evidence_blocks_record(self):
  with fixture() as root:
   entry=self.entry(root);(root/'proof.json').write_text('changed',encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'hash mismatch'):experience_record.record(entry,root,root/'lessons')
   self.assertFalse((root/'lessons/scoped-finding.json').exists())
 def test_confirmation_requires_regression_and_no_universal_adoption(self):
  with fixture() as root:
   entry=self.entry(root);entry['status']='confirmed'
   with self.assertRaises(ValueError):experience_record.record(entry,root,root/'lessons')
   entry['regression_evidence']=['local test command and report'];entry['applicability']='universal'
   with self.assertRaises(ValueError):experience_record.record(entry,root,root/'lessons')
if __name__=='__main__':unittest.main(verbosity=2)
