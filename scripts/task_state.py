"""Single-coordinator, persistent phase journal; never makes API calls or touches the game."""
import argparse, datetime, json, os, sys, tempfile
from pathlib import Path

PHASES=('identify','inventory','reuse_extract','translate_review','validate_build','deploy_export','user_acceptance','feedback_repair')
TRANSITIONS={'pending':{'running','not_requested'},'running':{'completed','blocked','failed'},'blocked':{'pending'},'failed':{'pending'},'completed':{'pending'},'not_requested':{'pending'}}

def write_atomic(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.task-state-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def init(path,task_id):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    state={'schema':1,'task_id':task_id,'phases':{name:'pending' for name in PHASES},'history':[]}
    with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(state,f,ensure_ascii=False,indent=2)
    return state

def transition(path,phase,status,note,next_step):
    p=Path(path);state=json.loads(p.read_text(encoding='utf-8'))
    if state.get('schema')!=1 or phase not in PHASES:raise ValueError('unrecognized state or phase')
    old=state['phases'][phase]
    if status not in TRANSITIONS.get(old,set()):raise ValueError(f'invalid transition {old} -> {status}')
    if not note.strip() or not next_step.strip():raise ValueError('evidence/reason and next step are required')
    if phase=='user_acceptance' and status=='completed':
        prerequisites=PHASES[:PHASES.index('user_acceptance')]
        if state['phases']['validate_build']!='completed' or any(state['phases'][p] not in {'completed','not_requested'} for p in prerequisites) or state['phases']['feedback_repair'] in {'running','blocked','failed'}:raise ValueError('cannot accept while prerequisites or feedback repair remain incomplete')
    if phase in PHASES[:PHASES.index('user_acceptance')] and status=='pending' and state['phases']['user_acceptance']=='completed':raise ValueError('reopen user_acceptance before revising a prior stage')
    if phase=='feedback_repair' and status=='running' and state['phases']['user_acceptance']=='completed':raise ValueError('reopen user_acceptance to pending before feedback repair')
    state['phases'][phase]=status
    state['history'].append({'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'phase':phase,'from':old,'to':status,'evidence_or_reason':note,'next_step':next_step})
    write_atomic(p,state);return state

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    x=sub.add_parser('init');x.add_argument('--state',required=True);x.add_argument('--task-id',required=True)
    x=sub.add_parser('status');x.add_argument('--state',required=True)
    x=sub.add_parser('set');x.add_argument('--state',required=True);x.add_argument('--phase',choices=PHASES,required=True);x.add_argument('--status',required=True);x.add_argument('--note',required=True);x.add_argument('--next-step',required=True)
    a=p.parse_args()
    try:
        s=init(a.state,a.task_id) if a.command=='init' else transition(a.state,a.phase,a.status,a.note,a.next_step) if a.command=='set' else json.loads(Path(a.state).read_text(encoding='utf-8'))
        print(json.dumps(s,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
