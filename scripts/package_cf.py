"""Package an already reviewed staging tree as a CurseForge-style ZIP, without touching the instance."""
import argparse, hashlib, json, os, sys, zipfile
from pathlib import Path, PurePosixPath

PRIVATE_DIRS={'saves','logs','crash-reports','journeymap','.connector'}
PRIVATE_FILES={'usercache.json','usernamecache.json','defaultoptions.journal.json','servers.dat','last_world.fmdata'}

def digest(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def build(stage,manifest_file,output):
 stage=Path(stage).resolve(strict=True);manifest_file=Path(manifest_file).resolve(strict=True);out=Path(output).resolve()
 if not stage.is_dir() or out.is_relative_to(stage) or manifest_file.is_relative_to(stage):raise ValueError('stage, manifest and output must be separate')
 partial=out.with_name(out.name+'.partial')
 if out.exists() or partial.exists():raise FileExistsError('refusing to replace an existing output or partial')
 data=json.loads(manifest_file.read_text(encoding='utf-8-sig'))
 if not isinstance(data,dict) or not isinstance(data.get('minecraft'),dict):raise ValueError('manifest minecraft object missing')
 mc=data['minecraft']
 if data.get('manifestType')!='minecraftModpack' or data.get('manifestVersion')!=1 or data.get('overrides')!='overrides' or not isinstance(mc.get('version'),str) or not isinstance(mc.get('modLoaders'),list) or not isinstance(data.get('files'),list) or not data.get('name') or not data.get('version'):raise ValueError('invalid or incomplete CurseForge manifest')
 for x in data['files']:
  if not isinstance(x,dict) or not isinstance(x.get('projectID'),int) or not isinstance(x.get('fileID'),int):raise ValueError('each online file needs projectID and fileID')
 entries=[];names=set()
 for p in sorted(stage.rglob('*')):
  if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('symbolic links or junctions are not supported')
  if not p.is_file():continue
  rel=p.relative_to(stage).as_posix();parts=PurePosixPath(rel).parts
  if any(part.lower() in PRIVATE_DIRS for part in parts) or p.name.lower() in PRIVATE_FILES or p.suffix.lower() in {'.env','.log','.sqlite','.db','.input'}:raise ValueError('unreviewed personal data, secrets, or cache in staged input: '+rel)
  if rel.casefold() in names:raise ValueError('case-insensitive duplicate path')
  names.add(rel.casefold());entries.append((p,rel,digest(p)))
 if not entries:raise ValueError('empty stage')
 if not data['files'] and not any(repr_path.startswith('mods/') and repr_path.endswith('.jar') for _,repr_path,_ in entries):raise ValueError('no online entries or staged source mods')
 out.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(partial,'x',zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
  z.writestr('manifest.json',json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))
  for p,rel,h in entries:
   if digest(p)!=h:raise ValueError('staged input changed while packaging')
   z.write(p,'overrides/'+rel,compress_type=zipfile.ZIP_STORED if p.suffix.lower() in {'.jar','.zip','.png','.ogg','.mp4'} else zipfile.ZIP_DEFLATED)
 with zipfile.ZipFile(partial) as z:
  names=z.namelist()
  if len(names)!=len(set(n.casefold() for n in names)) or z.testzip() is not None:raise ValueError('duplicate ZIP path or CRC error')
  for p,rel,h in entries:
   if hashlib.sha256(z.read('overrides/'+rel)).hexdigest()!=h or digest(p)!=h:raise ValueError('payload mismatch or staged source changed')
 # Atomic publish without replacing an unknown existing file; os.link fails if out was created concurrently.
 try:os.link(partial,out)
 except FileExistsError:raise FileExistsError('output created concurrently; partial preserved')
 partial.unlink()
 return {'archive':str(out),'bytes':out.stat().st_size,'sha256':digest(out),'payloads_verified':len(entries),'crc_verified':True,'actual_installer_tested':False,'game_launch_tested':False,'privacy_review_required':True}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',required=True);p.add_argument('--manifest',required=True);p.add_argument('--out',required=True);a=p.parse_args()
 try:print(json.dumps(build(a.stage,a.manifest,a.out),ensure_ascii=False));return 0
 except (OSError,ValueError,zipfile.BadZipFile) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
