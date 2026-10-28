"""Offline behavioral tests: no game, launcher, keys or network."""
import concurrent.futures, contextlib, io, json, os, shutil, subprocess, sys, unittest, uuid
from pathlib import Path
import file_tx, openai_compatible_adapter as http_adapter, recover_translation, text_guard as guard, translation_ledger as ledger, translate_driver as driver

@contextlib.contextmanager
def fixture_directory():
 # Normal mkdir inherits sandbox ACLs; Windows tempfile's restrictive ACL may not.
 root=(Path.cwd()/'test-data-tmp').resolve();root.mkdir(exist_ok=True)
 p=root/uuid.uuid4().hex;p.mkdir()
 try:yield str(p)
 finally:
  assert p.resolve().is_relative_to(root) and p.parent==root and not p.is_symlink()
  shutil.rmtree(p)

class TextTests(unittest.TestCase):
 def test_format_parameters_and_numbers(self):
  src='Attack %s for %d damage with mod:weapon; §cDanger§r; 5%% of 1,000.'
  self.assertTrue(guard.validate(src,'用 mod:weapon 攻击 %s，造成 %d 伤害；§c危险§r；1,000 的 5%%。')['ok'])
  self.assertIn('unnumbered_printf_order_changed',guard.validate(src,'用 mod:weapon 攻击 %d，造成 %s 伤害；§c危险§r；1,000 的 5%%。')['errors'])
  self.assertIn('protected_tokens_changed',guard.validate(src,'用 mod:weapon 攻击 %s，造成 %d 伤害；§c危险；1,000 的 5%%。')['errors'])
  self.assertIn('display_numbers_changed',guard.validate(src,'用 mod:weapon 攻击 %s，造成 %d 伤害；§c危险§r；999 的 5%%。')['errors'])
  self.assertTrue(guard.validate('%1$s has %2$d', '%2$d 个属于 %1$s')['ok'])
 def test_functional_table_locked_and_glitches(self):
  self.assertFalse(guard.validate(' ABC;DEF','ABC；DEF','create.flap_display.cycles.alphabet')['ok'])
  self.assertTrue(guard.validate(' ABC;DEF',' ABC;DEF','create.flap_display.cycles.alphabet')['ok'])
  self.assertFalse(guard.validate('Hello','坏\ufffd字')['ok'])
  self.assertFalse(guard.validate('example:item','example:other','quest.task.id')['ok'])
 def test_escaping_and_nontranslated_components(self):
  self.assertTrue(guard.validate('%%val1 + {name} §r https://example.org/x `x=5`','%%val1 加 {name} §r https://example.org/x `x=5`')['ok'])
  self.assertFalse(guard.validate('%%val1 + {name} §r','%%val2 加 {name} §r')['ok'])
 def test_attached_units_cjk_and_identifiers(self):
  self.assertIn('display_numbers_changed',guard.validate('Deals 2x damage','Deals 10x damage')['errors'])
  self.assertIn('display_numbers_changed',guard.validate('造成2倍伤害','造成3倍伤害')['errors'])
  self.assertIn('display_numbers_changed',guard.validate('Wait 1.5s','等待2秒')['errors'])
  self.assertTrue(guard.validate('mod:item2 produces 2x damage in 5s','mod:item2 造成2倍伤害，持续5秒')['ok'])
  self.assertTrue(guard.validate('Keep 2x_boost and item2 at v1.20.1','保留 2x_boost 和 item2，版本 v1.20.1')['ok'])
  self.assertIn('protected_tokens_changed',guard.validate('Use mod:item2 for 2x','使用 mod:item3 造成2倍')['errors'])

