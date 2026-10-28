"""Hash-guarded install and rollback for staged files; refuses if Java is running."""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path, PurePosixPath

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def current(path):return digest(path) if path.exists() else None
def atomic_json(path,obj):
    atomic_write(path,json.dumps(obj,ensure_ascii=False,indent=2).encode('utf-8'))
def atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.translation-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def safe_path(root,rel):
    p=PurePosixPath(rel)
    if not rel or p.is_absolute() or any(x in {'.','..'} for x in p.parts) or '\\' in rel or ':' in rel:raise ValueError('invalid relative path')
    root=Path(root).resolve();cur=root
    for part in p.parts:
        cur=cur/part
        if cur.is_symlink() or (hasattr(cur,'is_junction') and cur.is_junction()):raise ValueError('symlink or junction in target path')
    if not cur.resolve().is_relative_to(root):raise ValueError('path escapes root')
    return cur
def ensure_java_stopped():
    if os.name=='nt':
        for name in ('java.exe','javaw.exe'):
            p=subprocess.run(['tasklist','/FI','IMAGENAME eq '+name,'/NH','/FO','CSV'],capture_output=True,text=True,timeout=15)
            if p.returncode!=0:raise RuntimeError('unable to verify game process status')
            if '"'+name+'"' in p.stdout.lower():raise RuntimeError('Java is running; leave deployment pending')
    else:
        p=subprocess.run(['ps','-eo','comm='],capture_output=True,text=True,timeout=15)
        if p.returncode!=0:raise RuntimeError('unable to verify game process status')
        if any(Path(x.strip()).name.lower() in {'java','javaw','java.exe','javaw.exe'} for x in p.stdout.splitlines()):raise RuntimeError('Java is running; leave deployment pending')

def load(state):return json.loads((Path(state)/'transaction.json').read_text(encoding='utf-8'))
def save(state,obj):atomic_json(Path(state)/'transaction.json',obj)
def plan(stage,dest,state):
    stage=Path(stage).resolve();dest=Path(dest).resolve();state=Path(state).resolve()
    if not stage.is_dir() or not dest.is_dir():raise ValueError('stage and destination must exist')
    if state.exists():raise ValueError('state directory already exists; first backups are never overwritten')
    if state.is_relative_to(dest) or state.is_relative_to(stage) or stage.is_relative_to(dest) or dest.is_relative_to(stage):raise ValueError('stage, destination and state must be separate')
    entries=[];names=set()
    for src in sorted(stage.rglob('*')):
        if not src.is_file():continue
        rel=src.relative_to(stage).as_posix();s=safe_path(stage,rel);d=safe_path(dest,rel)
        if rel.casefold() in names:raise ValueError('case-insensitive duplicate target')
        names.add(rel.casefold());entries.append({'path':rel,'staged_sha256':digest(s),'original_sha256':current(d),'started':False})
    if not entries:raise ValueError('stage is empty')
    state.mkdir(parents=True,exist_ok=False)
    for r in entries:
        if r['original_sha256'] is None:continue
        target=safe_path(dest,r['path']);backup=safe_path(state/'backups',r['path']);backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(target,backup)
        if digest(backup)!=r['original_sha256'] or current(target)!=r['original_sha256']:raise RuntimeError('source changed while backing up')
    record={'format':1,'phase':'planned','stage':str(stage),'destination':str(dest),'files':entries}
    save(state,record);return {'phase':'planned','files':len(entries),'originals_backed_up':sum(x['original_sha256'] is not None for x in entries)}

def apply(state,guard=ensure_java_stopped):
    guard();obj=load(state);phase=obj['phase']
    if phase not in {'planned','applying'}:raise ValueError('transaction is not applicable')
    stage=Path(obj['stage']);dest=Path(obj['destination']);records=obj['files']
    for r in records:
        if digest(safe_path(stage,r['path']))!=r['staged_sha256']:raise RuntimeError('staged file changed; stop')
        got=current(safe_path(dest,r['path']));allowed={r['original_sha256']}
        if phase=='applying' and r['started']:allowed.add(r['staged_sha256'])
        if got not in allowed:raise RuntimeError('target modified since backup; stop')
        if r['original_sha256'] and digest(safe_path(Path(state)/'backups',r['path']))!=r['original_sha256']:raise RuntimeError('backup changed; stop')
    obj['phase']='applying';save(state,obj)
    for r in records:
        target=safe_path(dest,r['path']);got=current(target)
        if r['started'] and got==r['staged_sha256']:continue
        if got!=r['original_sha256']:raise RuntimeError('target changed during install; stop')
        r['started']=True;save(state,obj)
        data=safe_path(stage,r['path']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=r['staged_sha256']:raise RuntimeError('staged file changed during install; stop')
        atomic_write(target,data)
    for r in records:
        if current(safe_path(dest,r['path']))!=r['staged_sha256']:raise RuntimeError('post-install hash mismatch')
    obj['phase']='applied';save(state,obj);return {'phase':'applied','files':len(records),'hashes_verified':True}

def rollback(state,guard=ensure_java_stopped):
    guard();obj=load(state)
    if obj['phase'] not in {'planned','applying','applied','rolling_back'}:raise ValueError('transaction cannot be rolled back')
    dest=Path(obj['destination']);records=obj['files']
    for r in records:
        got=current(safe_path(dest,r['path']));allowed={r['original_sha256']}
        if r['started']:allowed.add(r['staged_sha256'])
        if got not in allowed:raise RuntimeError('a target was edited after install; rollback aborted')
        if r['original_sha256'] and digest(safe_path(Path(state)/'backups',r['path']))!=r['original_sha256']:raise RuntimeError('backup changed; rollback aborted')
    obj['phase']='rolling_back';save(state,obj)
    for r in records:
        target=safe_path(dest,r['path']);got=current(target)
        if got==r['original_sha256']:continue
        if not r['started'] or got!=r['staged_sha256']:raise RuntimeError('target changed during rollback; stop')
        if r['original_sha256'] is None:target.unlink()
        else:atomic_write(target,safe_path(Path(state)/'backups',r['path']).read_bytes())
    for r in records:
        if current(safe_path(dest,r['path']))!=r['original_sha256']:raise RuntimeError('post-rollback hash mismatch')
    obj['phase']='rolled_back';save(state,obj);return {'phase':'rolled_back','files':len(records),'original_hashes_verified':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    x=sub.add_parser('plan');x.add_argument('--stage',required=True);x.add_argument('--dest',required=True);x.add_argument('--state',required=True)
    for action in ('apply','rollback'):
        x=sub.add_parser(action);x.add_argument('--state',required=True)
    a=p.parse_args()
    try:
        r=plan(a.stage,a.dest,a.state) if a.action=='plan' else apply(a.state) if a.action=='apply' else rollback(a.state)
        print(json.dumps(r,ensure_ascii=False));return 0
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
