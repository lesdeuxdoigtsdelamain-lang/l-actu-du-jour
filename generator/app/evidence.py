"""Extract bounded private evidence from RSS and permitted public article pages."""
from html.parser import HTMLParser
import re
from .fetch import FetchError
from .ingest import plain


HIDDEN_TAGS = {'script', 'style', 'nav', 'footer', 'aside', 'noscript', 'form',
               'button', 'svg', 'figure', 'figcaption'}
VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
             'meta', 'param', 'source', 'track', 'wbr'}
HIDDEN_ROLES = {'navigation', 'complementary', 'contentinfo', 'dialog', 'banner'}
# These describe page components, not words appearing in a news paragraph.
# Avoid bare "comment" or "ad" substrings: they also occur in real story classes.
UI_COMPONENT = re.compile(
    r'(?:^|[\s_-])(?:comments|commentaires|تعليقات|التعليقات)(?:[\s_-]+(?:area|list|form|section|policy|rules|wrapper))?(?:$|[\s_-])'
    r'|(?:^|[\s_-])comment[\s_-]+(?:area|list|form|section|policy|rules|wrapper)(?:$|[\s_-])'
    r'|(?:^|[\s_-])(?:disqus|respond|subscription|subscribe|newsletter|social-share|share|sharing|'
    r'breadcrumb|cookie|consent|paywall|related|recommended|promo|advertisement|advertising|ads|'
    r'card-article|card-shuffle|shuffle-videos|shuffle-header__next|preferred-source|generic-vertical-rebound|'
    r'link[\s_-]+(?:headline|item))(?:$|[\s_-])',
    re.I,
)
POLICY_TEXT = re.compile(
    r'^(?:les\s+)?commentaires?\s+(?:doivent|ne\s+(?:doivent|refl[eè]tent)|sont\s+(?:mod[eé]r[eé]s|soumis))\b'
    r'|^(?:merci\s+de|veuillez)\s+respecter\s+(?:notre|la|les)\s+(?:charte|r[eè]gles)\b'
    r'|^(?:your\s+)?comments?\s+(?:must|are\s+(?:moderated|subject)|do\s+not\s+reflect)\b'
    r'|^(?:please\s+)?(?:sign\s+in|log\s+in|register)\s+to\s+(?:post|leave|add|read)\s+(?:a\s+)?comment'
    r'|^(?:please\s+)?(?:subscribe|sign\s+up)\s+to\s+(?:our|the)\s+newsletter\b'
    r'|^go\s+to\s+bbc[\w.]+(?:\s*,?\s*external)?\s+for\s+more\s+news\b'
    r'|^follow\s+us\s+on\s+(?:twitter|facebook|instagram|social\s+media)\b'
    r'|^suivez[-\s]nous\s+sur\s+(?:twitter|facebook|instagram|les\s+r[eé]seaux)\b'
    r'|^(?:اشترك|اشتركوا)\s+في\s+(?:النشرة|نشرتنا)'
    r'|^(?:التعليقات\s+(?:لا\s+تعبر|تخضع)|يرجى\s+احترام\s+(?:قواعد|شروط)\s+التعليق)',
    re.I,
)


def peripheral_node(tag, attrs):
    attributes = dict(attrs)
    labels = ' '.join(attributes.get(key, '') or '' for key in ('id', 'class'))
    labels = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', labels)
    return (tag in HIDDEN_TAGS or 'hidden' in attributes
            or (attributes.get('aria-hidden') or '').casefold() == 'true'
            or (attributes.get('role') or '').casefold() in HIDDEN_ROLES
            or UI_COMPONENT.search(labels) is not None)


class PageReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]
        self.current=None
        self.paragraphs=[]

    def finish_paragraph(self):
        if self.current is None:return
        text=plain(' '.join(self.current))
        # Short factual sentences are useful once adjacent paragraphs are combined.
        if len(text.split())>=4 and not POLICY_TEXT.search(text):
            self.paragraphs.append(text)
        self.current=None

    def handle_starttag(self,tag,attrs):
        # HTML permits omitted </p>; close the earlier paragraph first.
        if tag=='p' and any(frame[0]=='p' for frame in self.stack):
            self.handle_endtag('p')
        parent_hidden=self.stack[-1][1] if self.stack else False
        parent_article=self.stack[-1][2] if self.stack else False
        hidden=parent_hidden or peripheral_node(tag,attrs)
        scope=parent_article or tag in ('main','article') or dict(attrs).get('itemprop')=='articleBody'
        if tag not in VOID_TAGS:self.stack.append((tag,hidden,scope))
        if tag=='p' and scope and not hidden:self.current=[]

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID_TAGS:self.handle_endtag(tag)

    def handle_data(self,s):
        if self.current is not None and self.stack and not self.stack[-1][1]:
            self.current.append(s)

    def handle_endtag(self,tag):
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),None)
        if index is None:return
        if any(frame[0]=='p' for frame in self.stack[index:]):self.finish_paragraph()
        del self.stack[index:]

    def close(self):
        super().close()
        self.finish_paragraph()


def evidence_blocks(paragraphs, maximum=1100):
    """Combine short paragraphs without adding text, bounded over the whole source.

    RSS introductions often repeat the first page paragraph. Count each exact
    paragraph only once, then preserve the source order and every remaining word
    within the budget. This avoids interpreting nine short HTML paragraphs as
    the full amount of evidence available for an otherwise substantial story.
    """
    blocks=[];current=[];used=0;seen=set()
    for paragraph in paragraphs:
        text=plain(paragraph)
        key=text.casefold()
        if key in seen or POLICY_TEXT.search(text):continue
        seen.add(key)
        words=text.split()
        if len(words)<4:continue
        words=words[:max(0,maximum-used)]
        used+=len(words)
        while words:
            if current and len(current)+len(words)>110 and len(current)>=60:
                blocks.append(' '.join(current));current=[]
            take=min(110-len(current),len(words))
            current.extend(words[:take]);words=words[take:]
            if len(current)>=110 or (len(current)>=60 and not words):
                blocks.append(' '.join(current));current=[]
        if used>=maximum:break
    if current:blocks.append(' '.join(current))
    return blocks

def evidence(c,http):
    data=[c.description] if len(c.description.split())>=12 else []
    # When permitted by robots, analyse publicly readable article paragraphs
    # solely to extract facts; never publish publisher paragraphs verbatim.
    if c.feed.policy=='open' or http.can_read_article(c.url):
        try:
            html=http.get(c.url,limit=750000).decode('utf-8','replace')
            p=PageReader();p.feed(html);p.close()
            data.extend(p.paragraphs)
        except (FetchError,UnicodeError,ValueError): pass
    return evidence_blocks(data)


def make_facts(candidate,other_candidates,http):
    facts=[];sources=[];source_blocks=[];seen_urls=set()
    for c in [candidate,*other_candidates][:5]:
        if c.url in seen_urls:continue
        seen_urls.add(c.url)
        paragraphs=evidence(c,http)
        if not paragraphs: continue
        sources.append({'id':c.id,'publisher':c.feed.publisher,'url':c.url,
                        'language':c.feed.language,'published_at':c.published_at.isoformat()})
        source_blocks.append((c,paragraphs))
    # Round-robin keeps evidence from each source inside the global fact budget.
    for index in range(max((len(blocks) for _,blocks in source_blocks),default=0)):
        for c,blocks in source_blocks:
            if index<len(blocks):
                facts.append({'id':f'{c.id}:{index}','source_id':c.id,'text':blocks[index]})
                if len(facts)>=22:return facts,sources
    return facts,sources
