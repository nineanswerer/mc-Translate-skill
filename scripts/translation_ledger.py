"""Persistent, transactional single-budget translation accounting and cache; no networking."""
import argparse, hashlib, json, re, sqlite3, sys
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

SCALE=1_000_000
SECRET_FIELDS={'api_key','apikey','x_api_key','access_token','refresh_token','authorization','password','secret_key','bearer_token','client_secret'}

def units(value,ceiling=True):
    try:d=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('invalid amount')
    if not d.is_finite() or d<0:raise ValueError('amount must be finite and nonnegative')
    return int((d*SCALE).to_integral_value(rounding=ROUND_CEILING if ceiling else ROUND_FLOOR))
def amount(value):return format(Decimal(value)/SCALE,'.6f')
def request_key(request):
    def inspect(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if str(k).lower().replace('-','_') in SECRET_FIELDS:raise ValueError('request must not contain credentials; use local environment')
                inspect(v)
        elif isinstance(x,list):
            for v in x:inspect(v)
        elif isinstance(x,str) and re.search(r'(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._~+/-]{8,}|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization)\s*[:=]\s*["\']?[^\s"\',;]+)',x):raise ValueError('request appears to contain a credential; remove it before submission')
    inspect(request)
    canonical=json.dumps(request,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

def connect(path,create=False):
    if not create and not Path(path).is_file():raise ValueError('budget ledger missing or not initialized; no request sent')
    db=sqlite3.connect(str(path),timeout=30,isolation_level=None)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=30000')
    return db
def initialize(path,cap,currency):
    if not currency or len(currency)>12:raise ValueError('currency required')
    db=connect(path,create=True)
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1),cap INTEGER NOT NULL,currency TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT,request_hash TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('reserved','uncertain','settled','not_sent')),reserved INTEGER NOT NULL,actual INTEGER,validated INTEGER NOT NULL DEFAULT 0,output TEXT,quarantine TEXT,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,note TEXT);
        CREATE TABLE IF NOT EXISTS budget_changes (id INTEGER PRIMARY KEY AUTOINCREMENT,previous_cap INTEGER NOT NULL,new_cap INTEGER NOT NULL,authorization_note TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS bound_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,request_id INTEGER NOT NULL REFERENCES requests(id),evidence_note TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS retry_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,request_id INTEGER NOT NULL REFERENCES requests(id),evidence_note TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE UNIQUE INDEX IF NOT EXISTS one_inflight_per_request ON requests(request_hash) WHERE status IN ('reserved','uncertain');
        CREATE INDEX IF NOT EXISTS request_lookup ON requests(request_hash,id);''')
        if 'quarantine' not in {r['name'] for r in db.execute('PRAGMA table_info(requests)')}:db.execute('ALTER TABLE requests ADD COLUMN quarantine TEXT')
        if 'bound_violation' not in {r['name'] for r in db.execute('PRAGMA table_info(requests)')}:db.execute('ALTER TABLE requests ADD COLUMN bound_violation INTEGER NOT NULL DEFAULT 0')
        if 'bound_reviewed' not in {r['name'] for r in db.execute('PRAGMA table_info(requests)')}:db.execute('ALTER TABLE requests ADD COLUMN bound_reviewed INTEGER NOT NULL DEFAULT 0')
        if 'retry_authorized' not in {r['name'] for r in db.execute('PRAGMA table_info(requests)')}:db.execute('ALTER TABLE requests ADD COLUMN retry_authorized INTEGER NOT NULL DEFAULT 0')
        cap_units=units(cap,False)
        db.execute('BEGIN IMMEDIATE');existing=db.execute('SELECT cap,currency FROM meta WHERE id=1').fetchone()
        if existing and (existing['cap']!=cap_units or existing['currency']!=currency):raise ValueError('ledger exists with a different cap or currency; do not reset budget')
        if not existing:db.execute('INSERT INTO meta VALUES(1,?,?)',(cap_units,currency))
        db.execute('COMMIT')
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def account(db):
    try:meta=db.execute('SELECT * FROM meta WHERE id=1').fetchone()
    except sqlite3.OperationalError as exc:raise ValueError('budget ledger not initialized; no request sent') from exc
    if meta is None:raise ValueError('ledger not initialized')
    row=db.execute("SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual ELSE 0 END),0) AS spent,COALESCE(SUM(CASE WHEN status IN ('reserved','uncertain') THEN reserved ELSE 0 END),0) AS reserved FROM requests").fetchone()
    spent=row['spent']; reserved=row['reserved']; cap=meta['cap']
    frozen=db.execute("SELECT COUNT(*) FROM requests WHERE status='settled' AND bound_violation=1 AND bound_reviewed=0").fetchone()[0]
    return {'currency':meta['currency'],'cap':amount(cap),'settled':amount(spent),'reserved_or_uncertain':amount(reserved),'accounted':amount(spent+reserved),'remaining':amount(max(0,cap-spent-reserved)),'over_cap':spent+reserved>cap,'budget_frozen':bool(frozen),'unreviewed_bound_violations':frozen}

def reserve(path,request,max_cost):
    key=request_key(request);reserve_units=units(max_cost,True)
    if reserve_units<=0:raise ValueError('max_cost must be positive and cover the provider worst case')
    db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE');state=account(db)
        cached=db.execute("SELECT id,output FROM requests WHERE request_hash=? AND status='settled' AND validated=1 ORDER BY id DESC LIMIT 1",(key,)).fetchone()
        active=db.execute("SELECT id,status FROM requests WHERE request_hash=? AND status IN ('reserved','uncertain')",(key,)).fetchone()
        invalid=db.execute("SELECT id FROM requests WHERE request_hash=? AND status='settled' AND validated=0 AND retry_authorized=0 ORDER BY id DESC LIMIT 1",(key,)).fetchone()
        if cached:result={'decision':'cached','id':cached['id'],'translation':cached['output'],'request_hash':key}
        elif active:result={'decision':'already_in_flight','id':active['id'],'status':active['status'],'request_hash':key}
        elif invalid:result={'decision':'paid_output_needs_review','id':invalid['id'],'request_hash':key}
        elif state['budget_frozen']:result={'decision':'budget_frozen','request_hash':key,'budget':state}
        elif reserve_units>units(state['remaining'],False):result={'decision':'budget_exhausted','request_hash':key,'budget':state}
        else:
            cur=db.execute("INSERT INTO requests(request_hash,status,reserved) VALUES(?,'reserved',?)",(key,reserve_units))
            result={'decision':'reserved','id':cur.lastrowid,'request_hash':key,'max_cost':amount(reserve_units)}
        db.execute('COMMIT');return result
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def mutate(path,request_id,action,actual_cost=None,output=None,validated=False,note=None,quarantine=None,bound_violation=False):
    if action not in {'settle','uncertain','not_sent'}:raise ValueError('invalid action')
    if action=='settle' and actual_cost is None:raise ValueError('actual cost is required, including for failed validation')
    if action=='settle' and validated and not isinstance(output,str):raise ValueError('validated cache requires text output')
    if quarantine is not None and (not isinstance(quarantine,str) or len(quarantine.encode('utf-8'))>1_000_000):raise ValueError('quarantined response exceeds safe limit')
    if action=='not_sent' and not note:raise ValueError('document evidence that no request was submitted')
    db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE');r=db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
        if not r or r['status'] not in {'reserved','uncertain'}:raise ValueError('request is not unresolved')
        if action=='settle':
            actual=units(actual_cost,True)
            db.execute("UPDATE requests SET status='settled',actual=?,validated=?,output=?,quarantine=COALESCE(?,quarantine),bound_violation=?,note=?,updated=CURRENT_TIMESTAMP WHERE id=?",(actual,int(bool(validated)),output if validated else None,quarantine,int(bool(bound_violation or actual>r['reserved'])),note,request_id))
        elif action=='uncertain':db.execute("UPDATE requests SET status='uncertain',quarantine=COALESCE(?,quarantine),note=?,updated=CURRENT_TIMESTAMP WHERE id=?",(quarantine,note,request_id))
        else:db.execute("UPDATE requests SET status='not_sent',note=?,updated=CURRENT_TIMESTAMP WHERE id=?",(note,request_id))
        result={'id':request_id,'status':{'settle':'settled','uncertain':'uncertain','not_sent':'not_sent'}[action],'budget':account(db)}
        if action=='settle':result['actual_exceeded_reserved']=actual>r['reserved']
        db.execute('COMMIT');return result
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def status(path):
    db=connect(path)
    try:
        result=account(db);result['cap_changes']=db.execute('SELECT COUNT(*) FROM budget_changes').fetchone()[0];result['paid_invalid_needs_review']=db.execute("SELECT COUNT(*) FROM requests WHERE status='settled' AND validated=0 AND retry_authorized=0").fetchone()[0];result['unresolved']=[dict(x) for x in db.execute("SELECT id,request_hash,status,reserved,created,updated FROM requests WHERE status IN ('reserved','uncertain') ORDER BY id")]
        for r in result['unresolved']:r['reserved']=amount(r['reserved'])
        return result
    finally:db.close()

def set_cap(path,new_cap,authorization_note):
    if not authorization_note or not authorization_note.strip() or len(authorization_note)>500:raise ValueError('document explicit user authorization for this cap change')
    value=units(new_cap,False);db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT cap FROM meta WHERE id=1').fetchone()
        if not row:raise ValueError('ledger not initialized')
        if row['cap']!=value:
            db.execute('INSERT INTO budget_changes(previous_cap,new_cap,authorization_note) VALUES(?,?,?)',(row['cap'],value,authorization_note))
            db.execute('UPDATE meta SET cap=? WHERE id=1',(value,))
        result=account(db);db.execute('COMMIT');return result
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def review_bound(path,request_id,evidence_note):
    if not evidence_note or not evidence_note.strip() or len(evidence_note)>500:raise ValueError('document pricing and upper-bound review evidence')
    db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE');r=db.execute('SELECT status,bound_violation,bound_reviewed FROM requests WHERE id=?',(request_id,)).fetchone()
        if not r or r['status']!='settled' or not r['bound_violation'] or r['bound_reviewed']:raise ValueError('request has no unreviewed bound violation')
        db.execute('INSERT INTO bound_reviews(request_id,evidence_note) VALUES(?,?)',(request_id,evidence_note))
        db.execute('UPDATE requests SET bound_reviewed=1,updated=CURRENT_TIMESTAMP WHERE id=?',(request_id,))
        result=account(db);db.execute('COMMIT');return result
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def authorize_retry(path,request_id,evidence_note):
    if not evidence_note or not evidence_note.strip() or len(evidence_note)>500:raise ValueError('document unsuccessful offline recovery and reason to retry')
    db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE');r=db.execute('SELECT status,validated,retry_authorized FROM requests WHERE id=?',(request_id,)).fetchone()
        if not r or r['status']!='settled' or r['validated'] or r['retry_authorized']:raise ValueError('request has no unrecovered paid response')
        db.execute('INSERT INTO retry_reviews(request_id,evidence_note) VALUES(?,?)',(request_id,evidence_note))
        db.execute('UPDATE requests SET retry_authorized=1,updated=CURRENT_TIMESTAMP WHERE id=?',(request_id,))
        result=account(db);db.execute('COMMIT');return result
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def quarantine_read(path,request_id):
    db=connect(path)
    try:
        row=db.execute('SELECT id,request_hash,status,validated,quarantine FROM requests WHERE id=?',(request_id,)).fetchone()
        if row is None:raise ValueError('unknown request')
        out=dict(row);out['quarantine']=json.loads(out['quarantine']) if out['quarantine'] else None
        return out
    finally:db.close()

def promote(path,request_id,expected_hash,translations):
    db=connect(path)
    try:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT request_hash,status,validated FROM requests WHERE id=?',(request_id,)).fetchone()
        if not row or row['request_hash']!=expected_hash or row['status']!='settled':raise ValueError('request identity or confirmed billing does not match')
        if row['validated']:raise ValueError('request is already cached')
        db.execute("UPDATE requests SET validated=1,output=?,updated=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(translations,ensure_ascii=False),request_id))
        db.execute('COMMIT');return {'id':request_id,'status':'cached_after_offline_revalidation','budget':account(db)}
    except Exception:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    finally:db.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('init');x.add_argument('--db',required=True);x.add_argument('--cap',required=True);x.add_argument('--currency',required=True)
    x=sub.add_parser('status');x.add_argument('--db',required=True)
    x=sub.add_parser('set-cap');x.add_argument('--db',required=True);x.add_argument('--cap',required=True);x.add_argument('--authorization',required=True)
    x=sub.add_parser('review-bound');x.add_argument('--db',required=True);x.add_argument('--id',required=True,type=int);x.add_argument('--note',required=True)
    x=sub.add_parser('retry-invalid');x.add_argument('--db',required=True);x.add_argument('--id',required=True,type=int);x.add_argument('--note',required=True)
    x=sub.add_parser('reserve');x.add_argument('--db',required=True);x.add_argument('--request',required=True);x.add_argument('--max-cost',required=True)
    for op in ('uncertain','not-sent','settle'):
        x=sub.add_parser(op);x.add_argument('--db',required=True);x.add_argument('--id',required=True,type=int);x.add_argument('--note')
        if op=='settle':x.add_argument('--cost',required=True);x.add_argument('--validated-output')
    a=p.parse_args()
    try:
        if a.cmd=='init':initialize(a.db,a.cap,a.currency);r=status(a.db)
        elif a.cmd=='status':r=status(a.db)
        elif a.cmd=='set-cap':r=set_cap(a.db,a.cap,a.authorization)
        elif a.cmd=='review-bound':r=review_bound(a.db,a.id,a.note)
        elif a.cmd=='retry-invalid':r=authorize_retry(a.db,a.id,a.note)
        elif a.cmd=='reserve':r=reserve(a.db,json.loads(Path(a.request).read_text(encoding='utf-8')),a.max_cost)
        elif a.cmd=='settle':
            output=Path(a.validated_output).read_text(encoding='utf-8') if a.validated_output else None
            r=mutate(a.db,a.id,'settle',actual_cost=a.cost,output=output,validated=output is not None,note=a.note)
        else:r=mutate(a.db,a.id,a.cmd.replace('-','_'),note=a.note)
        print(json.dumps(r,ensure_ascii=False));return 0
    except (ValueError,OSError,sqlite3.Error) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