class LedgerTests(unittest.TestCase):
 def setUp(self):self.temp=fixture_directory();self.db=str(Path(self.temp.__enter__())/'ledger.db');ledger.initialize(self.db,'1','CNY')
 def tearDown(self):self.temp.__exit__(None,None,None)
 def test_reserve_settle_cache_and_budget(self):
  a={'model':'a','entries':[{'source':'Hello %s'}]};r=ledger.reserve(self.db,a,'0.4');self.assertEqual(r['decision'],'reserved')
  self.assertEqual(ledger.reserve(self.db,a,'0.4')['decision'],'already_in_flight')
  ledger.mutate(self.db,r['id'],'settle',actual_cost='0.21',output='["你好 %s"]',validated=True)
  self.assertEqual(ledger.reserve(self.db,a,'0.9')['decision'],'cached')
  b={'model':'b','entries':[{'source':'Hello %s'}]}
  self.assertEqual(ledger.reserve(self.db,b,'0.8')['decision'],'budget_exhausted')
  self.assertEqual(ledger.status(self.db)['settled'],'0.210000')
  with self.assertRaises(ValueError):ledger.initialize(self.db,'10','CNY')
  with self.assertRaises(ValueError):ledger.set_cap(self.db,'2','')
  ledger.set_cap(self.db,'2','user explicitly approved cumulative cap increase')
  self.assertEqual(ledger.status(self.db)['settled'],'0.210000');self.assertEqual(ledger.status(self.db)['remaining'],'1.790000')
  self.assertEqual(ledger.status(self.db)['cap_changes'],1)
 def test_uncertain_blocks_duplicates_and_requires_proof(self):
  a={'model':'a','source':'hello'};r=ledger.reserve(self.db,a,'0.6');ledger.mutate(self.db,r['id'],'uncertain',note='timeout')
  self.assertEqual(ledger.reserve(self.db,a,'0.1')['decision'],'already_in_flight')
  self.assertEqual(ledger.status(self.db)['reserved_or_uncertain'],'0.600000')
  with self.assertRaises(ValueError):ledger.mutate(self.db,r['id'],'not_sent')
  ledger.mutate(self.db,r['id'],'settle',actual_cost='0.3',validated=False)
  self.assertEqual(ledger.status(self.db)['accounted'],'0.300000')
  self.assertEqual(ledger.reserve(self.db,a,'0.6')['decision'],'paid_output_needs_review')
  ledger.authorize_retry(self.db,r['id'],'response unavailable after reconciliation; retry justified')
  self.assertEqual(ledger.reserve(self.db,a,'0.6')['decision'],'reserved')
 def test_parallel_reservations_are_atomic(self):
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
   results=list(pool.map(lambda i:ledger.reserve(self.db,{'source':str(i)},'0.3')['decision'],range(8)))
  self.assertEqual(results.count('reserved'),3);self.assertEqual(results.count('budget_exhausted'),5)
  self.assertEqual(ledger.status(self.db)['accounted'],'0.900000')
 def test_credentials_cannot_enter_request(self):
  with self.assertRaises(ValueError):ledger.reserve(self.db,{'model':'a','api_key':'secret'},'0.01')
 def test_exceeded_reservation_freezes_new_calls_until_review(self):
  r=ledger.reserve(self.db,{'source':'first'},'0.1');ledger.mutate(self.db,r['id'],'settle',actual_cost='0.2',validated=False)
  self.assertTrue(ledger.status(self.db)['budget_frozen']);self.assertEqual(ledger.reserve(self.db,{'source':'second'},'0.1')['decision'],'budget_frozen')
  ledger.review_bound(self.db,r['id'],'verified provider prices and raised the next bound')
  self.assertFalse(ledger.status(self.db)['budget_frozen']);self.assertEqual(ledger.reserve(self.db,{'source':'second'},'0.1')['decision'],'reserved')

