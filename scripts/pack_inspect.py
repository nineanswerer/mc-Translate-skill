#!/usr/bin/env python3
"""Read-only, static Minecraft Java pack inventory and *candidate* language layering.

Usage: python pack_inspect.py INSTANCE --output PATH [--locks JSON_FILE]
Only Java edition language JSON for the configured source/target locales is inspected. Selected file
resource packs use options.txt order (low to high). Mod JARs are treated as one
unordered tier: conflicts stay unresolved. Runtime-generated resources, hidden
required-pack positioning and arbitrary text formats need adapters
and real-game verification. Nothing in INSTANCE is written.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

ASSET = re.compile(r'^assets/([^/]+)/lang/([a-z][a-z0-9_-]{1,31})\.json$', re.I)
LOCALE = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
HAN = re.compile(r'[\u3400-\u9fff]')
DEFAULT_LOCKS = {'create:create.flap_display.cycles.alphabet'}
MAX_LANGUAGE_BYTES = 16 * 1024 * 1024
GENERATED = ('mods/.connector', 'lightspeed-cache', 'particular_cache',
             'config/QuantifiedAPI/cache', 'config/inventory-particles/cache',
             'config/inventory-interactions/cache', 'config/emi-accelerator/stack-cache.emic')
PRIVATE = ('saves', 'logs', 'crash-reports', 'journeymap', 'local/ftbchunks/data')


def sha256_file(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def clean_path(path: str) -> str:
    p = Path(path)
    if not path or p.is_absolute() or any(x in ('..', '') for x in p.parts):
        raise ValueError('unsafe relative path')
    return p.as_posix()


def bounded_files(path: Path) -> tuple[int, int]:
    if not path.exists() or path.is_symlink():
        return 0, 0
    if path.is_file():
        return 1, path.stat().st_size
    count = size = 0
    for directory, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in files:
            f = Path(directory) / name
            if not f.is_symlink():
                count += 1
                size += f.stat().st_size
    return count, size


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


class Inspector:
    def __init__(self, root: Path, locked: set[str], source_locale="en_us", target_locale="zh_cn"):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('instance must be a directory')
        self.source_locale = source_locale.lower()
        self.target_locale = target_locale.lower()
        if not LOCALE.fullmatch(self.source_locale) or not LOCALE.fullmatch(self.target_locale) or self.source_locale == self.target_locale:
            raise ValueError("distinct, valid source/target locales required")
        self.locales = (self.source_locale, self.target_locale)
        self.locked = locked
        self.errors = []
        self.hashes = {}
        self.tiers = []
        self.dynamic = []

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def evidence(self, path: Path) -> str:
        rel = self.relative(path)
        if rel not in self.hashes:
            self.hashes[rel] = sha256_file(path)
        return self.hashes[rel]

    def error(self, source: str, reason: str):
        self.errors.append({'source': source, 'reason': reason})

    def add_json(self, tier: dict, member: str, raw: bytes, source: str, evidence_hash: str):
        match = ASSET.fullmatch(member.replace('\\', '/'))
        if not match or match[2].lower() not in self.locales:
            return
        if len(raw) > MAX_LANGUAGE_BYTES:
            self.error(source, 'language JSON exceeds 16 MiB'); return
        namespace, locale = match[1].lower(), match[2].lower()
        try:
            obj = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=no_duplicate_keys)
            if not isinstance(obj, dict):
                raise ValueError('expected a JSON object')
        except (ValueError, UnicodeError) as exc:
            self.error(source, f'invalid language JSON: {type(exc).__name__}'); return
        for key, value in obj.items():
            if not isinstance(key, str) or not isinstance(value, str):
                self.error(source, 'non-string language key/value'); continue
            identity = namespace + ':' + key
            record = {'source': source, 'sha256': evidence_hash, 'value': value}
            tier['entries'].setdefault((identity, locale), []).append(record)

    def archive(self, path: Path, tier: dict):
        if not path.exists() or path.is_symlink() or not path.is_file():
            self.error(self.relative(path), 'missing, symlink or not a file'); return
        rel, digest = self.relative(path), self.evidence(path)
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    selected = ASSET.fullmatch(member.filename)
                    if not selected or selected[2].lower() not in self.locales:
                        continue
                    source = rel + '!/' + member.filename
                    if member.file_size > MAX_LANGUAGE_BYTES:
                        self.error(source, 'language JSON exceeds 16 MiB'); continue
                    try:
                        self.add_json(tier, member.filename, archive.read(member), source, digest)
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        self.error(source, 'cannot read ZIP member')
        except (OSError, zipfile.BadZipFile):
            self.error(rel, 'invalid or unreadable ZIP/JAR')

    def directory_pack(self, path: Path, tier: dict):
        assets = path / 'assets'
        if not assets.is_dir() or assets.is_symlink():
            self.error(self.relative(path), 'resource pack has no readable assets directory'); return
        for namespace in sorted(assets.iterdir(), key=lambda p: p.name.casefold()):
            if not namespace.is_dir() or namespace.is_symlink():
                continue
            lang = namespace / 'lang'
            if not lang.is_dir() or lang.is_symlink():
                continue
            for item in sorted(lang.iterdir(), key=lambda p: p.name.casefold()):
                if item.is_symlink() or not item.is_file():
                    continue
                member = item.relative_to(path).as_posix()
                selected = ASSET.fullmatch(member)
                if not selected or selected[2].lower() not in self.locales:
                    continue
                rel = self.relative(item)
                if item.stat().st_size > MAX_LANGUAGE_BYTES:
                    self.error(rel, 'language JSON exceeds 16 MiB'); continue
                self.add_json(tier, member, item.read_bytes(), rel, self.evidence(item))

    def options(self, relative: str):
        file = self.root / relative
        if not file.is_file() or file.is_symlink():
            return None
        self.evidence(file)
        data = {}
        for line in file.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            if ':' in line:
                key, val = line.split(':', 1)
                if key in ('resourcePacks', 'lang'):
                    data[key] = val
        try:
            packs = json.loads(data.get('resourcePacks', '[]'))
            if not isinstance(packs, list) or not all(isinstance(p, str) for p in packs):
                raise ValueError('resourcePacks must be a list of strings')
        except (ValueError, json.JSONDecodeError):
            self.error(relative, 'invalid resourcePacks value'); packs = []
        return {'packs_low_to_high': packs, 'lang': data.get('lang')}

    def setup_tiers(self):
        mods = self.root / 'mods'
        jars = sorted((p for p in mods.iterdir() if p.suffix.lower() == '.jar' and p.is_file() and not p.is_symlink()), key=lambda p: p.name.casefold()) if mods.is_dir() and not mods.is_symlink() else []
        if mods.is_symlink():
            self.error('mods', 'symlinked mods directory is excluded')
        base = {'id': 'top-level mod JARs; relative order unknown', 'kind': 'unordered_mods', 'entries': {}}
        for file in jars:
            self.archive(file, base)
        self.tiers.append(base)
        options = self.options('options.txt')
        defaults = self.options('config/defaultoptions/options.txt')
        packs = options['packs_low_to_high'] if options is not None else []
        if options is None:
            self.error('options.txt', 'missing: selected resource pack order unknown')
        for number, pack in enumerate(packs):
            if not pack.startswith('file/'):
                self.dynamic.append({'id': pack, 'position': number, 'reason': 'built-in, mod-provided or dynamic pack; runtime content/position not resolved'})
                continue
            relative = pack[5:]
            try:
                safe = clean_path(relative)
                if (self.root / 'resourcepacks').is_symlink():
                    raise ValueError('symlinked resourcepacks directory is excluded')
                target = self.root / 'resourcepacks' / safe
                if not target.resolve().is_relative_to((self.root / 'resourcepacks').resolve()):
                    raise ValueError('path escapes resourcepacks')
            except ValueError:
                self.error('options.txt', 'unsafe resource pack ID'); continue
            tier = {'id': pack, 'kind': 'selected_file_pack', 'position': number, 'entries': {}}
            self.tiers.append(tier)
            if target.is_symlink():
                self.error(pack, 'symlinked resource pack is excluded')
            elif target.is_dir():
                self.directory_pack(target, tier)
            elif target.is_file():
                self.archive(target, tier)
            else:
                self.error(pack, 'selected resource pack is missing')
        config_file = self.root / 'config/defaultoptions-common.toml'
        defaultoptions = None
        if config_file.is_file() and not config_file.is_symlink():
            self.evidence(config_file)
            if tomllib is None:
                self.error(self.relative(config_file), 'Python 3.11+ tomllib required for TOML inspection')
            else:
                try:
                    parsed = tomllib.loads(config_file.read_text(encoding='utf-8'))
                    p = parsed.get('defaultResourcePacks')
                    if isinstance(p, list) and all(isinstance(v, str) for v in p):
                        defaultoptions = {'packs_low_to_high': p, 'selected_file_packs_absent_from_defaults': [x for x in packs if x.startswith('file/') and x not in p], 'journal_exists': (self.root / 'defaultoptions.journal.json').is_file(), 'runtime_application_not_verified': True}
                    else:
                        self.error(self.relative(config_file), 'defaultResourcePacks missing or invalid')
                except (UnicodeError, ValueError):
                    self.error(self.relative(config_file), 'invalid TOML')
        return jars, options, defaults, defaultoptions

    def resolved(self):
        all_keys = sorted({identity for tier in self.tiers for identity, _ in tier['entries']})
        result = []
        for identity in all_keys:
            resolved = {}
            for locale in self.locales:
                candidates = []
                for rank, tier in enumerate(self.tiers):
                    records = tier['entries'].get((identity, locale), [])
                    if records:
                        candidates.append({'tier': rank, 'tier_id': tier['id'], 'sources': records})
                if not candidates:
                    resolved[locale] = {'status': 'absent_in_scanned_sources'}
                else:
                    winning = candidates[-1]
                    distinct = {row['value'] for row in winning['sources']}
                    resolved[locale] = {'status': 'ambiguous_same_tier' if len(distinct) > 1 else 'candidate_only', 'value': next(iter(distinct)) if len(distinct) == 1 else None, 'winning_tier': winning['tier_id'], 'layers': candidates}
            en, zh = resolved[self.source_locale], resolved[self.target_locale]
            raw_key = identity.split(':', 1)[1]
            if identity in self.locked or raw_key in self.locked:
                action = 'locked_functional_value_do_not_translate'
            elif zh['status'] != 'absent_in_scanned_sources':
                if self.target_locale.startswith('zh_'):
                    action = 'preserve_existing_chinese_and_review' if zh['status'] != 'candidate_only' or not HAN.search(zh['value'] or '') else 'preserve_existing_chinese'
                else:
                    action = 'preserve_existing_translation' if zh['status'] == 'candidate_only' else 'preserve_existing_translation_and_review'
            elif en['status'] == 'candidate_only':
                action = 'translation_candidate_requires_context'
            else:
                action = 'manual_review'
            result.append({'id': identity, 'action': action, self.source_locale: en, self.target_locale: zh, 'runtime_winner_verified': False})
        return result

    def run(self):
        jars, options, defaults, defaultoptions = self.setup_tiers()
        categories = {}
        for kind, paths in [('generated', GENERATED), ('private_or_runtime', PRIVATE)]:
            categories[kind] = []
            for rel in paths:
                n, size = bounded_files(self.root / rel)
                if n:
                    categories[kind].append({'path': rel, 'files': n, 'bytes': size, 'contents_read': False})
        entries = self.resolved()
        return {'schema': 1, 'source_locale': self.source_locale, 'target_locale': self.target_locale, 'root_top_level_directories': sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()), 'source_mod_jars': [self.relative(p) for p in jars], 'top_level_mod_count': len(jars), 'categories': categories, 'selected_options': options, 'default_options_file': defaults, 'defaultoptions_mod_config': defaultoptions, 'dynamic_or_nonfile_packs_unresolved': self.dynamic, 'inspection_limits': [f'Only Java assets/*/lang/{self.source_locale}.json and {self.target_locale}.json.', 'Mod JARs are one unordered tier; same-tier conflicting values are ambiguous.', 'File pack order is inferred from options.txt; runtime and required-pack ordering must be verified.', 'Dynamic packs, custom scripts, quest SNBT, books and hard-coded text require additional adapters.', 'Existing target-locale values are protected, not certified semantically correct.'], 'source_hashes': self.hashes, 'entries': entries, 'errors': self.errors, 'instance_modified': False, 'runtime_winner_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='JSON report outside instance directory')
    parser.add_argument('--locks', type=Path, help='JSON object with locked_keys array (namespace:key or raw key)')
    parser.add_argument('--source-locale', default='en_us')
    parser.add_argument('--target-locale', default='zh_cn')
    args = parser.parse_args(argv)
    if not LOCALE.fullmatch(args.source_locale.lower()) or not LOCALE.fullmatch(args.target_locale.lower()) or args.source_locale.lower() == args.target_locale.lower():
        parser.error('distinct valid source and target locales required')
    root = args.instance.resolve(strict=True)
    output = args.output.resolve()
    if output.is_relative_to(root):
        parser.error('--output must be outside the source instance')
    locks = set(DEFAULT_LOCKS)
    if args.locks:
        config = json.loads(args.locks.read_text(encoding='utf-8'))
        values = config.get('locked_keys', [])
        if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
            parser.error('locks file requires a locked_keys string array')
        locks.update(values)
    report = Inspector(root, locks, args.source_locale, args.target_locale).run()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error('refusing to overwrite an existing report')
    with output.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'report': str(output), 'mod_jars': report['top_level_mod_count'], 'entries': len(report['entries']), 'errors': len(report['errors']), 'unresolved_dynamic_packs': len(report['dynamic_or_nonfile_packs_unresolved']), 'runtime_verified': False}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
