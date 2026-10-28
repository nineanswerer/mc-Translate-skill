"""Flag possible glossary inconsistencies in a reviewed JSONL catalog; never rewrite text."""
import argparse, json, re, sys
from pathlib import Path


def check(glossary, rows):
    terms = glossary.get('terms')
    if not isinstance(terms, list): raise ValueError('glossary.terms must be a list')
    ids = set()
    for term in terms:
        tid=term.get('id'); preferred=term.get('preferred'); sources=term.get('source_forms')
        if not isinstance(tid,str) or not tid or tid in ids or not isinstance(preferred,str) or not preferred or not isinstance(sources,list) or not sources or not all(isinstance(s,str) and s for s in sources): raise ValueError('every glossary term needs unique id, preferred and source_forms')
        ids.add(tid)
    issues=[]
    for index,row in enumerate(rows):
        if not isinstance(row,dict) or not isinstance(row.get('source'),str) or not isinstance(row.get('translation'),str): raise ValueError('each row needs source and translation strings')
        exceptions=row.get('term_exceptions',[])
        if not isinstance(exceptions,list): raise ValueError('term_exceptions must be a list of reviewed term ids')
        for term in terms:
            if term['id'] in exceptions:continue
            matched=any(re.search(r'(?<![A-Za-z0-9_])'+re.escape(form)+r'(?![A-Za-z0-9_])',row['source'],re.I) for form in term['source_forms'])
            allowed=[term['preferred']]+term.get('allowed_variants',[])
            if matched and not any(v in row['translation'] for v in allowed):
                issues.append({'row':index+1,'item_id':row.get('id'),'term_id':term['id'],'expected_any':allowed,'reason':'source term present but no approved translation found; review context'})
    return {'entries_checked':len(rows),'terms_checked':len(terms),'candidate_issues':issues,'semantic_review_required':True}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--glossary',required=True);p.add_argument('--catalog',required=True);a=p.parse_args()
    try:
        glossary=json.loads(Path(a.glossary).read_text(encoding='utf-8'))
        rows=[json.loads(line) for line in Path(a.catalog).read_text(encoding='utf-8').splitlines() if line.strip()]
        print(json.dumps(check(glossary,rows),ensure_ascii=False));return 0
    except (ValueError,TypeError,OSError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
