"""Prepare one sourced France roundup and remember its individual news items.

Private extraction evidence is used only to prepare an AI packet. The memory
records contain the published French prose and public source metadata, never
the publisher's extracted paragraphs.
"""
from dataclasses import replace
from datetime import date
import json
import re

from .diversity import candidate_region, duplicates, normalize
from .evidence import make_facts
from .ingest import Candidate, EXCLUDE


_MONTHS = ('', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
           'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
_ABROAD = re.compile(
    r'\b(?:ukraine|ukrainien(?:ne)?s?|russie|russes?|moscou|kiev|gaza|'
    r'israel|israelien(?:ne)?s?|iran|iranien(?:ne)?s?|liban|libanais|'
    r'syrie|syrien(?:ne)?s?|soudan|soudanais|senegal|senegalais|'
    r'chine|chinois(?:e)?s?|pekin|washington|etats\s+unis|americain(?:e)?s?|'
    r'royaume\s+uni|londres|britannique(?:s)?|allemagne|berlin|'
    r'italie|rome|espagne|madrid|argentine|bresil|haiti|inde|pakistan|'
    r'afghanistan|turquie|indonesie|algerie|tunisie|maroc|morocco)\b')
_DOMESTIC = re.compile(
    r'\b(?:france|paris|lyon|marseille|'
    r'francais(?:e|es)?|francilien(?:ne)?s?|matignon|'
    r'sncf|ratp)\b')
_ENTERTAINMENT = re.compile(
    r'\b(?:gta(?:\s*(?:vi|6))?|grand\s+theft\s+auto|jeux?\s+video|'
    r'video\s+games?|gamers?|gaming|playstation|xbox|nintendo|'
    r'cinema|box\s+office|blockbuster|album|concert|telenovela)\b')
_PERSON_NAME = re.compile(
    r'\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿA-ZÀ-ÖØ-Þ’\'-]+'
    r'(?:\s+(?:(?:de|du|des|le|la)\s+)?[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿA-ZÀ-ÖØ-Þ’\'-]+){1,3}\b')
_GENERIC_NAME_WORDS = {'le', 'la', 'les', 'un', 'une', 'des', 'en', 'au', 'aux',
                       'france', 'francais', 'paris', 'premier', 'ministre', 'president',
                       'parlement', 'senat', 'assemblee', 'nationale', 'tour', 'actu'}


def is_roundup(article):
    title = article.get('title', '') if isinstance(article, dict) else getattr(article, 'title', '')
    return bool(re.search(r'\btour\s+de\s+l\s+actu(?:alite)?\s+en\s+france\b', normalize(title)))


def domestic_france(candidate):
    """A French-language world feed is not a France domestic-news feed."""
    text = candidate.title + ' ' + candidate.description
    if EXCLUDE.search(text) or _ENTERTAINMENT.search(normalize(text)):
        return False
    if re.search(r'/(?:culture/)?(?:jeux-video|cinema|musique|series|gaming|entertainment)/', candidate.url):
        return False
    region = candidate_region(candidate)
    if region in ('Maroc', 'Royaume-Uni'):
        return False
    title = normalize(candidate.title)
    # Diplomacy happening abroad does not use up one of the domestic briefs.
    if _ABROAD.search(title) and not _DOMESTIC.search(title):
        return False
    return region == 'France' or candidate.feed.category == 'France'


def same_roundup_subject(a, b):
    """A compact digest does not split one actor's announcement and its profile.

    This intentionally stricter variety check applies only within a roundup,
    not to the seven-day event gate: different news about a person can still be
    meaningful on different days. A full named actor in one headline is matched
    against the other headline, including a surname-only headline.
    """
    if duplicates(a, b):
        return True
    def title(value):
        return value.get('title', '') if isinstance(value, dict) else value.title
    left, right = title(a), title(b)
    def surnames(text):
        result = set()
        for match in _PERSON_NAME.finditer(text):
            words = [w for w in normalize(match.group()).split()
                     if w not in _GENERIC_NAME_WORDS and w not in ('de', 'du', 'des')]
            if len(words) >= 2 and len(words[-1]) >= 4:
                result.add(words[-1])
        return result
    return bool(surnames(left) & set(normalize(right).split())
                or surnames(right) & set(normalize(left).split()))


def _trim(text, limit):
    text = ' '.join(str(text).split())
    if len(text) <= limit:
        return text
    prefix = text[:limit].rsplit(' ', 1)[0]
    endings = list(re.finditer(r'[.!?](?:\s|$)', prefix))
    if endings and len(prefix[:endings[-1].end()].split()) >= 40:
        return prefix[:endings[-1].end()].strip()
    return prefix


def _one_source(facts, sources):
    """Use one adequately documented source per topic; retain real fact IDs."""
    options = []
    for source in sources:
        selected = []
        for fact in facts:
            if fact.get('source_id') != source['id']:
                continue
            text = _trim(fact.get('text', ''), 1100)
            if len(text.split()) < 10:
                continue
            tokens = set(normalize(text).split())
            match = next((i for i, previous in enumerate(selected)
                          if len(tokens & set(normalize(previous['text']).split())) >= 15
                          and len(tokens & set(normalize(previous['text']).split()))
                          / max(1, min(len(tokens), len(set(normalize(previous['text']).split())))) >= .90), None)
            if match is not None:
                if len(text) > len(selected[match]['text']):
                    selected[match] = {**fact, 'text': text}
                continue
            if len(selected) < 2:
                selected.append({**fact, 'text': text})
        # Up to two distinct excerpts; the final common packet budget is lower.
        words = sum(len(f['text'].split()) for f in selected)
        if words >= 40:
            options.append((words, selected, source))
    if not options:
        return None
    _, selected, source = max(options, key=lambda item: item[0])
    return selected, source


def _topic(candidate, facts, source):
    return {'id': candidate.id, 'title': candidate.title,
            'fact_ids': [f['id'] for f in facts], 'source_ids': [source['id']]}


def _preview(candidate, facts, source):
    return {'id': candidate.id, 'title': candidate.title,
            'paragraphs': [f['text'] for f in facts], 'source_refs': [source]}


def _fit(chosen):
    """Trim the richest topic first while preserving all topics and evidence floor."""
    def packet():
        facts = [f for _, fs, _ in chosen for f in fs]
        sources = [source for _, _, source in chosen]
        topics = [_topic(c, fs, source) for c, fs, source in chosen]
        return facts, sources, topics
    while True:
        facts, sources, topics = packet()
        size = len(json.dumps({'facts': facts, 'sources': sources, 'topics': topics}, ensure_ascii=False))
        if size <= 8500:
            return facts, sources, topics
        targets = [(len(f['text'].split()), f) for _, fs, _ in chosen for f in fs
                   if len(f['text'].split()) > 50]
        if not targets:
            return None
        _, longest = max(targets, key=lambda item: item[0])
        words = longest['text'].split()
        longest['text'] = ' '.join(words[:-10])


def prepare_roundup(candidates, http, day, make_facts_fn=make_facts, previous_articles=()):
    """Return (synthetic_candidate, facts, sources, topics), or insufficient evidence.

    Four independent domestic topics are preferred, three are the minimum. Each
    has at least forty evidence words, with at least 280 words across the final
    packet. A shortage returns None; the caller must not invent a daily roundup.
    The caller supplies already date-filtered candidates and enforces 300–400
    output words, topic coverage, and the ordinary article quality checks.
    """
    if isinstance(day, str):
        day = date.fromisoformat(day)
    previous_articles = list(previous_articles)
    chosen = []
    for candidate in candidates:
        if not domestic_france(candidate):
            continue
        initial = {'id': candidate.id, 'title': candidate.title,
                   'paragraphs': [candidate.description],
                   'source_refs': [{'url': candidate.url}]}
        if any(duplicates(initial, old) for old in previous_articles):
            continue
        facts, sources = make_facts_fn(candidate, candidate.clustered, http)
        selected = _one_source(facts, sources)
        if selected is None:
            continue
        topic_facts, source = selected
        preview = _preview(candidate, topic_facts, source)
        if any(duplicates(preview, old) for old in previous_articles):
            continue
        if any(same_roundup_subject(preview, _preview(c, fs, s)) for c, fs, s in chosen):
            continue
        if len(chosen) < 4:
            chosen.append((candidate, topic_facts, source))
        else:
            smallest = min(range(len(chosen)), key=lambda i: sum(len(f['text'].split()) for f in chosen[i][1]))
            if sum(len(f['text'].split()) for f in topic_facts) > sum(len(f['text'].split()) for f in chosen[smallest][1]):
                chosen[smallest] = (candidate, topic_facts, source)
        if len(chosen) == 4 and sum(len(f['text'].split()) for _, fs, _ in chosen for f in fs) >= 280:
            break
    if len(chosen) < 3:
        return None
    fitted = _fit(chosen)
    if fitted is None:
        return None
    facts, sources, topics = fitted
    if sum(len(f['text'].split()) for f in facts) < 280:
        return None
    if any(sum(len(f['text'].split()) for f in fs) < 40 for _, fs, _ in chosen):
        return None
    first = chosen[0][0]
    french_date = f'{day.day} {_MONTHS[day.month]} {day.year}'
    synthetic = Candidate(
        id=f'france-tour-{day.isoformat()}',
        title=f'Tour de l’actu en France — {french_date}',
        description='Brèves distinctes de l’actualité française du jour.',
        url=first.url,
        published_at=max(c.published_at for c, _, _ in chosen),
        feed=replace(first.feed, category='France'),
    )
    return synthetic, facts, sources, topics


def topic_records(article, proofs, topics, facts, sources):
    """Store only published French paragraphs associated with each proven topic."""
    paragraphs = article.get('paragraphs', []) if isinstance(article, dict) else article.paragraphs
    if not isinstance(proofs, list) or len(proofs) != len(paragraphs):
        return []
    fact_map = {f['id']: f for f in facts}
    if any(not isinstance(refs, list) or not refs
           or any(not isinstance(ref, str) or ref not in fact_map for ref in refs) for refs in proofs):
        return []
    records = []
    for topic in topics:
        fact_ids = set(topic['fact_ids'])
        indices = [i for i, refs in enumerate(proofs) if fact_ids & set(refs)]
        if not indices:
            continue
        published = [paragraphs[i] for i in indices]
        used = {ref for i in indices for ref in proofs[i] if ref in fact_ids}
        source_ids = {fact_map[ref]['source_id'] for ref in used}
        refs = [{k: value for k, value in source.items() if k in ('publisher', 'url', 'language', 'published_at')}
                for source in sources if source['id'] in source_ids]
        if not refs:
            continue
        # The title is also generated French, even if the RSS headline was not.
        title = re.split(r'(?<=[.!?])\s+', published[0], maxsplit=1)[0]
        title = title if len(title) <= 180 else title[:180].rsplit(' ', 1)[0]
        records.append({'id': topic['id'], 'title': title, 'category': 'France',
                        'paragraphs': published, 'source_refs': refs})
    return records
