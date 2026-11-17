"""RSS/Atom parser, ranking and conservative multilingual event matching."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from defusedxml import ElementTree as ET
from email.utils import parsedate_to_datetime
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit,urlunsplit
import re
import unicodedata
from .fetch import Fetcher,FetchError
from .sources import Feed

class Strip(HTMLParser):
    def __init__(self): super().__init__();self.parts=[]
    def handle_data(self,s): self.parts.append(s)
def plain(s):
    parser=Strip(); parser.feed(s or '')
    return re.sub(r'\s+',' ',unescape(' '.join(parser.parts))).strip()[:5000]
def when(s):
    if not s:return None
    try: d=parsedate_to_datetime(s)
    except (TypeError,ValueError):
        try:d=datetime.fromisoformat(s.replace('Z','+00:00'))
        except (TypeError,ValueError):return None
    return d.astimezone(timezone.utc) if d.tzinfo else None

def norm(s):
    s=''.join(ch for ch in unicodedata.normalize('NFD',s.casefold()) if unicodedata.category(ch)!='Mn')
    return re.sub(r'[^\w\s]',' ',s)

def content_tokens(s):
    return set(x for x in re.findall(r'[\w\u0600-\u06ff]{3,}',norm(s)) if x not in {
        'avec','pour','dans','plus','apres','avant','sur','les','des','une','the','and','from','after','with','that',
        'this','will','new','news','about','اليوم','على','من','هذا','هذه','ذلك','التي','بعد','الى','إلى'})

def same_event(a,b):
    a,b=content_tokens(a),content_tokens(b)
    inter=len(a & b)
    return inter>=3 and (inter/max(1,min(len(a),len(b)))>=0.72 or inter/max(1,len(a|b))>=0.58)

EXCLUDE = re.compile(r'\b(?:celebrity|celebrities|fashion|film\s+festival|movie|cinema|music|musique|gaming|video game|jeux vidéo|exhibition|exposition|people|automobile|football|soccer|cricket|rugby|tennis|sport|olympic|premier league|champions league|موسيقى|سينما|أفلام|رياضة)\b',re.I)
BOOST = re.compile(r'\b(?:maroc|morocco|المغرب|palestin|فلسطين|gaza|غزة|sudan|soudan|ukraine|iran|iranien|إيران|election|élection|president|président|prime minister|government|gouvernement|onu|nations unies|investissement|inflation|health|santé|climat|climate|ai|intelligence artificielle|islam|مسلم)\b',re.I)

@dataclass
class Candidate:
    id:str
    title:str
    description:str
    url:str
    published_at:datetime
    feed:Feed
    evidence:str=''
    clustered:list['Candidate']=field(default_factory=list)


def _get(item,names):
    for name in names:
        e=item.find(name)
        if e is not None:
            if name.endswith('link') and e.get('href'): return e.get('href')
            return ''.join(e.itertext())
    return ''

def parse(raw,feed,now,max_age=36):
    try:root=ET.fromstring(raw)
    except Exception: raise FetchError('Invalid feed XML') from None
    items=list(root.findall('.//item'))+list(root.findall('.//{http://www.w3.org/2005/Atom}entry'))
    result=[]
    for i in items[:80]:
        title=plain(_get(i,['title','{http://www.w3.org/2005/Atom}title']))[:200]
        desc=plain(_get(i,['description','{http://www.w3.org/2005/Atom}summary','{http://www.w3.org/2005/Atom}content','{http://purl.org/rss/1.0/modules/content/}encoded']))[:1800]
        u=_get(i,['link','{http://www.w3.org/2005/Atom}link']).strip()
        dt=when(_get(i,['pubDate','{http://www.w3.org/2005/Atom}published','{http://www.w3.org/2005/Atom}updated','{http://purl.org/dc/elements/1.1/}date']))
        if not dt or not title or not u or len(title)<12 or EXCLUDE.search(title): continue
        try:
            parts=urlsplit(u)
            if parts.scheme != 'https' or parts.hostname not in feed.hosts or parts.username or parts.password or parts.port not in (None,443): continue
        except ValueError:continue
        if not now-timedelta(hours=max_age)<=dt<=now+timedelta(minutes=15):continue
        url=urlunsplit((parts.scheme,parts.netloc,parts.path.rstrip('/'),'', ''))
        result.append(Candidate(sha256(url.encode()).hexdigest()[:22],title,desc,url,dt,feed))
    return result

def collect(feeds,now):
    hosts=set(x for f in feeds for x in f.hosts)
    fetcher=Fetcher(hosts)
    status={};candidates=[]
    def one(f):
        try:return f.id,parse(fetcher.get(f.url),f,now)
        except FetchError as e:return f.id,str(e)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(one,f):f for f in feeds}
        for fut in as_completed(futures):
            f=futures[fut]
            try:
                id_,res=fut.result()
                status[id_]={'ok':len(res)} if isinstance(res,list) else {'error':res}
                if isinstance(res,list):candidates.extend(res)
            except Exception:status[f.id]={'error':'Unexpected source error'}
    return candidates,status

def categorize(c):
    t=norm(c.title+' '+c.description[:220])
    if re.search(r'\b(maroc|morocco|المغرب|الرباط|rabat|casablanca|الدار البيضاء)\b',t):return 'Maroc'
    if re.search(r'\b(health|medicine|medical|sante|hopital|hospital|صحة|vaccin)\b',t):return 'Santé'
    if re.search(r'\b(artificial intelligence|intelligence artificielle|science|nasa|research|technolog|robot|satellit|astronomi)\b',t):return 'Science et technologie'
    if re.search(r'\b(climate|climat|environment|environnement|biodiversit|emissions|flood|glacier)\b',t):return 'Environnement'
    # Geography is only a topical signal; the publisher's language is not an identity.
    if re.search(r'\b(palestin|palestinian|gaza|غزة|فلسطين|iran|إيران|iraq|العراق|syrie|syria|سوريا|saudi|السعودية|yemen|اليمن|pakistan|باكستان|tunisie|tunisia|تونس|algerie|algérie|الجزائر|turquie|turkey|تركيا|indonesie|indonesia|إندونيسيا|afghanistan|أفغانستان|muslim|musulman|islam|مسلم|الإسلام)\b',t):return 'Monde musulman'
    return c.feed.category

def cluster(candidates):
    """Sources describing the same event may be merged; cross-script matches need editorial verification."""
    groups=[]
    for c in sorted(candidates,key=lambda x:x.published_at,reverse=True):
        if any(c.url==q.url for g in groups for q in [g,*g.clustered]):continue
        target=next((g for g in groups if same_event(g.title,c.title) and
                     all(q.feed.publisher!=c.feed.publisher for q in [g,*g.clustered])),None)
        if target:target.clustered.append(c)
        else:groups.append(c)
    return groups

def rank(groups,seen_ids=(),seen_titles=(),limit=25):
    selected=[];counts={};publishers={}
    pool=[g for g in groups if g.id not in seen_ids and not any(same_event(g.title,t) for t in seen_titles)]
    while pool and len(selected)<limit:
        def score(g):
            cat=categorize(g)
            bias=3 if cat in ('Maroc','Monde musulman','Monde') else 1
            depth=len(g.description.split())+sum(len(x.description.split()) for x in g.clustered)
            return (bias + (4 if BOOST.search(g.title) else 0)+ min(4,len(g.clustered)*2)
                    +min(3,depth//70) - 4*counts.get(cat,0) - 2*publishers.get(g.feed.publisher,0))
        best=max(pool,key=score);pool.remove(best)
        if EXCLUDE.search(best.title):continue
        cat=categorize(best)
        selected.append(best)
        counts[cat]=counts.get(cat,0)+1
        publishers[best.feed.publisher]=publishers.get(best.feed.publisher,0)+1
    return selected
