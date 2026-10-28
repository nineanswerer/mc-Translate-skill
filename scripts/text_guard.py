"""Conservative display-text guard. Unknown data formats must use their own adapter."""
import argparse, collections, json, re, sys

# IDs and literal code are protected as wholes; numbers within them are not re-counted.
TOKEN = re.compile(r'https?://[^\s<>"\]\)]+|`[^`\n]+`|%%(?:val\d+|n)(?![A-Za-z0-9_])|%n%|%%|%(?:\d+\$)?[-#+ 0,(<]*\d*(?:\.\d+)?[tT]?[A-Za-z]|\{[A-Za-z_][\w.:-]*\}|\{\d+\}|§[0-9a-fk-orA-FK-OR]|(?<![\w.])[a-z0-9_.-]+:[a-z0-9_./-]+')
# Permit Chinese immediately around a number and recognize attached short units
# (2x, 5s, 10ms). ASCII identifiers such as item2 or 2x_boost are not numbers.
# Colon-qualified IDs, URLs and backtick code are masked by TOKEN first.
NUMBER = re.compile(r'(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?=$|[^A-Za-z0-9_.]|\.(?!\d)|[A-Za-z]{1,12}(?![A-Za-z0-9_]))')
LOCKED_FIELDS={'id','item','item_id','block','entity','type','nbt','tag','tags','recipe','recipes','command','commands','url','file','path','texture','icon','ingredient','ingredients','condition','conditions','requirement','requirements','unlock','rewards','tasks','count','amount','weight','chance','x','y','z'}

def classification(key):
    """Technical field names and known separators are locked; everything else needs adapter context."""
    parts=re.split(r'[./\[\]]+',str(key).lower())
    if parts[-1] in LOCKED_FIELDS or ('/' in str(key) and any(p in LOCKED_FIELDS for p in parts[:-1])): return 'locked_structural'
    if re.search(r'(?:^|\.)flap_display\.cycles\.',str(key).lower()): return 'locked_functional'
    return 'review_context'

def extract(s):
    spans=list(TOKEN.finditer(s)); tokens=[m.group() for m in spans]
    masked=TOKEN.sub(lambda m:' '*len(m.group()),s)
    numbers=NUMBER.findall(masked)
    # Unnumbered printf parameters are supplied in source order. Explicit indices permit reordering.
    ordered=[x for x in tokens if re.fullmatch(r'%(?!\d+\$)[-#+ 0,(<]*\d*(?:\.\d+)?[tT]?[A-Za-z]',x)]
    colors=[x for x in tokens if re.fullmatch(r'§[0-9a-fk-orA-FK-OR]',x)]
    return {'tokens':collections.Counter(tokens),'numbers':collections.Counter(numbers),'ordered_printf':ordered,'colors':colors}

def validate(source,translation,key=''):
    if not isinstance(source,str) or not isinstance(translation,str):raise ValueError('source and translation must be strings')
    kind=classification(key);a=extract(source);b=extract(translation);errors=[]
    if source.strip() and not translation.strip():errors.append('empty_translation_for_nonempty_source')
    if kind.startswith('locked_') and source!=translation:errors.append('locked_structural_or_functional_field_changed')
    if a['tokens']!=b['tokens']:errors.append('protected_tokens_changed')
    if a['numbers']!=b['numbers']:errors.append('display_numbers_changed')
    if a['ordered_printf']!=b['ordered_printf']:errors.append('unnumbered_printf_order_changed')
    if a['colors']!=b['colors']:errors.append('color_sequence_changed')
    if '\ufffd' in translation and '\ufffd' not in source:errors.append('replacement_character_introduced')
    return {'ok':not errors,'classification':kind,'errors':errors}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--file',help='JSON object {key,source,translation}; default stdin')
    a=p.parse_args();obj=json.loads(open(a.file,encoding='utf-8').read() if a.file else sys.stdin.read())
    result=validate(obj['source'],obj['translation'],obj.get('key',''));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 2
if __name__=='__main__':sys.exit(main())
