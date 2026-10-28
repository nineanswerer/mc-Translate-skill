"""Read-only project discovery. Detecting a loader/format is not a compatibility claim."""
import argparse, collections, hashlib, json, os, sys, zipfile
from pathlib import Path

LOADERS={'forge-':'forge','neoforge-':'neoforge','fabric-':'fabric','quilt-':'quilt'}
COMPONENTS={'net.minecraftforge':'forge','net.neoforged':'neoforge','net.fabricmc.fabric-loader':'fabric','org.quiltmc.quilt-loader':'quilt'}

def profile(root):
 root=Path(root).resolve(strict=True)
 if not root.is_dir():raise ValueError('instance directory required')
 evidence=[];observations=[];warnings=[];formats=collections.Counter();locales=set();indicators=collections.Counter()
 def observe(kind,version,source):
  if isinstance(version,(str,int)):observations.append({'kind':kind,'version':str(version),'source':source})
 for name in ('manifest.json','modrinth.index.json','mmc-pack.json'):
  p=root/name
  if not p.is_file() or p.is_symlink():continue
  if p.stat().st_size>1_000_000:warnings.append(name+': metadata too large');continue
  raw=p.read_bytes();evidence.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest()})
  try:d=json.loads(raw.decode('utf-8-sig'))
  except (ValueError,UnicodeError):warnings.append(name+': invalid JSON');continue
  if not isinstance(d,dict):warnings.append(name+': expected object');continue
  if name=='manifest.json':
   mc=d.get('minecraft',{})
   if not isinstance(mc,dict):warnings.append(name+': missing minecraft object');continue
   observe('minecraft',mc.get('version'),name);observe('java_requirement_declared',mc.get('javaVersion'),name)
   loaders=mc.get('modLoaders',[])
   if not isinstance(loaders,list):warnings.append(name+': invalid modLoaders');loaders=[]
   for item in loaders:
    value=item.get('id','') if isinstance(item,dict) else ''
    for prefix,loader in LOADERS.items():
     if value.startswith(prefix):observe(loader,value[len(prefix):],name)
  elif name=='modrinth.index.json':
   deps=d.get('dependencies',{})
   if isinstance(deps,dict):
    for key,kind in {'minecraft':'minecraft','forge':'forge','neoforge':'neoforge','fabric-loader':'fabric','quilt-loader':'quilt'}.items():observe(kind,deps.get(key),name)
  else:
   components=d.get('components',[])
   if not isinstance(components,list):warnings.append(name+': invalid components');components=[]
   for c in components:
    if isinstance(c,dict):
     uid=c.get('uid');kind='minecraft' if uid=='net.minecraft' else COMPONENTS.get(uid)
     if kind:observe(kind,c.get('version'),name)
 for directory in ('kubejs','config/ftbquests','config/fancymenu','patchouli_books','tacz','resourcepacks'):
  start=root/directory
  if not start.is_dir() or start.is_symlink():continue
  for folder,dirs,files in os.walk(start,followlinks=False):
   dirs[:]=[d for d in dirs if d.lower() not in {'cache','logs','saves'} and not (Path(folder)/d).is_symlink() and not (hasattr(Path(folder)/d,'is_junction') and (Path(folder)/d).is_junction())]
   for name in files:
    p=Path(folder)/name
    if p.is_symlink():continue
    suffix=p.suffix.lower()
    if suffix in {'.snbt','.js','.json','.toml','.lang','.png','.zip','.txt'}:formats[directory+':'+suffix]+=1
 mods=root/'mods';jars=[]
 if mods.is_dir() and not mods.is_symlink():jars=sorted(p for p in mods.glob('*.jar') if p.is_file() and not p.is_symlink())
 for jar in jars:
  try:
   with zipfile.ZipFile(jar) as z:
    names=z.namelist()
    if len(names)>100000:warnings.append('mods/'+jar.name+': too many ZIP entries');continue
    members=set(names)
    for member,loader in {'META-INF/mods.toml':'forge_or_compatible','META-INF/neoforge.mods.toml':'neoforge','fabric.mod.json':'fabric','quilt.mod.json':'quilt'}.items():
     if member in members:indicators[loader]+=1
    for member in names:
     parts=member.split('/')
     if len(parts)==4 and parts[0]=='assets' and parts[2]=='lang' and (parts[3].endswith('.json') or parts[3].endswith('.lang')):
      locales.add(parts[3].rsplit('.',1)[0]);formats['jar_language:'+parts[3].rsplit('.',1)[1]]+=1
  except (OSError,zipfile.BadZipFile):warnings.append('mods/'+jar.name+': unreadable archive')
 by_kind=collections.defaultdict(set)
 for o in observations:by_kind[o['kind']].add(o['version'])
 conflicts={kind:sorted(values) for kind,values in by_kind.items() if len(values)>1}
 registry=json.loads((Path(__file__).resolve().parent.parent/'assets/capabilities.json').read_text(encoding='utf-8'))
 return {'schema':1,'metadata_observations':observations,'conflicting_metadata':conflicts,'source_metadata_hashes':evidence,'top_level_source_jars':len(jars),'loader_indicators_not_definitive':dict(indicators),'detected_language_locales':sorted(locales),'format_counts':dict(sorted(formats.items())),'capabilities':registry['capabilities'],'warnings':warnings,'selection_required':{'source_locale':True,'target_locale':True,'delivery_target':True,'translation_provider_and_budget':True},'instance_modified':False,'runtime_compatibility_verified':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('instance');p.add_argument('--output',required=True);a=p.parse_args()
 try:
  root=Path(a.instance).resolve(strict=True);out=Path(a.output).resolve()
  if out.is_relative_to(root):raise ValueError('output must be outside instance')
  if out.exists():raise FileExistsError('existing profile will not be overwritten')
  result=profile(root);out.parent.mkdir(parents=True,exist_ok=True)
  with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
  print(json.dumps({'profile':str(out),'metadata_conflicts':len(result['conflicting_metadata']),'runtime_verified':False}));return 0
 except (ValueError,OSError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
