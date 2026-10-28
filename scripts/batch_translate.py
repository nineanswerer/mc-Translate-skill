"""Resumable, budget-shared batch coordinator for translate_driver (single coordinator per state)."""
import argparse, concurrent.futures, contextlib, hashlib, importlib, json, os, re, sqlite3, sys, tempfile, time
from collections import deque
from pathlib import Path

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.batch-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

@contextlib.contextmanager
def coordinator_lock(path):
    p=Path(str(path)+'.lock');p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a+b') as f:
        f.seek(0,2)
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('another coordinator holds the state lock')
        else:
            import fcntl
            try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('another coordinator holds the state lock')
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def load_modules(scripts_dir):
    location=Path(scripts_dir).resolve()
    if not (location/'translate_driver.py').is_file() or not (location/'text_guard.py').is_file():raise ValueError('scripts directory lacks translate_driver.py or text_guard.py')
    sys.path.insert(0,str(location))
    return importlib.import_module('translate_driver'),importlib.import_module('text_guard')

def plan_rows(plan,guard,batch_size):
    if plan.get('schema')!=1 or not isinstance(plan.get('items'),list) or not isinstance(plan.get('request_base'),dict):raise ValueError('plan requires schema 1, items and request_base')
    base=plan['request_base']
    if any(k in base for k in ('entries','source','key')):raise ValueError('request_base must not contain entries/source/key')
    if not isinstance(base.get('model'),str) or not base['model']:raise ValueError('request_base.model is required')
    if not isinstance(base.get('prompt_version'),str) or not base['prompt_version'] or not isinstance(base.get('glossary_version'),str) or not base['glossary_version']:raise ValueError('request_base must pin prompt_version and glossary_version')
    if not 1<=batch_size<=100:raise ValueError('batch size must be 1..100')
    ids=set();groups={};items=[]
    locked_types={'locked','functional','technical','identifier','id','code','recipe','command','url','nbt','number'}
    for row in plan['items']:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or row['id'] in ids:raise ValueError('every item requires a unique string id')
        ids.add(row['id']);result={'id':row['id'],'key':row.get('key',''),'status':'skipped','reason':None}
        if row.get('status')!='pending_translation':result['reason']='not_explicitly_pending_translation'
        elif row.get('already_correct') is True or any(isinstance(row.get(field),str) and bool(row[field].strip()) for field in ('existing_translation','existing_zh')):result['reason']='existing_translation_preserved'
        elif row.get('locked') is True or str(row.get('kind','')).lower() in locked_types or guard.classification(row.get('key','')).startswith('locked_'):result['reason']='functional_or_structural_field_locked'
        elif not isinstance(row.get('source'),str) or not row['source']:result['reason']='missing_source_needs_review'
        elif re.search(r'[\u3400-\u9fff]',row['source']):result['reason']='source_contains_chinese_needs_review'
        elif re.fullmatch(r'[?？\s]+',row['source']):result['reason']='intentional_unknown_marker_needs_review'
        else:
            kind=row.get('kind');key=row.get('key');context=row.get('context')
            if not isinstance(kind,str) or not kind or not isinstance(key,str) or not key:
                result['reason']='missing_key_or_kind_needs_review';items.append(result);continue
            # With missing context, even equal text is isolated by full key. Explicit context permits reuse.
            grouping={'source':row['source'],'context':context if context not in (None,'',{}) else {'unprovided_context_key':key},'kind':kind,'locked':False}
            token=fingerprint(grouping)
            if token not in groups:groups[token]={'entry':{'source':row['source'],'key':key,'context':context,'kind':kind},'members':[]}
            groups[token]['members'].append(len(items));result.update(status='pending',reason=None,group=token)
        items.append(result)
    keys=list(groups);batches=[]
    for i in range(0,len(keys),batch_size):
        selected=keys[i:i+batch_size];request=dict(base);request['entries']=[groups[x]['entry'] for x in selected]
        batches.append({'id':fingerprint(request),'groups':selected,'request':request})
    return items,groups,batches

def identity(driver,request,adapter):return driver.ledger.request_key({'protocol':'local-json-v1','provider':adapter['provider'],'adapter_identity':adapter.get('identity',adapter['command']),'request':request})
def inspect_ledger(driver,db,request,adapter):
    key=identity(driver,request,adapter);conn=driver.ledger.connect(db)
    try:
        cached=conn.execute("SELECT id,output FROM requests WHERE request_hash=? AND status='settled' AND validated=1 ORDER BY id DESC LIMIT 1",(key,)).fetchone()
        active=conn.execute("SELECT id,status FROM requests WHERE request_hash=? AND status IN ('reserved','uncertain')",(key,)).fetchone()
        invalid=conn.execute("SELECT id FROM requests WHERE request_hash=? AND status='settled' AND validated=0 AND retry_authorized=0 ORDER BY id DESC LIMIT 1",(key,)).fetchone()
        if cached:return {'kind':'cached','id':cached['id'],'translations':json.loads(cached['output'])}
        if active:return {'kind':'already_in_flight','id':active['id'],'status':active['status']}
        if invalid:return {'kind':'paid_output_needs_review','id':invalid['id']}
        return {'kind':'available'}
    finally:conn.close()

