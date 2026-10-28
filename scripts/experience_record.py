"""Validate scoped lessons against local evidence hashes; record immutably without editing rules."""
import argparse,hashlib,json,re,sys
from pathlib import Path,PurePosixPath

def record(entry,evidence_root,store):
 if not isinstance(entry,dict):raise ValueError('entry must be an object')
 name=entry.get('id','')
 if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',name):raise ValueError('safe unique id required')
 if entry.get('status') not in {'observed','inferred','confirmed'}:raise ValueError('explicit evidence status required')
 if entry.get('applicability') not in {'project_only','candidate_rule'}:raise ValueError('a record cannot automatically become a universal rule')
 scope=entry.get('scope');finding=entry.get('finding');evidence=entry.get('evidence')
 if not isinstance(scope,dict) or not scope or not isinstance(finding,str) or not finding.strip() or not isinstance(evidence,list) or not evidence:raise ValueError('scope, finding and evidence are required')
 if re.search(r'(?i)\b(?:sk-[a-z0-9_-]{16,}|Bearer\s+[a-z0-9._-]{12,}|(?:password|api[_-]?key|access[_-]?token)\s*[:=])',json.dumps(entry)):raise ValueError('possible credential in record')
 base=Path(evidence_root).resolve(strict=True)
 for item in evidence:
  rel=item.get('path','') if isinstance(item,dict) else ''
  p=PurePosixPath(rel)
  if not rel or p.is_absolute() or '..' in p.parts or '\\' in rel or ':' in rel:raise ValueError('evidence paths must be task-relative')
  target=base
  for part in p.parts:
   target=target/part
   if target.is_symlink() or (hasattr(target,'is_junction') and target.is_junction()):raise ValueError('linked evidence is not supported')
  if not target.resolve().is_relative_to(base) or not target.is_file():raise ValueError('missing or escaping evidence')
  with target.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  if digest!=item.get('sha256'):raise ValueError('evidence hash mismatch')
 if entry['status']=='confirmed' and not entry.get('regression_evidence'):raise ValueError('confirmed lessons need recorded regression evidence')
 dest=Path(store).resolve();dest.mkdir(parents=True,exist_ok=True);out=dest/(name+'.json')
 with out.open('x',encoding='utf-8') as f:json.dump(entry,f,ensure_ascii=False,indent=2)
 return {'record':out.name,'status':entry['status'],'evidence_hashes_verified':len(evidence),'rules_modified':False,'independent_regression_execution_verified':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--entry',required=True);p.add_argument('--evidence-root',required=True);p.add_argument('--store',required=True);a=p.parse_args()
 try:print(json.dumps(record(json.loads(Path(a.entry).read_text(encoding='utf-8')),a.evidence_root,a.store),ensure_ascii=False));return 0
 except (ValueError,OSError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
