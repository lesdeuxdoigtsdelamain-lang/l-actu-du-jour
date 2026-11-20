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

NUMERIC=re.compile(
    r'(?<![\w.,٫٬])[+\-−]?\d+'
    r'(?:(?:[.,٫٬]\d+)|(?:[ \u00a0\u202f\u2009]\d{3}(?!\d)))*'
    r'(?:[ \u00a0\u202f\u2009]?[%٪])?')

def normalized_number(s,language=None):
    """Canonical value, preserving decimals/sign/percent; None if ambiguous.

    Separators are interpreted using the source language. Thus English 80,000
    and French 80 000 agree, while French 2,07 never authorizes 207. Without a
    known convention, a lone separator followed by three digits is ambiguous.
    No units are converted and no inferred/calculated values are admitted.
    """
    text=unicodedata.normalize('NFKC',str(s)).strip().replace('−','-').replace('٪','%')
    text=''.join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in text)
    percent=text.endswith('%')
    if percent:text=text[:-1].rstrip()
    sign=''
    if text.startswith(('+','-')):
        sign='-' if text[0]=='-' else '';text=text[1:]
    if not text:return None
    lang=(language or '').split('-')[0].lower()
    decimal=None;group=None
    if '٫' in text or '٬' in text:
        # These Arabic separator characters have explicit, different meanings.
        if ',' in text or '.' in text:return None
        decimal='٫';group='٬'
    elif lang=='en':
        decimal='.';group=','
    elif lang=='fr':
        if ',' in text and '.' in text:return None
        decimal=',' if ',' in text else '.'
    elif lang=='es':
        decimal=',';group='.'
    else:
        # Arabic prose may use several Latin-number conventions. Only explicit
        # Arabic separators, or otherwise unambiguous Latin forms, are inferred.
        present=[sep for sep in (',','.') if sep in text]
        if len(present)>1:return None
        if present:
            sep=present[0];parts=text.split(sep)
            if len(parts)==2:
                if len(parts[1])==3:return None
                decimal=sep
            elif len(parts)>2 and all(len(p)==3 for p in parts[1:]):
                group=sep
            else:return None
    if decimal and text.count(decimal)>1:return None
    parts=text.split(decimal) if decimal and decimal in text else [text]
    integer=parts[0];fraction=parts[1] if len(parts)==2 else ''
    if len(parts)==2 and (not fraction or not fraction.isascii() or not fraction.isdecimal()):return None
    # A grouping separator can occur only in the integer, in groups of three.
    separators=' ' + (group or '')
    chunks=re.split('['+re.escape(separators)+']',integer)
    if len(chunks)>1:
        if not (1<=len(chunks[0])<=3 and all(len(p)==3 for p in chunks[1:])):return None
    if not all(p and p.isascii() and p.isdecimal() for p in chunks):return None
    integer=''.join(chunks).lstrip('0') or '0'
    fraction=fraction.rstrip('0')
    value=integer+('.'+fraction if fraction else '')
    if value=='0':sign=''
    return sign+value+('%' if percent else '')

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

def allowed_numbers(facts,sources):
    languages={s['id']:s.get('language') for s in sources if s.get('id')}
    unique={s.get('language') for s in sources if s.get('language')}
    fallback=next(iter(unique)) if len(unique)==1 else None
    allowed=set()
    for fact in facts:
        language=languages.get(fact.get('source_id'),fallback)
        for token in NUMERIC.findall(fact.get('text','')):
            value=normalized_number(token,language)
            if value is not None:allowed.add(value)
    # Publication dates can support their day/month/year, never arbitrary clock
    # components such as a minute or a second mistaken for a reported quantity.
    for source in sources:
        try:stamp=datetime.fromisoformat(str(source.get('published_at','')).replace('Z','+00:00'))
        except (TypeError,ValueError):continue
        allowed.update(str(value) for value in (stamp.year,stamp.month,stamp.day))
    return allowed

def unsupported_numbers(result,facts,sources):
    title=result.get('titre','')
    paragraphs=result.get('paragraphes',[])
    text=' '.join([title if isinstance(title,str) else '', *[p for p in paragraphs if isinstance(p,str)]]) if isinstance(paragraphs,list) else ''
    used=set()
    for token in NUMERIC.findall(text):
        value=normalized_number(token,'fr')
        # Malformed numeric output must not disappear from the rejection gate.
        used.add(value if value is not None else 'format ambigu: '+token)
    return used-allowed_numbers(facts,sources)

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
    if unsupported_numbers(result,facts,sources):return 'chiffre sans preuve dans les sources'
    # Reject word-for-word copying of a very large span of copyrighted excerpts.
    for f in facts:
        words=f['text'].casefold().split()
        # Flag substantial verbatim reuse, not generic 15-word factual phrasing.
        # A continuous 26-word match is longer than a short incidental excerpt.
        if len(words)<26:continue
        for start in range(0,len(words)-25):
            span=' '.join(words[start:start+26])
            if span in text.casefold():return 'passage source recopie sans reformulation'
    return None

def verify(result,facts,sources):
    return rejection_reason(result,facts,sources) is None

def assemble(result,group,facts,sources,*,min_words=280,max_words=780,category=None):
    result=reflow_paragraphs(result)
    if not verify(result,facts,sources):return None
    # Keep word counts compatible with older Android clients (ASCII whitespace).
    paragraphs=[re.sub(r'\s+',' ',p).strip() for p in result['paragraphes']]
    wc=count_words(' '.join(paragraphs))
    if not min_words<=wc<=max_words:return None
    refs=[Source(publisher=s['publisher'],url=s['url'],language=s['language'],
                 published_at=datetime.fromisoformat(s['published_at'])) for s in sources]
    return Article(id=group.id,category=category or categorize(group),title=result['titre'],paragraphs=paragraphs,
                   source_refs=refs,word_count=wc,minutes=max(2,min(7,round(wc/170))))
