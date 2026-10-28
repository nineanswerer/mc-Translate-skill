"""Synthetic projects; no downloaded packs, external calls or game execution."""
import contextlib,hashlib,json,shutil,unittest,uuid,zipfile
from pathlib import Path
import batch_translate,openai_compatible_adapter,pack_inspect,project_profile,translation_ledger

@contextlib.contextmanager
def fixture():
 root=(Path.cwd()/'test-data-tmp').resolve();root.mkdir(exist_ok=True);p=root/('project-variants-'+uuid.uuid4().hex);p.mkdir()
 try:yield p
 finally:
  assert p.resolve().is_relative_to(root) and p.parent==root and not p.is_symlink();shutil.rmtree(p)

def put(root,name,value):
 p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

class ProjectVariants(unittest.TestCase):
 def test_curseforge_forge_metadata_without_runtime_claim(self):
  with fixture() as root:
   put(root,'manifest.json',{'minecraft':{'version':'1.20.1','modLoaders':[{'id':'forge-47.4.20'}],'javaVersion':21}})
   before=(root/'manifest.json').read_bytes();r=project_profile.profile(root)
   self.assertIn({'kind':'forge','version':'47.4.20','source':'manifest.json'},r['metadata_observations'])
   self.assertFalse(r['runtime_compatibility_verified']);self.assertEqual(before,(root/'manifest.json').read_bytes())
 def test_modrinth_fabric_independent_metadata(self):
  with fixture() as root:
   put(root,'modrinth.index.json',{'dependencies':{'minecraft':'1.21','fabric-loader':'0.16.0'}})
   r=project_profile.profile(root)
   self.assertEqual({x['kind'] for x in r['metadata_observations']},{'minecraft','fabric'})
   self.assertEqual(next(x for x in r['capabilities'] if x['id']=='modrinth_output')['status'],'needs_adapter')
 def test_prism_neoforge_and_conflicting_source_versions(self):
  with fixture() as root:
   put(root,'mmc-pack.json',{'components':[{'uid':'net.minecraft','version':'1.21.1'},{'uid':'net.neoforged','version':'21.1.0'}]})
   put(root,'manifest.json',{'minecraft':{'version':'1.20.1','modLoaders':[]}})
   r=project_profile.profile(root)
   self.assertEqual(r['conflicting_metadata']['minecraft'],['1.20.1','1.21.1'])
   self.assertIn('neoforge',{x['kind'] for x in r['metadata_observations']})
 def test_legacy_lang_detected_but_not_declared_parser_support(self):
  with fixture() as root:
   (root/'mods').mkdir()
   with zipfile.ZipFile(root/'mods/old.jar','w') as z:z.writestr('assets/demo/lang/en_us.lang','item.name=Old Item')
   r=project_profile.profile(root)
   self.assertEqual(r['format_counts']['jar_language:lang'],1)
   self.assertEqual(next(x for x in r['capabilities'] if x['id']=='legacy_lang')['status'],'needs_adapter')
 def test_nonenglish_source_and_traditional_target_are_selective(self):
  with fixture() as root:
   (root/'mods').mkdir();p=root/'mods/demo.jar'
   with zipfile.ZipFile(p,'w') as z:
    z.writestr('assets/demo/lang/de_de.json',json.dumps({'item.demo.name':'Apfel','item.demo.missing':'Schwert'}))
    z.writestr('assets/demo/lang/zh_tw.json',json.dumps({'item.demo.name':'蘋果'},ensure_ascii=False))
    z.writestr('assets/demo/lang/zh_cn.json','not selected invalid JSON')
   (root/'options.txt').write_text('lang:zh_tw\nresourcePacks:[]\n',encoding='utf-8');before=hashlib.sha256(p.read_bytes()).hexdigest()
   r=pack_inspect.Inspector(root,set(),'de_de','zh_tw').run();byid={x['id']:x for x in r['entries']}
   self.assertEqual(byid['demo:item.demo.name']['zh_tw']['value'],'蘋果')
   self.assertNotIn('zh_cn',byid['demo:item.demo.name']);self.assertEqual(r['errors'],[])
   self.assertEqual(byid['demo:item.demo.missing']['action'],'translation_candidate_requires_context')
   self.assertEqual(before,hashlib.sha256(p.read_bytes()).hexdigest())
 def test_invalid_or_equal_locale_rejected(self):
  with fixture() as root:
   for src,tgt in [('en_us','../zh'),('zh_cn','zh_cn')]:
    with self.assertRaises(ValueError):pack_inspect.Inspector(root,set(),src,tgt)
 def test_provider_prompt_and_cache_identity_use_requested_locale(self):
  base={'model':'fake','entries':[{'source':'Apfel'}],'source_locale':'de_de','target_locale':'zh_tw'}
  cfg={'endpoint':'https://example.invalid/v1/chat/completions','api_key_env':'FIXTURE_KEY','model':'fake','currency':'CNY','input_price_per_million':'1','output_price_per_million':'1','verified_max_prompt_utf8_bytes':10000,'verified_max_input_tokens':20000,'max_output_tokens':1000,'provider_pricing_and_token_bound_verified':True}
  ready=openai_compatible_adapter.prepare({'request':base,'currency':'CNY','max_cost':'1'},cfg,{'FIXTURE_KEY':'test-only-value'})
  self.assertIsNotNone(ready);body=json.loads(ready['request'].data)
  self.assertIn('Traditional Chinese',body['messages'][0]['content']);self.assertIn('de_de',body['messages'][0]['content'])
  alternate=dict(base,target_locale='zh_cn');self.assertNotEqual(translation_ledger.request_key(base),translation_ledger.request_key(alternate))
 def test_generic_existing_translation_is_preserved(self):
  import text_guard
  plan={'schema':1,'request_base':{'model':'fake','prompt_version':'v1','glossary_version':'v1','target_locale':'zh_tw'},'items':[{'id':'x','source':'Apple','key':'item.demo.apple','kind':'item_name','status':'pending_translation','existing_translation':'蘋果'}]}
  rows,groups,batches=batch_translate.plan_rows(plan,text_guard,8)
  self.assertEqual(rows[0]['reason'],'existing_translation_preserved');self.assertEqual(batches,[])
if __name__=='__main__':unittest.main(verbosity=2)
