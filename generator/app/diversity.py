"""Editorial geography and event repetition checks, independent of the UI rubric.

The final comparison runs on the generated French text as well as source URLs,
so two reports translated from different languages cannot bypass the check just
because their feed headlines differ. This is a conservative lexical gate, not a
claim of perfect semantic matching. Source-based and text-based checks are both
needed; country, publisher or a single shared subject word is never sufficient.
"""
from collections.abc import Mapping
import re
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def normalize(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold())
                   if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^\w\s]', ' ', text)


def canonical_url(url):
    """Ignore tracking, fragments, host casing and equivalent encoded paths."""
    parts = urlsplit(str(url))
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = unquote(parts.path).rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), host, path, '', ''))


_GEOGRAPHY = {
    'Maroc': re.compile(
        r'\b(?:maroc(?:ain(?:e)?s?)?|morocc(?:o|an(?:s)?)|rabat|casablanca|'
        r'marrakech|fes|tanger|agadir|meknes|oujda|laayoune|dakhla|'
        r'mohammed\s+vi|forces\s+armees\s+royales|'
        r'المغرب|المغربي|المغربية|المغاربة|مغربي|مغربية|الرباط|'
        r'الدار\s+البيضاء|مراكش|فاس|طنجة|اكادير|العيون|الداخلة)\b'),
    'Royaume-Uni': re.compile(
        r'\b(?:royaume\s+uni|grande\s+bretagne|united\s+kingdom|uk|britain|british|'
        r'britannique(?:s)?|angleterre|england|anglais(?:e|es)?|'
        r'ecosse|scotland|ecossais(?:e|es)?|pays\s+de\s+galles|wales|'
        r'northern\s+ireland|irlande\s+du\s+nord|londres|london|'
        r'leicester|birmingham|manchester|westminster|nhs|ukhsa|'
        r'met\s+office|yorkshire|midlands|'
        r'بريطانيا|بريطاني|بريطانية|المملكة\s+المتحدة|لندن)\b'),
    'France': re.compile(
        r'\b(?:france|francais(?:e|es)?|french|paris|lyon|marseille|'
        r'matignon|elysee|macron|francilien(?:ne)?s?|'
        r'فرنسا|الفرنسي|الفرنسية|فرنسي|فرنسية|باريس)\b'),
}


def _region(title, lead='', body=''):
    title, lead, body = normalize(title), normalize(lead), normalize(body)
    # A quoted charity is not the location of a trial or of its participants.
    # In particular Cancer Research UK routinely comments on US/global studies.
    organisation = re.compile(r'\bcancer\s+research\s+uk\b')
    title, lead, body = (organisation.sub('', text) for text in (title, lead, body))
    scores = {}
    for region, pattern in _GEOGRAPHY.items():
        in_title = len(pattern.findall(title))
        in_lead = len(pattern.findall(lead))
        in_body = len(pattern.findall(body))
        # A passing mention near the end of a worldwide report is not its focus.
        # A UK comparison/statistic in the closing paragraphs cannot satisfy the
        # dedicated domestic-news slot: the country must be central from the lead.
        if in_title or in_lead or (region != 'Royaume-Uni' and in_body >= 3):
            scores[region] = 8 * in_title + 3 * in_lead + min(5, in_body)
    # An explicitly Moroccan lead/actor counts toward the cap even when the
    # wider event takes place abroad (e.g. Moroccan officers deployed to Gaza).
    if _GEOGRAPHY['Maroc'].search(title):
        return 'Maroc'
    return max(scores, key=scores.get) if scores else None


def article_region(article):
    """Return subject geography; neither language nor publisher proves it."""
    paragraphs = _get(article, 'paragraphs', []) or []
    return _region(_get(article, 'title', ''), ' '.join(paragraphs[:2]), ' '.join(paragraphs))


def candidate_region(candidate):
    """Preflight hint for RSS candidates; the final French article is rechecked."""
    return _region(_get(candidate, 'title', ''), _get(candidate, 'description', ''))


def is_morocco_article(article):
    return article_region(article) == 'Maroc'


_STOP = frozenset('''
avec pour dans plus apres avant sur les des une que qui est sont cette cet ces
leur leurs notre votre elle elles lui eux tous tout toute toutes aux etait avoir
son ses pas par comme aussi mais ainsi afin entre sans notamment selon cela
tres lors soit ont ete chaque peut dont pendant depuis aujourd hui jour article
information informations actualite actualites actu tour novembre janvier fevrier
mars avril mai juin juillet aout septembre octobre decembre nouveau nouvelle
nouveaux nouvelles premier premiere fois fait faits presente presentee presentees
permet permettre contexte objectif objectifs enjeu enjeux mesure mesures
important importante importance pays region regions population populations
mise notamment sujet sujets point points reste restent cette
the and from after with that this will new news about into their they has have
'''.split())


