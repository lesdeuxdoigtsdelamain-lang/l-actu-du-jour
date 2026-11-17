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


def reflow_paragraphs(result):
    """Re-paragraph AI text without adding facts, words or source references.

    Source fact IDs follow each sentence. This repairs a model that splits a
    substantive article into many tiny paragraphs, but never pads short texts.
    """
    paragraphs=result.get('paragraphes')
    proofs=result.get('preuves')
    if not isinstance(paragraphs,list) or not isinstance(proofs,list) or len(paragraphs)!=len(proofs):
        return result
    if not paragraphs or any(not isinstance(p,str) for p in paragraphs) or any(not isinstance(p,list) for p in proofs):
        return result
    if all(22<=count_words(p)<=160 for p in paragraphs) and 4<=len(paragraphs)<=11:
        return result
    total=count_words(' '.join(paragraphs))
    if not 280<=total<=780:
        return result
    units=[]
    for paragraph,refs in zip(paragraphs,proofs):
        for sentence in re.split(r'(?<=[.!?])\s+',paragraph.strip()):
            if sentence.strip():
                if count_words(sentence)>150:return result
                units.append((sentence.strip(),refs))
    if len(units)<4:return result
    target=max(48,min(110,round(total/6)))
    groups=[];current=[];current_refs=[];current_size=0
    for sentence,refs in units:
        length=count_words(sentence)
        if current and current_size+length>155:
            groups.append((' '.join(current),current_refs))
            current=[];current_refs=[];current_size=0
        current.append(sentence);current_size+=length
        for ref in refs:
            if ref not in current_refs:current_refs.append(ref)
        if current_size>=target:
            groups.append((' '.join(current),current_refs))
            current=[];current_refs=[];current_size=0
    if current:
        last=' '.join(current)
        if groups and count_words(last)<22 and count_words(groups[-1][0])+count_words(last)<=160:
            prev_text,prev_refs=groups[-1]
            groups[-1]=(prev_text+' '+last,list(dict.fromkeys([*prev_refs,*current_refs])))
        else:
            groups.append((last,current_refs))
    if not 4<=len(groups)<=11 or any(not 22<=count_words(p)<=160 for p,_ in groups):
        return result
    corrected=dict(result)
    corrected['paragraphes']=[p for p,_ in groups]
    corrected['preuves']=[refs for _,refs in groups]
    # The underlying text must not gain or lose any words.
    assert ' '.join(corrected['paragraphes']).split()== ' '.join(paragraphs).split()
    return corrected

def rejection_reason(result,facts,sources):
    """Structural + source-ID validation; numbers must occur in source evidence.

    Prevent unsupported percentages, totals, dates. Relative dates written out in
    words are not automatically verified, so editorial prompt separately forbids them.
    """
    title=result.get('titre');paragraphs=result.get('paragraphes');proofs=result.get('preuves')
    if not isinstance(title,str) or not 18<=len(title)<=180 or not isinstance(paragraphs,list):return 'titre ou paragraphes invalides'
    if not isinstance(proofs,list) or len(proofs)!=len(paragraphs) or not 4<=len(paragraphs)<=11:return 'nombre de paragraphes ou preuves incoherent'
    if any(not isinstance(p,str) or not 22<=count_words(p)<=160 for p in paragraphs):return 'paragraphe trop court ou trop long'
    text=' '.join(paragraphs)
    if not 280<=count_words(text)<=780:return 'article trop court ou trop long'
    if any('<' in x or '>' in x or '\x00' in x for x in [title,*paragraphs]):return 'balises interdites'
    ids={f['id'] for f in facts}
    if not all(isinstance(p,list) and p and all(isinstance(q,str) and q in ids for q in p) for p in proofs):return 'preuve absente ou reference inconnue'
    evidencetext=' '.join(f['text'] for f in facts)+' '+ ' '.join(s.get('published_at','') for s in sources)
    in_numbers={normalized_number(x) for x in NUMERIC.findall(evidencetext)}
    out_numbers={normalized_number(x) for x in NUMERIC.findall(title+' '+text)}
    if not out_numbers <= in_numbers:return 'chiffre sans preuve dans les sources'
    # Reject word-for-word copying of a very large span of copyrighted excerpts.
    for f in facts:
        words=f['text'].casefold().split()
        # Flag substantial verbatim reuse, not generic 15-word factual phrasing.
        # A continuous 26-word match is longer than a short incidental excerpt.
        if len(words)<26:continue
        for start in range(0,len(words)-25,8):
            span=' '.join(words[start:start+26])
            if span in text.casefold():return 'passage source recopie sans reformulation'
    return None

def verify(result,facts,sources):
    return rejection_reason(result,facts,sources) is None

def assemble(result,group,facts,sources):
    result=reflow_paragraphs(result)
    if not verify(result,facts,sources):return None
    paragraphs=[p.strip() for p in result['paragraphes']]
    wc=count_words(' '.join(paragraphs))
    refs=[Source(publisher=s['publisher'],url=s['url'],language=s['language'],
                 published_at=datetime.fromisoformat(s['published_at'])) for s in sources]
    return Article(id=group.id,category=categorize(group),title=result['titre'],paragraphs=paragraphs,
                   source_refs=refs,word_count=wc,minutes=max(2,min(7,round(wc/170))))