class AdapterTests(unittest.TestCase):
 def test_missing_usage_retains_reservation_and_prevents_duplicate(self):
  with fixture_directory() as d:
   root=Path(d);db=str(root/'ledger.db');ledger.initialize(db,'1','CNY');count=root/'calls.txt';child=root/'child.py'
   child.write_text('import json,sys\njson.load(sys.stdin)\nopen(sys.argv[1],"a").write("x")\nprint(json.dumps({"status":"success","translations":["你好"],"cost":"0"},ensure_ascii=False))',encoding='utf-8')
   spec={'provider':'fixture','identity':'missing-usage','currency':'CNY','command':[sys.executable,str(child),str(count)],'timeout_seconds':2,'enforces_max_cost':True};req={'source':'Hello'}
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'uncertain')
   self.assertEqual(ledger.status(db)['accounted'],'0.100000')
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'already_in_flight')
   self.assertEqual(count.read_text(),'x')
 def test_empty_translation_rejected_and_paid_result_not_repeated(self):
  with fixture_directory() as d:
   root=Path(d);db=str(root/'ledger.db');ledger.initialize(db,'1','CNY');count=root/'calls.txt';child=root/'child.py'
   child.write_text('import json,sys\njson.load(sys.stdin)\nopen(sys.argv[1],"a").write("x")\nprint(json.dumps({"status":"success","translations":["  "],"cost":"0.02","usage":{"input_tokens":1,"output_tokens":1}}))',encoding='utf-8')
   spec={'provider':'fixture','identity':'blank','currency':'CNY','command':[sys.executable,str(child),str(count)],'timeout_seconds':2,'enforces_max_cost':True};req={'source':'Apple'}
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'invalid_output')
   self.assertEqual(ledger.status(db)['settled'],'0.020000')
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'paid_output_needs_review')
   self.assertEqual(count.read_text(),'x')
 def test_missing_and_uninitialized_ledger_never_send_or_create_db(self):
  import sqlite3
  with fixture_directory() as d:
   missing=Path(d)/'missing.sqlite'
   with self.assertRaisesRegex(ValueError,'ledger'):
    driver.run(str(missing),{'source':'Hello'}, {'provider':'fixture','currency':'CNY','command':[sys.executable,'should-not-run.py'],'enforces_max_cost':True},'0.1')
   self.assertFalse(missing.exists())
   empty=Path(d)/'empty.sqlite';sqlite3.connect(empty).close()
   with self.assertRaisesRegex(ValueError,'not initialized'):ledger.status(empty)
 def test_real_child_protocol_cache_and_uncertain(self):
  with fixture_directory() as d:
   root=Path(d);db=str(root/'ledger.db');ledger.initialize(db,'1','CNY');count=root/'calls.txt';child=root/'child.py'
   child.write_text('''import json,sys,time\np=json.load(sys.stdin)\nwith open(sys.argv[1],"a",encoding="utf-8") as f:f.write("x")\nif p["request"].get("mode")=="timeout":time.sleep(3)\nrows=p["request"]["entries"]\nprint(json.dumps({"status":"success","translations":[r["source"].replace("Hello","你好") for r in rows],"cost":"0.02","usage":{"input_tokens":10,"output_tokens":10}},ensure_ascii=False))\n''',encoding='utf-8')
   spec={'provider':'fixture','identity':'fixture-v1','currency':'CNY','command':[sys.executable,str(child),str(count)],'timeout_seconds':2,'enforces_max_cost':True}
   req={'model':'fake','entries':[{'source':'Hello %s','key':'item.example.name'}]}
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'translated')
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'cached');self.assertEqual(count.read_text(),'x')
   req2={'mode':'timeout','entries':[{'source':'Hello'}]};spec['timeout_seconds']=0.2
   self.assertEqual(driver.run(db,req2,spec,'0.1')['decision'],'uncertain')
   self.assertEqual(driver.run(db,req2,spec,'0.1')['decision'],'already_in_flight')
   self.assertEqual(count.read_text(),'xx');self.assertEqual(ledger.status(db)['accounted'],'0.120000')
 def test_paid_invalid_response_recovered_without_resend(self):
  with fixture_directory() as d:
   root=Path(d);db=str(root/'ledger.db');ledger.initialize(db,'1','CNY');count=root/'calls.txt';child=root/'child.py'
   child.write_text('''import json,sys\np=json.load(sys.stdin)\nwith open(sys.argv[1],"a",encoding="utf-8") as f:f.write("x")\nprint(json.dumps({"status":"success","translations":["你好"],"text":"api_key=sk-1234567890abcdefghijkl 12345678-1234-1234-1234-123456789abc","api_key":"top-secret","cost":"0.03","usage":{"input_tokens":10,"output_tokens":10}},ensure_ascii=False))\n''',encoding='utf-8')
   spec={'provider':'fixture','identity':'recover-v1','currency':'CNY','command':[sys.executable,str(child),str(count)],'timeout_seconds':2,'enforces_max_cost':True}
   req={'model':'fake','entries':[{'source':'Hello %s','key':'item.example.name'}]}
   result=driver.run(db,req,spec,'0.1');self.assertEqual(result['decision'],'invalid_output');rid=result['ledger']['id']
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'paid_output_needs_review');self.assertEqual(count.read_text(),'x')
   q=ledger.quarantine_read(db,rid)['quarantine'];serialized=json.dumps(q)
   self.assertEqual(q['translations'],['你好']);self.assertNotIn('top-secret',serialized);self.assertNotIn('sk-1234567890abcdefghijkl',serialized);self.assertNotIn('12345678-1234-1234-1234-123456789abc',serialized)
   self.assertEqual(recover_translation.recover(db,rid,req,spec)['status'],'still_invalid')
   self.assertEqual(recover_translation.recover(db,rid,req,spec,['你好 %s'])['status'],'cached_after_offline_revalidation')
   self.assertEqual(driver.run(db,req,spec,'0.1')['decision'],'cached');self.assertEqual(count.read_text(),'x');self.assertEqual(ledger.status(db)['settled'],'0.030000')
 def test_paid_shape_error_automatically_recovers(self):
  with fixture_directory() as d:
   root=Path(d);db=str(root/'ledger.db');ledger.initialize(db,'1','CNY');child=root/'child.py'
   child.write_text('import json,sys\njson.load(sys.stdin)\nprint(json.dumps({"status":"success","translation":"你好 %s","cost":"0.02","usage":{"input_tokens":10,"output_tokens":10}},ensure_ascii=False))',encoding='utf-8')
   spec={'provider':'fixture','identity':'shape-v1','currency':'CNY','command':[sys.executable,str(child)],'timeout_seconds':2,'enforces_max_cost':True};req={'source':'Hello %s'}
   result=driver.run(db,req,spec,'0.1');self.assertEqual(result['decision'],'invalid_output');rid=result['ledger']['id']
   self.assertEqual(recover_translation.recover(db,rid,req,spec)['status'],'cached_after_offline_revalidation')
   self.assertEqual(driver.run(db,req,spec,'0.1')['translations'],['你好 %s'])

