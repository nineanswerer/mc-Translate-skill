"""Invoke one user-configured local adapter under a shared transactional budget. Never handles API keys."""
import argparse, json, os, re, sqlite3, subprocess, sys
from pathlib import Path
import text_guard, translation_ledger as ledger

def quarantine(response):
    """Keep only bounded content/usage fields, removing credential-shaped strings and UUIDs."""
    allowed={'status','translations','translation','text','content','result','cost','usage'}
    def clean(v,depth=0):
        if depth>8:return '[OMITTED]'
        if isinstance(v,str):
            v=re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=?','[REDACTED]',v)
            v=re.sub(r'(?i)\bsk-[A-Za-z0-9_-]{12,}\b','[REDACTED]',v)
            v=re.sub(r'(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b','[REDACTED_UUID]',v)
            v=re.sub(r'(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization)\s*[:=]\s*["\']?[^\s"\',;]+',r'\1=[REDACTED]',v)
            return v[:100_000]+('[TRUNCATED]' if len(v)>100_000 else '')
        if isinstance(v,list):return [clean(x,depth+1) for x in v[:512]]
        if isinstance(v,dict):return {k:clean(x,depth+1) for k,x in v.items() if k in {'translations','translation','text','content','result','message'}}
        return v if v is None or isinstance(v,(bool,int,float)) else '[OMITTED]'
    reduced={k:clean(v) for k,v in response.items() if k in allowed and k!='usage'}
    usage=response.get('usage',{})
    if isinstance(usage,dict):reduced['usage']={k:usage[k] for k in ('input_tokens','output_tokens','total_tokens') if k in usage and isinstance(usage[k],int) and 0<=usage[k]<=10**12}
    encoded=json.dumps(reduced,ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8'))>1_000_000:
        encoded=json.dumps({'truncated':True,'content_excerpt':encoded[:200_000]},ensure_ascii=False)
    return encoded

def entries(request):
    rows=request.get('entries')
    if rows is None:rows=[{'source':request['source'],'key':request.get('key','')}]
    if not isinstance(rows,list) or not rows or any(not isinstance(r,dict) or not isinstance(r.get('source'),str) for r in rows):raise ValueError('entries must be a non-empty array of {source, key?, context?}')
    return rows

def run(db,request,adapter,max_cost):
    rows=entries(request);command=adapter.get('command');provider=adapter.get('provider');timeout=adapter.get('timeout_seconds',120)
    if not provider or not isinstance(command,list) or not command or not all(isinstance(x,str) and x for x in command):raise ValueError('adapter provider and non-empty command argv required')
    if adapter.get('enforces_max_cost') is not True:raise ValueError('adapter must enforce the stated worst-case request ceiling')
    if adapter.get('currency')!=ledger.status(db)['currency']:raise ValueError('adapter currency differs from ledger')
    if not isinstance(timeout,(int,float)) or not 0<timeout<=3600:raise ValueError('invalid adapter timeout')
    if any(x.lower().startswith(('--api-key','--access-token','--authorization','--password','--secret')) for x in command):raise ValueError('do not put secrets in adapter argv')
    # Include backend, model and every prompt/context/glossary/config value; the database stores only this hash.
    identity={'protocol':'local-json-v1','provider':provider,'adapter_identity':adapter.get('identity',command),'request':request}
    decision=ledger.reserve(db,identity,max_cost)
    if decision['decision']=='cached':return {'decision':'cached','translations':json.loads(decision['translation']),'request_id':decision['id']}
    if decision['decision']!='reserved':return decision
    rid=decision['id'];packet={'protocol':'local-json-v1','request_id':rid,'request':request,'max_cost':decision['max_cost'],'currency':adapter['currency']}
    response=None
    try:
        env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['PYTHONUTF8']='1'
        proc=subprocess.run(command,input=json.dumps(packet,ensure_ascii=False).encode('utf-8'),capture_output=True,timeout=timeout,shell=False,env=env)
        if proc.returncode!=0:raise RuntimeError('adapter_exit_nonzero')
        if len(proc.stdout)>8_000_000:raise RuntimeError('adapter_response_too_large')
        response=json.loads(proc.stdout.decode('utf-8'))
        if not isinstance(response,dict):raise ValueError('invalid adapter response')
        state=response.get('status')
        if state=='not_sent':
            # Only permitted when the trusted adapter can establish failure before any external submission.
            return {'decision':'not_sent','ledger':ledger.mutate(db,rid,'not_sent',note='adapter certified no external request was submitted')}
        usage=response.get('usage')
        if state in {'success','failed_billed'} and (not isinstance(usage,dict) or any(isinstance(usage.get(k),bool) or not isinstance(usage.get(k),int) or not 0<=usage[k]<=10**12 for k in ('input_tokens','output_tokens'))):raise ValueError('missing or invalid usage; billing must remain uncertain')
        if state=='failed_billed':
            return {'decision':'failed_billed','ledger':ledger.mutate(db,rid,'settle',actual_cost=response['cost'],note='adapter reported billed failure; output not cached',quarantine=quarantine(response),bound_violation=response.get('bound_violation') is True)}
        if state!='success' or 'cost' not in response:raise ValueError('unrecognized response or unknown billing')
        translated=response.get('translations')
        if not isinstance(translated,list) or len(translated)!=len(rows) or not all(isinstance(x,str) for x in translated):
            settle=ledger.mutate(db,rid,'settle',actual_cost=response['cost'],note='paid response had invalid output shape',quarantine=quarantine(response),bound_violation=response.get('bound_violation') is True)
            return {'decision':'invalid_output','errors':['batch_size_or_type_mismatch'],'ledger':settle}
        errors=[{'index':i,'errors':v['errors']} for i,(row,out) in enumerate(zip(rows,translated)) if not (v:=text_guard.validate(row['source'],out,row.get('key','')))['ok']]
        if json.loads(quarantine({'translations':translated})).get('translations')!=translated:errors.append({'index':None,'errors':['sensitive_or_truncated_output_requires_review']})
        safe_usage={k:usage[k] for k in ('input_tokens','output_tokens','total_tokens') if isinstance(usage,dict) and k in usage and isinstance(usage[k],int) and 0<=usage[k]<=10**12}
        settle=ledger.mutate(db,rid,'settle',actual_cost=response['cost'],validated=not errors,output=json.dumps(translated,ensure_ascii=False) if not errors else None,note=json.dumps({'usage':safe_usage,'validated':not errors}),quarantine=quarantine(response) if errors else None,bound_violation=response.get('bound_violation') is True)
        return {'decision':'invalid_output' if errors else 'translated','translations':translated if not errors else None,'errors':errors,'ledger':settle}
    except (subprocess.TimeoutExpired,UnicodeError,RuntimeError,ValueError,KeyError,OSError,json.JSONDecodeError):
        # The remote may have processed the request even when the child failed or the response was lost.
        return {'decision':'uncertain','request_id':rid,'ledger':ledger.mutate(db,rid,'uncertain',note='adapter failure or response/billing unknown; reconcile before retry',quarantine=quarantine(response) if isinstance(response,dict) else None)}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',required=True);p.add_argument('--request',required=True);p.add_argument('--adapter',required=True);p.add_argument('--max-cost',required=True)
    a=p.parse_args()
    try:
        result=run(a.db,json.loads(Path(a.request).read_text(encoding='utf-8')),json.loads(Path(a.adapter).read_text(encoding='utf-8')),a.max_cost)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['decision'] in {'translated','cached'} else 2
    except (ValueError,OSError,KeyError,sqlite3.Error) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
