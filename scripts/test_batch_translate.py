"""Offline integration tests of budget, duplicate, cache, resume and user-file protection."""
import contextlib, json, shutil, sys, unittest, uuid
from pathlib import Path
import batch_translate as batch

HERE=Path(__file__).resolve().parent
SCRIPTS=HERE if (HERE/'translate_driver.py').exists() else HERE.parent/'minecraft-modpack-localization'/'scripts'
driver,guard=batch.load_modules(SCRIPTS)

@contextlib.contextmanager
def fixture():
 root=Path.cwd()/'batch-test-data';root.mkdir(exist_ok=True);path=root/uuid.uuid4().hex;path.mkdir()
 try:yield path
 finally:
  assert path.parent==root and not path.is_symlink();shutil.rmtree(path)

def setup(root,cap='1'):
 db=root/'ledger.sqlite';driver.ledger.initialize(db,cap,'CNY');count=root/'calls.txt';child=root/'fake_adapter.py'
 child.write_text('''import json,sys\np=json.load(sys.stdin)\nwith open(sys.argv[1],"a",encoding="utf-8") as f:f.write("x")\nmode=p["request"].get("fixture_mode")\nif mode=="uncertain":sys.exit(2)\nif mode=="not_sent":print(json.dumps({"status":"not_sent"}));sys.exit()\nitems=p["request"]["entries"]\ntranslations=["" if mode=="blank" else r["source"].replace("Apple","苹果").replace("Sword","剑") for r in items]\nprint(json.dumps({"status":"success","translations":translations,"cost":"0.03","usage":{"input_tokens":10,"output_tokens":10}},ensure_ascii=False))\n''',encoding='utf-8')
 adapter={'provider':'fixture','identity':'fixture-model-v1','currency':'CNY','command':[sys.executable,str(child),str(count)],'timeout_seconds':4,'enforces_max_cost':True}
 return db,count,adapter

def plan(items,**base):
 return {'schema':1,'request_base':dict({'model':'fixture','prompt_version':'v1','glossary_version':'v1'},**base),'items':items}

def item(i,source,context='shared',key=None,**extra):
 return dict({'id':i,'source':source,'context':context,'key':key or 'item.'+i,'kind':'item_name','status':'pending_translation'},**extra)

def run(root,p,adapter,db,**kwargs):
 return batch.coordinate(p,adapter,db,root/'state.json',root/'output.json',kwargs.get('batch_size',2),kwargs.get('max_cost','0.04'),kwargs.get('workers',1),kwargs.get('retries',0),0,driver,guard,pause_file=kwargs.get('pause_file'))

