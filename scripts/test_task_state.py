import contextlib,json,shutil,unittest,uuid
from pathlib import Path
import task_state

@contextlib.contextmanager
def fixture():
 root=Path.cwd()/'test-data-tmp';root.mkdir(exist_ok=True);p=root/uuid.uuid4().hex;p.mkdir()
 try:yield p
 finally:
  assert p.parent==root and not p.is_symlink();shutil.rmtree(p)

class StateTests(unittest.TestCase):
 def test_resume_preserves_block_and_history(self):
  with fixture() as root:
   p=root/'state.json';task_state.init(p,'fixture')
   task_state.transition(p,'identify','running','input found','scan')
   task_state.transition(p,'identify','blocked','missing version','inspect metadata')
   with self.assertRaises(ValueError):task_state.transition(p,'identify','completed','guess','continue')
   task_state.transition(p,'identify','pending','version discovered','resume')
   task_state.transition(p,'identify','running','source verified','run')
   task_state.transition(p,'identify','completed','manifest verified','inventory')
   saved=json.loads(p.read_text(encoding='utf-8'));self.assertEqual(saved['phases']['identify'],'completed')
   self.assertEqual([h['to'] for h in saved['history']],['running','blocked','pending','running','completed'])
   self.assertEqual(saved['phases']['user_acceptance'],'pending')
   with self.assertRaises(FileExistsError):task_state.init(p,'fixture')
 def test_acceptance_requires_completed_prerequisites(self):
  with fixture() as root:
   p=root/'state.json';task_state.init(p,'fixture')
   task_state.transition(p,'user_acceptance','running','user checking','wait')
   with self.assertRaises(ValueError):task_state.transition(p,'user_acceptance','completed','claimed pass','finish')
   for phase in task_state.PHASES[:task_state.PHASES.index('user_acceptance')]:
    if phase=='validate_build':
     task_state.transition(p,phase,'running','started','validate')
     task_state.transition(p,phase,'completed','validated','continue')
    else:task_state.transition(p,phase,'not_requested','outside this fixture scope','continue')
   task_state.transition(p,'user_acceptance','completed','user explicitly passed','done')
   with self.assertRaises(ValueError):task_state.transition(p,'feedback_repair','running','new report','investigate')
   with self.assertRaises(ValueError):task_state.transition(p,'validate_build','pending','new change','rebuild')
   task_state.transition(p,'user_acceptance','pending','new feedback','repeat acceptance')
   task_state.transition(p,'feedback_repair','running','new report','investigate')
   task_state.transition(p,'user_acceptance','running','user reviewing','wait for repair')
   with self.assertRaises(ValueError):task_state.transition(p,'user_acceptance','completed','premature pass','finish')
   task_state.transition(p,'feedback_repair','completed','repair checked','retest')
   task_state.transition(p,'user_acceptance','completed','user passed retest','finish')
if __name__=='__main__':unittest.main(verbosity=2)