class TransactionTests(unittest.TestCase):
 def fixture(self,root):
  stage=root/'stage';dest=root/'dest';state=root/'record';stage.mkdir();dest.mkdir();(stage/'old.txt').write_text('new');(stage/'new.txt').write_text('created');(dest/'old.txt').write_text('original');(dest/'user.txt').write_text('preserve');return stage,dest,state
 def test_edit_guard_and_rollback(self):
  with fixture_directory() as d:
   stage,dest,state=self.fixture(Path(d));file_tx.plan(stage,dest,state)
   file_tx.apply(state,guard=lambda:None);self.assertEqual((dest/'old.txt').read_text(),'new')
   (dest/'old.txt').write_text('user edit')
   with self.assertRaises(RuntimeError):file_tx.rollback(state,guard=lambda:None)
   self.assertEqual((dest/'new.txt').read_text(),'created');self.assertEqual((dest/'user.txt').read_text(),'preserve')
   (dest/'old.txt').write_text('new');file_tx.rollback(state,guard=lambda:None)
   self.assertEqual((dest/'old.txt').read_text(),'original');self.assertFalse((dest/'new.txt').exists());self.assertEqual((dest/'user.txt').read_text(),'preserve')
   with self.assertRaises(ValueError):file_tx.plan(stage,dest,state)
 def test_resume_after_interruption(self):
  with fixture_directory() as d:
   stage,dest,state=self.fixture(Path(d));file_tx.plan(stage,dest,state);v=file_tx.load(state);v['phase']='applying'
   row=next(x for x in v['files'] if x['path']=='old.txt');row['started']=True;file_tx.save(state,v);(dest/'old.txt').write_text('new')
   self.assertEqual(file_tx.apply(state,guard=lambda:None)['phase'],'applied');file_tx.rollback(state,guard=lambda:None);self.assertEqual((dest/'old.txt').read_text(),'original')
 def test_changed_target_blocks_all_writes(self):
  with fixture_directory() as d:
   stage,dest,state=self.fixture(Path(d));file_tx.plan(stage,dest,state);(dest/'old.txt').write_text('another change')
   with self.assertRaises(RuntimeError):file_tx.apply(state,guard=lambda:None)
   self.assertFalse((dest/'new.txt').exists());self.assertEqual((dest/'old.txt').read_text(),'another change')

