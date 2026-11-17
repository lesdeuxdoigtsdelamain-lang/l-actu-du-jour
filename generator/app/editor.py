"""Strong quality gate: reject half-line summaries, hallucinated figures, or insufficient evidence.

Limitations: semantic fact checking and cross-language transliteration are not perfect;
automatic output cannot replace a human fact checker.
"""
from datetime import datetime
from hashlib import sha256
import re
import unicodedata
from .models import Article,Source
from .ingest import categorize

NUMERIC=re.compile(r'(?<![\w])\d+(?:[.,]\d+)?(?:\s?%)?')

def normalized_number(s):
    return re.sub(r'[\s,\u202f]','',s).replace('٫','.')

def count_words(s):return len(s.split())

def verify(result,facts,sources):
    """Structural + source-ID validation; numbers must occur in source evidence.

    Prevent unsupported percentages, totals, dates. Relative dates written out in
    words are not automatically verified, so editorial prompt separately forbids them.
    """
    title=result.get('titre');paragraphs=result.get('paragraphes');proofs=result.get('preuves')
    if not isinstance(title,str) or not 18<=len(title)<=180 or not isinstance(paragraphs,list):return False
    if not isinstance(proofs,list) or len(proofs)!=len(paragraphs) or not 4<=len(paragraphs)<=11:return False
    if any(not isinstance(p,str) or not 22<=count_words(p)<=160 for p in paragraphs):return False
    text=' '.join(paragraphs)
    if not 280<=count_words(text)<=780:return False
    if any('<' in x or '>' in x or '\x00' in x for x in [title,*paragraphs]):return False
    ids={f['id'] for f in facts}
    if not all(isinstance(p,list) and p and all(isinstance(q,str) and q in ids for q in p) for p in proofs):return False
    evidencetext=' '.join(f['text'] for f in facts)+' '+ ' '.join(s.get('published_at','') for s in sources)
    in_numbers={normalized_number(x) for x in NUMERIC.findall(evidencetext)}
    out_numbers={normalized_number(x) for x in NUMERIC.findall(title+' '+text)}
    if not out_numbers <= in_numbers:return False
    # Reject word-for-word copying of a very large span of copyrighted excerpts.
    for f in facts:
        words=f['text'].casefold().split()
        if len(words)<18:continue
        for start in range(0,len(words)-14,7):
            span=' '.join(words[start:start+15])
            if span in text.casefold():return False
    return True

def assemble(result,group,facts,sources):
    if not verify(result,facts,sources):return None
    paragraphs=[p.strip() for p in result['paragraphes']]
    wc=count_words(' '.join(paragraphs))
    refs=[Source(publisher=s['publisher'],url=s['url'],language=s['language'],
                 published_at=datetime.fromisoformat(s['published_at'])) for s in sources]
    return Article(id=group.id,category=categorize(group),title=result['titre'],paragraphs=paragraphs,
                   source_refs=refs,word_count=wc,minutes=max(2,min(7,round(wc/170))))