def _tokens(text):
    result = set()
    for token in re.findall(r'[^\W\d_]{3,}', normalize(text), re.UNICODE):
        if token in _STOP:
            continue
        if token.startswith('hospital') or token in ('hopital', 'hopitaux'):
            token = 'hospital'
        elif token.startswith('minister') or token in ('ministre', 'ministres'):
            token = 'ministre'
        elif token.endswith('s') and not token.endswith('ss'):
            token = token[:-1]
        result.add(token)
    return result


def _overlap(a, b):
    return len(a & b) / max(1, min(len(a), len(b)))


def _jaccard(a, b):
    return len(a & b) / max(1, len(a | b))


def _urls(article):
    return {canonical_url(_get(s, 'url', '')) for s in _get(article, 'source_refs', [])
            if _get(s, 'url', '')}


def _is_roundup(article):
    title = normalize(_get(article, 'title', ''))
    return bool(re.search(r'\btour\s+de\s+l\s+actu(?:alite)?\s+en\s+france\b', title))


def duplicates(a, b):
    """Whether two articles repeat an event, including across publication days.

    A fixed daily roundup heading is deliberately excluded from title matching.
    Different source URLs do not establish different events; conversely two
    unrelated hospital visits or two stories about a country are not duplicates.
    """
    if _get(a, 'id') and _get(a, 'id') == _get(b, 'id'):
        return True
    urls_a, urls_b = _urls(a), _urls(b)
    if urls_a & urls_b:
        # Roundups combine independent stories. Their caller checks each brief;
        # one continuing source alone does not make two whole digests identical.
        if not (_is_roundup(a) and _is_roundup(b)):
            return True
        if urls_a == urls_b:
            return True
    paragraphs_a = _get(a, 'paragraphs', []) or []
    paragraphs_b = _get(b, 'paragraphs', []) or []
    text_a = normalize(' '.join(paragraphs_a)).strip()
    text_b = normalize(' '.join(paragraphs_b)).strip()
    if text_a and text_a == text_b:
        return True
    body_a, body_b = _tokens(' '.join(paragraphs_a)), _tokens(' '.join(paragraphs_b))
    if min(len(body_a), len(body_b)) < 8:
        return False
    title_a, title_b = _tokens(_get(a, 'title', '')), _tokens(_get(b, 'title', ''))
    title_common = len(title_a & title_b)
    title_overlap = _overlap(title_a, title_b)
    body_overlap = _overlap(body_a, body_b)
    # Long near-identical prose is a duplicate even after changing its headline.
    if body_overlap >= .80 and _jaccard(body_a, body_b) >= .65:
        return True
    if _is_roundup(a) or _is_roundup(b):
        return False
    # Shared actor + event + location, with independent corroboration in text.
    if title_common >= 4 and title_overlap >= .78 and body_overlap >= .26:
        return True
    if title_common >= 3 and title_overlap >= .48 and body_overlap >= .48:
        return True
    lead_a = _tokens(' '.join(paragraphs_a[:2]))
    lead_b = _tokens(' '.join(paragraphs_b[:2]))
    return (title_common >= 2 and body_overlap >= .63
            and _overlap(lead_a, lead_b) >= .58 and _jaccard(lead_a, lead_b) >= .42)


def clean_articles(articles, previous_articles=(), *, max_morocco=1):
    """Retain order and IDs, rejecting repetitions and excess Morocco coverage.

    Set max_morocco=None to apply only repetition checks. Previous articles must
    belong to earlier editions, not the edition currently being repaired.
    Reasons identify removed and matching IDs without exposing source text.
    """
    kept, reasons = [], []
    previous_articles = list(previous_articles)
    morocco_count = 0
    for article in articles:
        duplicate = next((other for other in [*previous_articles, *kept]
                          if duplicates(article, other)), None)
        if duplicate is not None:
            reasons.append(f"{_get(article, 'id')}: sujet déjà traité ({_get(duplicate, 'id')})")
            continue
        morocco = is_morocco_article(article)
        if morocco and max_morocco is not None and morocco_count >= max_morocco:
            reasons.append(f"{_get(article, 'id')}: maximum quotidien Maroc atteint")
            continue
        kept.append(article)
        morocco_count += int(morocco)
    return kept, reasons
