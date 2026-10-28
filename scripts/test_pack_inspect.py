import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
import zipfile

SCRIPT = Path(__file__).with_name('pack_inspect.py')


def put_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding='utf-8')


def put_zip(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        for name, value in members.items():
            z.writestr(name, json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value)


def tree_hash(root):
    return {f.relative_to(root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in root.rglob('*') if f.is_file()}


class PackInspectionTest(unittest.TestCase):
    def setUp(self):
        self.test_root = Path.cwd().resolve()
        self.base = self.test_root / ('pack-inspect-test-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.root = self.base / 'instance'
        self.root.mkdir()
        put_zip(self.root/'mods/a.jar', {
            'assets/demo/lang/en_us.json': {'item.demo.known':'Apple', 'item.demo.ambiguous':'One', 'item.demo.missing':'Berry', 'item.demo.literal':'Pebble'},
            'assets/demo/lang/zh_cn.json': {'item.demo.known':'苹果', 'item.demo.literal':'Pebble'},
            'assets/create/lang/en_us.json': {'create.flap_display.cycles.alphabet':' ABC;DEF;'},
            'assets/create/lang/zh_cn.json': {'create.flap_display.cycles.alphabet':' ABC;DEF;'},
        })
        put_zip(self.root/'mods/B.JAR', {'assets/demo/lang/en_us.json':{'item.demo.ambiguous':'Two'}})
        put_zip(self.root/'mods/.connector/generated.jar', {'assets/demo/lang/en_us.json':{'item.demo.missing':'CORRUPT'}})
        put_file(self.root/'resourcepacks/low/assets/demo/lang/zh_cn.json', json.dumps({'item.demo.known':'低优先'}, ensure_ascii=False))
        put_zip(self.root/'resourcepacks/high.zip', {'assets/demo/lang/zh_cn.json': {'item.demo.known':'高优先'}})
        put_file(self.root/'options.txt', 'lang:zh_cn\nresourcePacks:["vanilla","file/low","generated/dynamic","file/high.zip"]\n')
        put_file(self.root/'config/defaultoptions-common.toml', 'defaultResourcePacks = ["vanilla", "file/low"]\n')
        put_file(self.root/'saves/private-world/level.dat', 'private data not inspected')

    def tearDown(self):
        assert self.base.resolve().is_relative_to(self.test_root)
        assert self.base.parent == self.test_root and self.base.name.startswith('pack-inspect-test-')
        shutil.rmtree(self.base)

    def run_cli(self, output=None, extra=()):
        output = output or self.base/'report.json'
        return subprocess.run([sys.executable, str(SCRIPT), str(self.root), '--output', str(output), *extra], text=True, capture_output=True)

    def test_layering_locks_conflicts_and_no_side_effects(self):
        original = tree_hash(self.root)
        locks = self.base/'locks.json'
        locks.write_text(json.dumps({'locked_keys':['demo:item.demo.missing']}), encoding='utf-8')
        result = self.run_cli(extra=('--locks', str(locks)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree_hash(self.root), original)
        report = json.loads((self.base/'report.json').read_text(encoding='utf-8'))
        entries = {e['id']:e for e in report['entries']}
        self.assertEqual(report['top_level_mod_count'], 2)
        self.assertEqual(entries['demo:item.demo.known']['zh_cn']['value'], '高优先')
        self.assertEqual(entries['demo:item.demo.known']['action'], 'preserve_existing_chinese')
        self.assertEqual(len(entries['demo:item.demo.known']['zh_cn']['layers']), 3)
        self.assertEqual(entries['demo:item.demo.ambiguous']['en_us']['status'], 'ambiguous_same_tier')
        self.assertIsNone(entries['demo:item.demo.ambiguous']['en_us']['value'])
        self.assertEqual(entries['create:create.flap_display.cycles.alphabet']['action'], 'locked_functional_value_do_not_translate')
        self.assertEqual(entries['demo:item.demo.missing']['action'], 'locked_functional_value_do_not_translate')
        self.assertEqual(entries['demo:item.demo.literal']['action'], 'preserve_existing_chinese_and_review')
        self.assertFalse(report['runtime_winner_verified'])
        self.assertTrue(any(p['id']=='generated/dynamic' for p in report['dynamic_or_nonfile_packs_unresolved']))
        self.assertEqual(report['defaultoptions_mod_config']['selected_file_packs_absent_from_defaults'], ['file/high.zip'])
        self.assertTrue(any(x['path']=='mods/.connector' and x['files']==1 for x in report['categories']['generated']))
        self.assertNotIn('saves/private-world/level.dat', report['source_hashes'])
        self.assertEqual(report['source_hashes']['mods/a.jar'], hashlib.sha256((self.root/'mods/a.jar').read_bytes()).hexdigest())

    def test_higher_file_pack_resolves_base_collision_but_runtime_still_unknown(self):
        put_zip(self.root/'resourcepacks/high.zip', {'assets/demo/lang/en_us.json': {'item.demo.ambiguous':'High priority'}})
        self.assertEqual(self.run_cli().returncode, 0)
        data = json.loads((self.base/'report.json').read_text(encoding='utf-8'))
        entry = next(e for e in data['entries'] if e['id']=='demo:item.demo.ambiguous')
        self.assertEqual(entry['en_us']['value'], 'High priority')
        self.assertEqual(entry['en_us']['winning_tier'], 'file/high.zip')
        self.assertFalse(entry['runtime_winner_verified'])

    def test_invalid_json_and_external_output_guard(self):
        put_file(self.root/'resourcepacks/low/assets/demo/lang/zh_cn.json', '{"duplicate":"one","duplicate":"two"}')
        snapshot = tree_hash(self.root)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.base/'report.json').read_text(encoding='utf-8'))
        self.assertTrue(any('invalid language JSON' in x['reason'] for x in data['errors']))
        attempted = self.root/'do-not-write.json'
        denied = self.run_cli(attempted)
        self.assertNotEqual(denied.returncode, 0)
        self.assertFalse(attempted.exists())
        self.assertEqual(tree_hash(self.root), snapshot)


if __name__ == '__main__':
    unittest.main()