class HttpAdapterTests(unittest.TestCase):
 def setUp(self):
  self.config={'endpoint':'https://example.invalid/v1/chat/completions','api_key_env':'TEST_ONLY_KEY','model':'fixture-model','currency':'CNY','input_price_per_million':'2','output_price_per_million':'4','verified_max_prompt_utf8_bytes':4096,'verified_max_input_tokens':800,'max_output_tokens':200,'provider_pricing_and_token_bound_verified':True}
  self.packet={'request':{'model':'fixture-model','entries':[{'source':'Hello %s'}]},'currency':'CNY','max_cost':'0.003'};self.env={'TEST_ONLY_KEY':'fixture-secret'};self.sent=[]
 def opener(self,request,timeout):
  self.sent.append(request);response={'usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[{'message':{'content':json.dumps({'translations':['你好 %s']},ensure_ascii=False)}}]}
  return io.BytesIO(json.dumps(response,ensure_ascii=False).encode('utf-8'))
 def test_success_and_real_upper_reservation_calculation(self):
  r=http_adapter.run(self.packet,self.config,self.env,opener=self.opener);self.assertEqual(r['status'],'success');self.assertEqual(r['translations'],['你好 %s']);self.assertEqual(r['cost'],'0.000400');self.assertEqual(len(self.sent),1)
  body=json.loads(self.sent[0].data.decode('utf-8'));self.assertEqual(body['max_tokens'],200);self.assertNotIn('fixture-secret',json.dumps(body))
 def test_no_send_without_bound_credentials_or_budget(self):
  self.packet['max_cost']='0.002';self.assertEqual(http_adapter.run(self.packet,self.config,self.env,opener=self.opener)['status'],'not_sent')
  self.packet['max_cost']='0.003';self.config['provider_pricing_and_token_bound_verified']=False;self.assertEqual(http_adapter.run(self.packet,self.config,self.env,opener=self.opener)['status'],'not_sent')
  self.config['provider_pricing_and_token_bound_verified']=True;self.assertEqual(http_adapter.run(self.packet,self.config,{},opener=self.opener)['status'],'not_sent');self.assertEqual(len(self.sent),0)
 def test_failure_after_submission_remains_unknown(self):
  def lost(request,timeout):raise TimeoutError('response lost')
  with self.assertRaises(TimeoutError):http_adapter.run(self.packet,self.config,self.env,opener=lost)
 def test_provider_exceeding_token_bound_freezes_signal(self):
  def over(request,timeout):return io.BytesIO(json.dumps({'usage':{'prompt_tokens':801,'completion_tokens':3},'choices':[{'message':{'content':'{"translations":["hello"]}'}}]}).encode())
  self.assertTrue(http_adapter.run(self.packet,self.config,self.env,opener=over)['bound_violation'])

if __name__=='__main__':unittest.main(verbosity=2)
