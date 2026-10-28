"""User-configured HTTPS Chat Completions adapter; no provider/model/price is hard-coded."""
import argparse,json,os,sys,urllib.parse,urllib.request
from decimal import Decimal,InvalidOperation,ROUND_CEILING

SYSTEM='Translate the supplied Minecraft display text into Simplified Chinese using the supplied context and glossary. Preserve technical identifiers, placeholders, formatting codes and exact numbers. Return a JSON object with a translations array of strings, one per entry in the same order. Do not add commentary.'

def money(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('invalid price')
    if not result.is_finite() or result<0:raise ValueError('invalid price')
    return result
def rounded(value):return value.quantize(Decimal('0.000001'),rounding=ROUND_CEILING)
def prepare(packet,config,env):
    """Check every condition before network submission; None means no request was sent."""
    try:
        endpoint=config['endpoint'];u=urllib.parse.urlsplit(endpoint)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:return None
        if config.get('provider_pricing_and_token_bound_verified') is not True:return None
        key=env.get(config['api_key_env']);model=config['model'];req=packet['request']
        if not key or not model or req.get('model')!=model or packet['currency']!=config['currency']:return None
        if any(str(k).lower().replace('-','_') in {'api_key','password','authorization','access_token','client_secret'} for k in config):return None
        rows=req.get('entries')
        if rows is None:rows=[{'source':req['source'],'key':req.get('key','')}]
        if not isinstance(rows,list) or not rows or any(not isinstance(x,dict) or not isinstance(x.get('source'),str) for x in rows):return None
        user_payload=json.dumps({'entries':rows,'glossary':req.get('glossary',{}),'instructions':req.get('instructions','')},ensure_ascii=False,separators=(',',':'))
        messages=[{'role':'system','content':SYSTEM},{'role':'user','content':user_payload}]
        payload_bytes=len(json.dumps(messages,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
        max_prompt_bytes=int(config['verified_max_prompt_utf8_bytes']);max_input=int(config['verified_max_input_tokens']);max_output=int(config['max_output_tokens'])
        if min(max_prompt_bytes,max_input,max_output)<=0 or payload_bytes>max_prompt_bytes:return None
        input_price=money(config['input_price_per_million']);output_price=money(config['output_price_per_million']);flat=money(config.get('flat_fee_per_request','0'))
        if input_price==0 and output_price==0 and flat==0 and config.get('verified_free_service') is not True:return None
        worst=rounded((Decimal(max_input)*input_price+Decimal(max_output)*output_price)/Decimal(1_000_000)+flat)
        if worst>money(packet['max_cost']):return None
        token_parameter=config.get('output_token_parameter','max_tokens')
        if token_parameter not in {'max_tokens','max_completion_tokens'}:return None
        body={'model':model,'messages':messages,token_parameter:max_output}
        if config.get('json_response_format') is True:body['response_format']={'type':'json_object'}
        request=urllib.request.Request(endpoint,data=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf-8'),headers={'Content-Type':'application/json; charset=utf-8','Authorization':'Bearer '+key},method='POST')
        timeout=float(config.get('network_timeout_seconds',90))
        if not 0<timeout<=3600:return None
        return {'request':request,'input_price':input_price,'output_price':output_price,'flat':flat,'max_input':max_input,'max_output':max_output,'timeout':timeout}
    except (KeyError,ValueError,TypeError,InvalidOperation):return None

def run(packet,config,env,opener=urllib.request.urlopen):
    ready=prepare(packet,config,env)
    if ready is None:return {'status':'not_sent'}
    # After urlopen starts, ANY transport, parsing, or billing uncertainty must raise.
    # Parent driver catches the nonzero exit and retains the full reservation.
    with opener(ready['request'],timeout=ready['timeout']) as response:
        raw=response.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('provider response exceeds limit')
    data=json.loads(raw.decode('utf-8'));usage=data['usage']
    inp=usage['prompt_tokens'];out=usage['completion_tokens']
    if isinstance(inp,bool) or isinstance(out,bool) or not isinstance(inp,int) or not isinstance(out,int) or min(inp,out)<0:raise ValueError('invalid provider usage')
    cost=rounded((Decimal(inp)*ready['input_price']+Decimal(out)*ready['output_price'])/Decimal(1_000_000)+ready['flat'])
    bound_violation=inp>ready['max_input'] or out>ready['max_output']
    safe_usage={'input_tokens':inp,'output_tokens':out,'total_tokens':inp+out}
    try:content=data['choices'][0]['message']['content']
    except (KeyError,IndexError,TypeError):return {'status':'failed_billed','cost':str(cost),'bound_violation':bound_violation,'usage':safe_usage}
    if not isinstance(content,str):return {'status':'failed_billed','cost':str(cost),'text':str(content)[:100_000],'bound_violation':bound_violation,'usage':safe_usage}
    try:parsed=json.loads(content)
    except json.JSONDecodeError:parsed={}
    if isinstance(parsed,list):translations=parsed
    elif isinstance(parsed,dict):translations=parsed.get('translations')
    else:translations=None
    return {'status':'success','translations':translations,'content':content,'cost':str(cost),'bound_violation':bound_violation,'usage':safe_usage}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);a=p.parse_args()
    # A config or credential error before network is safely not_sent; never print secrets or traceback.
    try:config=json.loads(open(a.config,encoding='utf-8').read());packet=json.load(sys.stdin)
    except (OSError,ValueError):print('{"status":"not_sent"}');return 0
    try:result=run(packet,config,os.environ)
    except Exception:return 2
    print(json.dumps(result,ensure_ascii=False));return 0
if __name__=='__main__':sys.exit(main())
