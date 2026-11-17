"""Extract source evidence, only from explicitly reusable official/open-license pages.

Commercial publishers remain RSS-based: no full-text republishing or press-image reuse.
"""
from html.parser import HTMLParser
import re
from .fetch import FetchError
from .ingest import plain

class PageReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden=0;self.depth=0;self.p=0;self.current=[];self.paragraphs=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','nav','footer','aside','noscript','form'):
            self.hidden+=1
        if tag in ('main','article'):
            self.depth+=1
        if tag=='p' and self.depth and not self.hidden:
            self.p+=1;self.current=[]
    def handle_data(self,s):
        if self.p and not self.hidden:self.current.append(s)
    def handle_endtag(self,tag):
        if tag=='p' and self.p:
            t=plain(' '.join(self.current))
            if 60<=len(t)<=1200 and not re.search(r'cookie|subscribe|privacy policy|advertising|newsletter',t,re.I):
                self.paragraphs.append(t)
            self.p=0;self.current=[]
        if tag in ('main','article'):
            self.depth=max(0,self.depth-1)
        if tag in ('script','style','nav','footer','aside','noscript','form'):
            self.hidden=max(0,self.hidden-1)

def evidence(c,http):
    data=[c.description] if len(c.description.split())>=12 else []
    # When permitted by robots, analyse publicly readable article paragraphs
    # solely to extract facts; never publish publisher paragraphs verbatim.
    if c.feed.policy=='open' or http.can_read_article(c.url):
        try:
            html=http.get(c.url,limit=750000).decode('utf-8','replace')
            p=PageReader();p.feed(html)
            words=0
            for block in p.paragraphs:
                n=len(block.split())
                if words+n>1100:break
                data.append(block);words+=n
        except (FetchError,UnicodeError,ValueError): pass
    return [x for x in data if x and len(x.split())>=10]


def make_facts(candidate,other_candidates,http):
    facts=[];sources=[]
    for c in [candidate,*other_candidates][:5]:
        paragraphs=evidence(c,http)
        if not paragraphs: continue
        sources.append({'id':c.id,'publisher':c.feed.publisher,'url':c.url,
                        'language':c.feed.language,'published_at':c.published_at.isoformat()})
        for index,p in enumerate(paragraphs[:9]):
            # Feed excerpts and page paragraphs are *private AI evidence*, not published quotations.
            if len(p)>1150:p=p[:1150].rsplit(' ',1)[0]
            facts.append({'id':f'{c.id}:{index}','source_id':c.id,'text':p})
            if len(facts)>=22:break
        if len(facts)>=22:break
    return facts,sources
