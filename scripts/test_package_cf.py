import contextlib,hashlib,json,shutil,unittest,uuid,zipfile
from pathlib import Path
import package_cf

@contextlib.contextmanager
def fixture():
 root=Path.cwd()/'test-data-tmp';root.mkdir(exist_ok=True);p=root/uuid.uuid4().hex;p.mkdir()
 try:yield p
 finally:
  assert p.parent==root and not p.is_symlink();shutil.rmtree(p)

class PackageTests(unittest.TestCase):
 def test_pack_structure_hash_and_no_overwrite(self):
  with fixture() as root:
   stage=root/'stage';(stage/'mods').mkdir(parents=True);jar=stage/'mods/source.jar'
   with zipfile.ZipFile(jar,'w') as z:z.writestr('META-INF/mods.toml','modId="demo"')
   before=hashlib.sha256(jar.read_bytes()).hexdigest();manifest=root/'manifest.json'
   manifest.write_text(json.dumps({'minecraft':{'version':'1.20.1','modLoaders':[{'id':'forge-47.1.0','primary':True}]},'manifestType':'minecraftModpack','manifestVersion':1,'name':'test','version':'1','files':[],'overrides':'overrides'}),encoding='utf-8')
   out=root/'pack.zip';report=package_cf.build(stage,manifest,out)
   with zipfile.ZipFile(out) as z:
    self.assertEqual(set(z.namelist()),{'manifest.json','overrides/mods/source.jar'});self.assertEqual(hashlib.sha256(z.read('overrides/mods/source.jar')).hexdigest(),before)
   self.assertTrue(report['crc_verified']);self.assertFalse(report['actual_installer_tested']);self.assertEqual(hashlib.sha256(jar.read_bytes()).hexdigest(),before)
   with self.assertRaises(FileExistsError):package_cf.build(stage,manifest,out)
 def test_personal_files_blocked_before_archive_creation(self):
  with fixture() as root:
   stage=root/'stage';(stage/'saves/world').mkdir(parents=True);(stage/'saves/world/level.dat').write_bytes(b'world')
   m=root/'manifest.json';m.write_text(json.dumps({'minecraft':{'version':'1','modLoaders':[]},'manifestType':'minecraftModpack','manifestVersion':1,'name':'x','version':'1','files':[],'overrides':'overrides'}))
   with self.assertRaises(ValueError):package_cf.build(stage,m,root/'pack.zip')
   self.assertFalse((root/'pack.zip').exists());self.assertEqual((stage/'saves/world/level.dat').read_bytes(),b'world')
if __name__=='__main__':unittest.main(verbosity=2)
