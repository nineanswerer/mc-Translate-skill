"""Inspect quarantined paid output, then revalidate an offline repair into the same cache."""
import argparse,json,sys
from pathlib import Path
import text_guard, translation_ledger as ledger, translate_driver as driver

def recover(db,rid,request,adapter,candidates=None):
    record=ledger.quarantine_read(db,rid)
    identity={'protocol':'local-json-v1','provider':adapter['provider'],'adapter_identity':adapter.get('identity',adapter['command']),'request':request}
    if ledger.request_key(identity)!=record['request_hash']:raise ValueError('request/provider identity does not match the paid response')
    if record['status']!='settled':raise ValueError('reconcile billing before caching an uncertain response')
    rows=driver.entries(request);saved=record['quarantine'] or {}
    if candidates is None:
        candidates=saved.get('translations')
        if not isinstance(candidates,list) and len(rows)==1:
            # Only a clearly named single translation is safe to promote automatically.
            # Raw provider content may be fenced JSON, an error, or other non-display text.
            value=saved.get('translation') if isinstance(saved.get('translation'),str) else None
            candidates=[value] if value is not None else None
    if not isinstance(candidates,list) or len(candidates)!=len(rows) or not all(isinstance(x,str) for x in candidates):raise ValueError('recoverable batch has wrong shape; supply a repaired candidates JSON array')
    if any(any(marker in value for marker in ('[REDACTED]','[REDACTED_UUID]','[TRUNCATED]','[OMITTED]')) for value in candidates):raise ValueError('redacted or truncated output cannot be cached; repair separately')
    if json.loads(driver.quarantine({'translations':candidates})).get('translations')!=candidates:raise ValueError('candidate contains sensitive or oversized material')
    errors=[{'index':i,'errors':result['errors']} for i,(row,out) in enumerate(zip(rows,candidates)) if not (result:=text_guard.validate(row['source'],out,row.get('key','')))['ok']]
    if errors:return {'status':'still_invalid','errors':errors,'extra_api_calls':0}
    result=ledger.promote(db,rid,record['request_hash'],candidates)
    result.update({'structural_validation_passed':True,'semantic_review_required':True,'extra_api_calls':0});return result

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    x=sub.add_parser('show');x.add_argument('--db',required=True);x.add_argument('--id',type=int,required=True)
    x=sub.add_parser('revalidate');x.add_argument('--db',required=True);x.add_argument('--id',type=int,required=True);x.add_argument('--request',required=True);x.add_argument('--adapter',required=True);x.add_argument('--candidates',help='optional JSON array with an offline repair')
    a=p.parse_args()
    try:
        if a.action=='show':result=ledger.quarantine_read(a.db,a.id)
        else:
            req=json.loads(Path(a.request).read_text(encoding='utf-8'));adapter=json.loads(Path(a.adapter).read_text(encoding='utf-8'));candidates=json.loads(Path(a.candidates).read_text(encoding='utf-8')) if a.candidates else None
            result=recover(a.db,a.id,req,adapter,candidates)
        print(json.dumps(result,ensure_ascii=False));return 0 if result.get('status')!='still_invalid' else 2
    except (ValueError,KeyError,OSError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