def coordinate(plan,adapter,db,state_path,output_path,batch_size,max_cost,workers,retries,backoff,driver,guard,plan_bytes=None,pause_file=None):
    if not 1<=workers<=8 or not 0<=retries<=4 or not 0<=backoff<=5:raise ValueError('workers 1..8, retries 0..4, backoff 0..5 seconds')
    if driver.ledger.units(max_cost)<=0:raise ValueError('max-cost must be positive')
    # Reject credentials in the immutable request/config identity before touching the ledger.
    driver.ledger.request_key({'base':plan.get('request_base',{}),'provider':adapter.get('provider'),'identity':adapter.get('identity',adapter.get('command'))})
    items,groups,batches=plan_rows(plan,guard,batch_size)
    # Scan every would-be external request before dispatching any parallel worker.
    for batch in batches:identity(driver,batch['request'],adapter)
    pause_file=Path(pause_file).resolve() if pause_file else None
    config_hash=fingerprint({'plan':hashlib.sha256(plan_bytes).hexdigest() if plan_bytes is not None else fingerprint(plan),'provider':adapter.get('provider'),'identity':adapter.get('identity',adapter.get('command')),'currency':adapter.get('currency'),'batch_size':batch_size,'max_cost':str(max_cost),'ledger_path':str(Path(db).resolve()).casefold(),'output_path':str(Path(output_path).resolve()).casefold(),'pause_path':str(pause_file).casefold() if pause_file else None})
    state_path=Path(state_path);output_path=Path(output_path)
    if state_path.resolve()==output_path.resolve() or state_path.resolve()==Path(db).resolve() or output_path.resolve()==Path(db).resolve():raise ValueError('state, output and ledger must be separate files')
    if pause_file and pause_file in {state_path.resolve(),output_path.resolve(),Path(db).resolve()}:raise ValueError('pause marker must be a separate file')
    with coordinator_lock(state_path):
        if state_path.exists():
            state=json.loads(state_path.read_text(encoding='utf-8'))
            if state.get('schema')!=1 or state.get('configuration_hash')!=config_hash:raise ValueError('plan, adapter identity, budget parameters or ledger changed; keep the original state and audit before a new plan')
            if set(state['batches'])!={b['id'] for b in batches}:raise ValueError('persisted batch set differs from plan')
            if output_path.exists():
                previous=state.get('output_sha256')
                if not previous or hashlib.sha256(output_path.read_bytes()).hexdigest()!=previous:raise ValueError('output report changed or is untracked; protect the existing file and audit before resuming')
            elif state.get('output_sha256'):raise ValueError('previously recorded output report is missing; audit before resuming')
        else:
            if output_path.exists():raise ValueError('refusing to overwrite untracked output')
            state={'schema':1,'configuration_hash':config_hash,'batches':{b['id']:{'status':'pending','not_sent_attempts':0} for b in batches}}
            state_path.parent.mkdir(parents=True,exist_ok=True)
            with state_path.open('x',encoding='utf-8') as f:json.dump(state,f,ensure_ascii=False,indent=2)
        byid={b['id']:b for b in batches};queue=deque();translations={};stop=None
        def adopt(batch,values):
            if not isinstance(values,list) or len(values)!=len(batch['groups']):return False
            for group,value in zip(batch['groups'],values):
                if not isinstance(value,str):return False
                for index in groups[group]['members']:
                    row=plan['items'][index]
                    if not guard.validate(row['source'],value,row['key'])['ok']:return False
            for group,value in zip(batch['groups'],values):translations[group]=value
            return True
        for b in batches:
            item=state['batches'][b['id']];record=inspect_ledger(driver,db,b['request'],adapter)
            if record['kind']=='cached':
                item.update(status='completed',request_id=record['id'])
                if not adopt(b,record['translations']):item['status']='replica_validation_failed';stop=stop or item['status']
            elif record['kind'] in {'already_in_flight','paid_output_needs_review'}:
                item.update(status=record['kind'],request_id=record['id']);stop=stop or record['kind']
            elif item['status']=='completed':item['status']='cache_missing';stop=stop or 'cache_missing'
            elif item.get('not_sent_attempts',0)>retries:item['status']='not_sent_exhausted';stop=stop or item['status']
            else:item['status']='pending';queue.append(b['id'])
        atomic_json(state_path,state)
        pending={}
        def request_if_unpaused(request):
            if pause_file and pause_file.exists():return {'decision':'paused_by_user'}
            return driver.run(db,request,adapter,max_cost)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            while pending or (queue and stop is None):
                while queue and len(pending)<workers and stop is None:
                    if pause_file and pause_file.exists():stop='paused_by_user';break
                    bid=queue.popleft();b=byid[bid];state['batches'][bid]['status']='running';atomic_json(state_path,state)
                    pending[pool.submit(request_if_unpaused,b['request'])]=bid
                if not pending:break
                done,_=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    bid=pending.pop(future);b=byid[bid];item=state['batches'][bid]
                    try:result=future.result()
                    except Exception:item['status']='coordinator_error';stop=stop or item['status'];continue
                    outcome=result.get('decision','unknown');item['status']=outcome
                    item['request_id']=result.get('request_id',result.get('id',result.get('ledger',{}).get('id')))
                    if outcome in {'translated','cached'}:
                        if adopt(b,result.get('translations')):item['status']='completed'
                        else:item['status']='replica_validation_failed';stop=stop or item['status']
                    elif outcome=='not_sent':
                        item['not_sent_attempts']=item.get('not_sent_attempts',0)+1
                        if item['not_sent_attempts']<=retries:
                            atomic_json(state_path,state);time.sleep(min(5,backoff*2**(item['not_sent_attempts']-1)));item['status']='pending';queue.append(bid)
                        else:item['status']='not_sent_exhausted';stop=stop or item['status']
                    else:stop=stop or outcome
                    atomic_json(state_path,state)
        # Preserve unfinished batches exactly and emit a row for every input item.
        for item in items:
            group=item.pop('group',None)
            if group is not None and group in translations:item.update(status='translated_structural_check_only',translation=translations[group])
            elif group is not None:
                b=next(x for x in batches if group in x['groups']);item.update(status='pending_or_blocked',reason=state['batches'][b['id']]['status'])
        counts={k:sum(x['status']==k for x in items) for k in ('skipped','translated_structural_check_only','pending_or_blocked')}
        review_pending=any(str(item.get('reason','')).endswith('_needs_review') for item in items)
        report={'schema':1,'configuration_hash':config_hash,'status':'blocked' if stop else 'needs_review' if review_pending else 'complete_structural_only','stop_reason':stop,'counts':counts,'unique_translation_groups':len(groups),'batches':len(batches),'semantic_review_required':True,'user_game_acceptance':False,'items':items,'budget':driver.ledger.status(db)}
        atomic_json(state_path,state)
        expected_output=state.get('output_sha256')
        if output_path.exists():
            if not expected_output or hashlib.sha256(output_path.read_bytes()).hexdigest()!=expected_output:raise ValueError('output report changed during execution; leave it intact')
        elif expected_output:raise ValueError('previous output report disappeared during execution')
        atomic_json(output_path,report)
        state['output_sha256']=hashlib.sha256(output_path.read_bytes()).hexdigest();atomic_json(state_path,state)
        return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','adapter','db','state','output','max-cost'):p.add_argument('--'+name,required=True)
    p.add_argument('--scripts-dir',default=str(Path(__file__).resolve().parent));p.add_argument('--batch-size',type=int,default=8);p.add_argument('--workers',type=int,default=1);p.add_argument('--max-not-sent-retries',type=int,default=1);p.add_argument('--backoff',type=float,default=1)
    p.add_argument('--pause-file',help='existing marker requests pause after in-flight calls finish; remove only after user authorizes resume')
    a=p.parse_args()
    try:
        driver,guard=load_modules(a.scripts_dir);raw=Path(a.plan).read_bytes();plan=json.loads(raw.decode('utf-8'));adapter=json.loads(Path(a.adapter).read_text(encoding='utf-8'))
        result=coordinate(plan,adapter,a.db,a.state,a.output,a.batch_size,a.max_cost,a.workers,a.max_not_sent_retries,a.backoff,driver,guard,plan_bytes=raw,pause_file=a.pause_file)
        print(json.dumps({k:result[k] for k in ('status','stop_reason','counts','unique_translation_groups','batches','budget')},ensure_ascii=False));return 0 if result['status']=='complete_structural_only' else 2
    except (ValueError,KeyError,OSError,RuntimeError,sqlite3.Error) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