class BatchTests(unittest.TestCase):
 def test_dedup_preserve_resume_and_guard_output_edits(self):
  with fixture() as root:
   db,count,adapter=setup(root)
   rows=[item('one','Apple'),item('two','Apple'),item('known','Sword',existing_zh='好剑'),item('table',' A;B',key='create.flap_display.cycles.alphabet'),item('unknown','???')]
   p=plan(rows);r=run(root,p,adapter,db)
   self.assertEqual(r['unique_translation_groups'],1);self.assertEqual([x.get('translation') for x in r['items'][:2]],['苹果','苹果'])
   self.assertEqual([x['reason'] for x in r['items'][2:]],['existing_translation_preserved','functional_or_structural_field_locked','intentional_unknown_marker_needs_review'])
   self.assertEqual(r['status'],'needs_review');self.assertEqual(count.read_text(),'x');self.assertFalse(r['user_game_acceptance'])
   r2=run(root,p,adapter,db);self.assertEqual(r2['items'][0]['translation'],'苹果');self.assertEqual(count.read_text(),'x')
   (root/'output.json').write_text('user edit',encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'changed or is untracked'):run(root,p,adapter,db)
   self.assertEqual((root/'output.json').read_text(),'user edit');self.assertEqual(count.read_text(),'x')
 def test_budget_exhaustion_stops_next_dispatch(self):
  with fixture() as root:
   db,count,adapter=setup(root,'0.06');p=plan([item('one','Apple'),item('two','Sword'),item('three','Other')])
   r=run(root,p,adapter,db,batch_size=1)
   self.assertEqual(r['status'],'blocked');self.assertEqual(r['stop_reason'],'budget_exhausted');self.assertEqual(count.read_text(),'x')
   self.assertEqual(r['budget']['settled'],'0.030000');self.assertEqual(r['counts']['pending_or_blocked'],2)
 def test_uncertain_request_is_not_sent_twice(self):
  with fixture() as root:
   db,count,adapter=setup(root);p=plan([item('one','Apple')],fixture_mode='uncertain')
   r=run(root,p,adapter,db);self.assertEqual(r['stop_reason'],'uncertain');self.assertEqual(r['budget']['reserved_or_uncertain'],'0.040000')
   r2=run(root,p,adapter,db);self.assertEqual(r2['stop_reason'],'already_in_flight');self.assertEqual(count.read_text(),'x')
 def test_explicit_pause_marker_stops_dispatch_and_resumes_after_removal(self):
  with fixture() as root:
   db,count,adapter=setup(root);p=plan([item('one','Apple'),item('two','Sword')]);marker=root/'paused'
   marker.write_text('pause',encoding='utf-8')
   r=run(root,p,adapter,db,batch_size=1,pause_file=marker)
   self.assertEqual(r['stop_reason'],'paused_by_user');self.assertFalse(count.exists());self.assertEqual(r['budget']['accounted'],'0.000000')
   marker.unlink();r2=run(root,p,adapter,db,batch_size=1,pause_file=marker)
   self.assertEqual(r2['status'],'complete_structural_only');self.assertEqual(count.read_text(),'xx');self.assertEqual(r2['budget']['settled'],'0.060000')
 def test_pause_stops_later_batches_but_keeps_confirmed_inflight_result(self):
  with fixture() as root:
   db,count,adapter=setup(root);p=plan([item('one','Apple'),item('two','Sword')]);marker=root/'paused';child=Path(adapter['command'][1])
   body=child.read_text(encoding='utf-8');body=body.replace('items=p["request"]["entries"]','open(sys.argv[2],"w",encoding="utf-8").write("pause")\nitems=p["request"]["entries"]');child.write_text(body,encoding='utf-8');adapter['command'].append(str(marker))
   r=run(root,p,adapter,db,batch_size=1,pause_file=marker)
   self.assertEqual(r['stop_reason'],'paused_by_user');self.assertEqual(r['counts']['translated_structural_check_only'],1);self.assertEqual(count.read_text(),'x')
   marker.unlink();r2=run(root,p,adapter,db,batch_size=1,pause_file=marker)
   self.assertEqual(r2['counts']['translated_structural_check_only'],2);self.assertEqual(count.read_text(),'xx')
 def test_paid_invalid_needs_review_before_repeat(self):
  with fixture() as root:
   db,count,adapter=setup(root);p=plan([item('one','Apple')],fixture_mode='blank')
   r=run(root,p,adapter,db);self.assertEqual(r['stop_reason'],'invalid_output');self.assertEqual(r['budget']['settled'],'0.030000')
   r2=run(root,p,adapter,db);self.assertEqual(r2['stop_reason'],'paid_output_needs_review');self.assertEqual(count.read_text(),'x')
 def test_no_context_isolated_and_plan_change_refused(self):
  with fixture() as root:
   db,count,adapter=setup(root);p=plan([item('one','Apple',context=None),item('two','Apple',context=None)])
   r=run(root,p,adapter,db);self.assertEqual(r['unique_translation_groups'],2)
   p['items'][0]['context']='different'
   with self.assertRaisesRegex(ValueError,'plan, adapter identity'):run(root,p,adapter,db)
   self.assertEqual(count.read_text(),'x')
 def test_reject_secret_in_later_batch_before_any_dispatch(self):
  with fixture() as root:
   db,count,adapter=setup(root);p=plan([item('one','Apple'),item('two','api_key=synthetic-fixture-secret')])
   with self.assertRaisesRegex(ValueError,'credential'):run(root,p,adapter,db,batch_size=1,workers=2)
   self.assertFalse(count.exists());self.assertEqual(driver.ledger.status(db)['accounted'],'0.000000')

if __name__=='__main__':unittest.main(verbosity=2)
