"""Optional, independently refreshed urgent alerts and a short historical note.

The edition wire format stays unchanged for installed readers. Scheduled alert
checks use public RSS metadata only: they never instantiate an AI provider.
Historical notes are cached by London date; a persisted attempt marker prevents
the evening retry from spending a second model request on the same day.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import argparse
import json
import re

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .editor import NUMERIC, normalized_number
from .fetch import Fetcher, FetchError
from .ingest import collect, content_tokens, norm, plain, same_event
from .models import Wire, safe_https
from .publish import atomic
from .sources import load

ROOT = Path(__file__).resolve().parents[2]
LONDON = ZoneInfo('Europe/London')
ALERT_TTL = timedelta(hours=3)
MONTHS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet',
          'août', 'septembre', 'octobre', 'novembre', 'décembre')
CONTROL = re.compile(r'[\x00-\x1f\x7f<>]')


def plain_text(value):
    if not isinstance(value, str) or CONTROL.search(value):
        raise ValueError('Plain text required')
    return value


def canonical_date(value):
    if isinstance(value, str) and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)
                                   or date.fromisoformat(value).isoformat() != value):
        raise ValueError('Canonical ISO date required')
    if not isinstance(value, (str, date)) or isinstance(value, datetime):
        raise ValueError('ISO date required')
    return value


class ExtraSource(Wire):
    publisher: str = Field(min_length=2, max_length=90)
    url: str

    @field_validator('publisher')
    @classmethod
    def publisher_text(cls, value):
        return plain_text(value)

    @field_validator('url')
    @classmethod
    def url_ok(cls, value):
        safe_https(value)
        host = urlsplit(value).hostname.casefold()
        if host == 'localhost.localdomain' or host.endswith(('.local', '.localhost')):
            raise ValueError('Public HTTPS address required')
        return value


class UrgentAlert(Wire):
    id: str = Field(min_length=12, max_length=64)
    title: str = Field(min_length=12, max_length=200)
    summary: str = Field(default='', max_length=400)
    published_at: AwareDatetime
    expires_at: AwareDatetime
    sources: list[ExtraSource] = Field(min_length=1, max_length=5)

    @field_validator('id', 'title', 'summary')
    @classmethod
    def text_only(cls, value):
        return plain_text(value)

    @model_validator(mode='after')
    def expiry(self):
        if not timedelta(0) < self.expires_at - self.published_at <= ALERT_TTL:
            raise ValueError('Urgent alert must expire within three hours')
        return self


class HistoricalNote(Wire):
    edition_date: date
    event_date: date
    lines: list[str] = Field(min_length=6, max_length=10)
    sources: list[ExtraSource] = Field(min_length=1, max_length=5)

    @field_validator('edition_date', 'event_date', mode='before')
    @classmethod
    def dates(cls, value):
        return canonical_date(value)

    @field_validator('lines')
    @classmethod
    def text_lines(cls, values):
        if any(not 1 <= len(plain_text(line).strip()) <= 240 for line in values):
            raise ValueError('Historical lines must be short plain text')
        if len({norm(line) for line in values}) != len(values):
            raise ValueError('Repeated historical lines')
        return [line.strip() for line in values]

    @model_validator(mode='after')
    def anniversary(self):
        if (self.event_date >= self.edition_date or
                (self.event_date.month, self.event_date.day) !=
                (self.edition_date.month, self.edition_date.day)):
            raise ValueError('Historical event must match this day and month in a past year')
        return self


class Extras(Wire):
    schema_version: Literal[1] = 1
    updated_at: AwareDatetime
    alerts: list[UrgentAlert] = Field(default_factory=list, max_length=3)
    history: list[HistoricalNote] = Field(default_factory=list, max_length=7)

    @model_validator(mode='after')
    def distinct(self):
        if len({item.id for item in self.alerts}) != len(self.alerts):
            raise ValueError('Repeated urgent alerts')
        if len({item.edition_date for item in self.history}) != len(self.history):
            raise ValueError('Repeated historical dates')
        if any(a.published_at > self.updated_at for a in self.alerts):
            raise ValueError('Future urgent alert')
        today = self.updated_at.astimezone(LONDON).date()
        if any(not 0 <= (today - h.edition_date).days <= 6 for h in self.history):
            raise ValueError('Historical cache outside seven-day window')
        return self


# Fresh publication is necessary but not sufficient: retrospectives, rumours,
# exercises and political/online "attacks" must not become a red emergency.
NOT_LIVE = re.compile(
    r'\b(?:anniversair\w*|commemor\w*|proces|retrospect\w*|souvenir\w*|'
    r'menac\w*|simul\w*|exercice\w*|dejou\w*|dement\w*|rumeur\w*|'
    r'cyberattaqu\w*|enquete\w*|accus\w*|condamn\w*|fiction|film|'
    r'risque|pourrait|possible|suppose\w*)\b|'
    r'\b(?:il y a|retour sur|fausse alerte|alerte levee|sans victime|'
    r'aucune? (?:victime|mort|blesse|degat)|attaque (?:verbale|politique)|'
    r'en (?:19|20)\d{2})\b')
DEATHS = re.compile(r'\b(\d{1,6}|dizaines|centaines|milliers)\s+(?:de\s+)?morts?\b')
GENERIC_NAMES = set('''un une des les le la de du au aux en ce cette cet ces
    direct urgent alerte info selon apres avant depuis nouveau nouvelle nouveaux
    nouvelles plusieurs nombreux nombreuses premier premiere puissant puissante
    violent violente fort forte meurtrier meurtriere seisme tremblement terre
    attentat attaque attaques fusillade fusillades tsunami explosion bombardement
    frappes inondation inondations ouragan cyclone incendie eruption magnitude
    morts mort blesses blesse victimes bilan dizaines centaines milliers
    ville capitale centre pays region gouvernement police secours hopital
    dimanche lundi mardi mercredi jeudi vendredi samedi dernier derniere
    images video videos voici comment pourquoi'''.split())


def named_anchors(title):
    """A shared disaster word or death toll is not a shared event identity.

    Proper-name anchors intentionally favour precision over recall. If both
    headlines add conflicting named places (Paris/France versus Lyon/France),
    the common country alone must not merge them into one corroborated event.
    """
    words = re.findall(r"(?<!\w)(?:[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ][a-zàâçéèêëîïôùûü'’\-]{2,}|[A-ZÀÂÇÉÈÊËÎÏÔÙÛÜ]{3,})(?!\w)", title)
    return {norm(word).strip() for word in words if norm(word).strip() not in GENERIC_NAMES}


def alert_same_event(a, b):
    left, right = named_anchors(a), named_anchors(b)
    return (bool(left & right) and not (left - right and right - left)
            and same_event(a, b))


def severe_kind(title):
    """Conservative lexical triage, followed by cross-publisher corroboration."""
    text = norm(title)
    if NOT_LIVE.search(text):
        return None
    deaths = [m.group(1) for m in DEATHS.finditer(text)]
    mass_casualties = any(not count.isdecimal() or int(count) >= 10 for count in deaths)
    if re.search(r'\b(?:attentats?|fusillades?|attaque terroriste)\b', text):
        return 'attack'
    if re.search(r'\b(?:seisme|tremblement de terre)\b', text):
        # Inspect the un-normalized title to preserve a decimal magnitude.
        magnitudes = re.findall(r'magnitude\s*(?:de\s*)?(\d(?:[.,]\d)?)', title, re.I)
        if (any(float(m.replace(',', '.')) >= 6.5 for m in magnitudes)
                or mass_casualties or re.search(r'\b(?:puissant|violent|meurtrier|devastateur)\b', text)):
            return 'earthquake'
    if re.search(r'\b(?:alerte (?:au |de |a un )?tsunami|tsunami (?:frappe|touche|devaste))\b', text):
        return 'tsunami'
    if re.search(r'\b(?:attaque|frappes?|bombardement|massacre|explosion)\b', text) and mass_casualties:
        return 'attack'
    if re.search(r'\b(?:inondations?|cyclone|ouragan|incendie|eruption)\b', text) and mass_casualties:
        return 'disaster'
    return None


def select_alerts(candidates, now, previous=()):
    """No single RSS story is enough, and repeated checks never extend a TTL."""
    fresh = [candidate for candidate in candidates
             if candidate.feed.language == 'fr'
             and now - ALERT_TTL < candidate.published_at <= now
             and severe_kind(candidate.title)]
    groups = []
    for candidate in sorted(fresh, key=lambda c: (c.published_at, c.url), reverse=True):
        if any(candidate.url == item.url for group in groups for item in group):
            continue
        group = next((group for group in groups
                      if severe_kind(group[0].title) == severe_kind(candidate.title)
                      and alert_same_event(group[0].title, candidate.title)), None)
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    active = [alert for alert in previous if alert.published_at <= now < alert.expires_at]
    for group in groups:
        publishers = {}
        for candidate in group:
            publishers.setdefault(candidate.feed.publisher.casefold(), candidate)
        if len(publishers) < 2:
            continue
        sources = [ExtraSource(publisher=c.feed.publisher, url=c.url)
                   for c in list(publishers.values())[:5]]
        chosen = group[0]
        # A short, explicitly sourced headline; do not republish RSS paragraphs.
        headline = ' '.join(chosen.title.split()[:25])[:200].strip()
        published = min(c.published_at for c in group)
        expiry = published + ALERT_TTL
        known = next((a for a in previous if alert_same_event(a.title, chosen.title)
                      or {s.url for s in a.sources} & {s.url for s in sources}), None)
        if known:
            # Keep the original observation window, even when a publisher edits
            # an item's timestamp or another URL reports the same event later.
            active = [a for a in active if a.id != known.id]
            published = min(published, known.published_at)
            expiry = min(expiry, known.expires_at)
        if expiry <= now:
            continue
        active.append(UrgentAlert(id=known.id if known else sha256(chosen.url.encode()).hexdigest()[:22],
            title=headline, summary='', published_at=published, expires_at=expiry, sources=sources))
    return sorted(active, key=lambda a: (a.published_at, a.id), reverse=True)[:3]


def load_history_event(day, fetcher=None):
    """Use Wikimedia's day/month feed, not a model's recollection of a date.

    Official interface: mediawiki.org/wiki/Wikifeeds#.../feed/onthisday/{type}/{mm}/{dd}
    Only French Wikipedia page links and extracts returned by that feed are used.
    """
    fetcher = fetcher or Fetcher({'fr.wikipedia.org'})
    endpoint = f'https://fr.wikipedia.org/api/rest_v1/feed/onthisday/events/{day:%m/%d}'
    body = json.loads(fetcher.get(endpoint, limit=1800000))
    if not isinstance(body, dict) or not isinstance(body.get('events'), list):
        raise ValueError('Invalid historical calendar response')
    calendar = f'https://fr.wikipedia.org/wiki/{day.day}_{MONTHS[day.month - 1]}'
    pool = []
    for event in body['events'][:200]:
        if not isinstance(event, dict):
            continue
        year = event.get('year')
        if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year < day.year:
            continue
        try:
            event_day = date(year, day.month, day.day)
        except ValueError:
            continue
        summary = plain(event.get('text') if isinstance(event.get('text'), str) else '')[:1200]
        if len(summary.split()) < 10:
            continue
        sources = [ExtraSource(publisher='Wikipédia (CC BY-SA)', url=calendar)]
        facts = [{'id': 'H1', 'text': f'{day.day} {MONTHS[day.month - 1]} {year} : {summary}'}]
        pages = event.get('pages', [])
        if not isinstance(pages, list):
            continue
        useful = []
        for page in pages[:12]:
            if not isinstance(page, dict):
                continue
            title = page.get('title', '')
            extract = plain(page.get('extract') if isinstance(page.get('extract'), str) else '')[:3500]
            try:
                url = page.get('content_urls', {}).get('desktop', {}).get('page', '')
                source = ExtraSource(publisher='Wikipédia (CC BY-SA)', url=url)
                if urlsplit(source.url).hostname != 'fr.wikipedia.org':
                    continue
            except (ValueError, TypeError, AttributeError):
                continue
            if not isinstance(title, str) or len(extract.split()) < 35:
                continue
            # Year, calendar and generic country links provide poor evidence for
            # what this particular event changed; prefer the named event itself.
            relevance = len(content_tokens(title) & content_tokens(summary))
            if not relevance or re.fullmatch(r'\d{1,4}', title):
                continue
            useful.append((relevance, extract, source))
        for _, extract, source in sorted(useful, key=lambda row: row[0], reverse=True)[:3]:
            if source.url not in {s.url for s in sources}:
                sources.append(source)
                facts.append({'id': f'H{len(facts) + 1}', 'text': extract})
        if len(facts) < 2 or sum(len(f['text'].split()) for f in facts) < 80:
            continue
        packet = {'edition_date': day.isoformat(), 'event_date': event_day.isoformat(),
                  'event_summary': summary, 'facts': facts}
        quality = min(6, max((u[0] for u in useful), default=0))
        tie = sha256((str(day) + str(year) + summary).encode()).hexdigest()
        pool.append((quality, tie, packet, sources))
    if not pool:
        raise ValueError('No sufficiently documented historical event for this date')
    # Deterministic for a given date, with diversity rather than always selecting
    # the first entry in a reverse-chronological calendar.
    eligible = sorted(pool, key=lambda item: (-item[0], item[1]))[:8]
    selected = eligible[int(sha256(str(day).encode()).hexdigest()[:8], 16) % len(eligible)]
    return selected[2], selected[3]


def historical_note(output, packet, sources):
    """Reject unsupported numeric claims and missing/unknown per-line evidence."""
    if not isinstance(output, dict) or set(output) != {'lines', 'evidence'}:
        raise ValueError('Invalid historical response')
    lines, evidence = output['lines'], output['evidence']
    if not isinstance(lines, list) or not isinstance(evidence, list) or len(lines) != len(evidence):
        raise ValueError('Missing historical evidence')
    facts = {fact['id']: fact['text'] for fact in packet['facts']}
    for line, refs in zip(lines, evidence):
        plain_text(line)
        if (not isinstance(refs, list) or not refs or len(refs) > 5
                or any(not isinstance(ref, str) or ref not in facts for ref in refs)):
            raise ValueError('Unknown historical evidence')
        allowed = {normalized_number(number, 'fr') for ref in refs
                   for number in NUMERIC.findall(facts[ref])}
        used = {normalized_number(number, 'fr') for number in NUMERIC.findall(line)}
        if None in used or used - allowed:
            raise ValueError('Unsupported historical number')
    if not lines or 'H1' not in evidence[0]:
        raise ValueError('First historical line must describe the dated event')
    if len(content_tokens(lines[0]) & content_tokens(packet['event_summary'])) < 2:
        raise ValueError('Historical event omitted')
    text = ' '.join(lines)
    if not 60 <= len(text.split()) <= 160:
        raise ValueError('Historical note must remain short')
    if re.search(r"\b(?:aujourd hui|hier|demain|cette annee|cette semaine)\b", norm(text)):
        raise ValueError('Use absolute historical dates')
    for fact in facts.values():
        words = fact.casefold().split()
        if any(' '.join(words[i:i + 26]) in text.casefold() for i in range(len(words) - 25)):
            raise ValueError('Historical source must be paraphrased')
    return HistoricalNote(edition_date=packet['edition_date'], event_date=packet['event_date'],
                          lines=lines, sources=sources)


def reviewed_note(day, path=None):
    """Optional editorially reviewed entries; never use another calendar date."""
    path = path or ROOT / 'config' / 'history-reviewed.json'
    if not path.exists():
        return None
    item = json.loads(path.read_text(encoding='utf-8')).get(f'{day:%m-%d}')
    return HistoricalNote(edition_date=day, **item) if item else None


def _read_health(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_health(path, health):
    atomic(path, (json.dumps(health, ensure_ascii=False, indent=2) + '\n').encode())


def update_extras(site, now, feeds, *, alerts_only=False, history_only=False,
                  provider=None, collector=collect, history_loader=load_history_event,
                  reviewed_path=None):
    if alerts_only and history_only:
        raise ValueError('Choose at most one supplements mode')
    if now.tzinfo is None:
        raise ValueError('Timezone-aware current time required')
    site = Path(site)
    day = now.astimezone(LONDON).date()
    health_path = site / 'extras-health.json'
    health = _read_health(health_path)
    health.update({'checked_at': now.isoformat()})
    path = site / 'extras.json'
    try:
        previous = Extras.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        previous = Extras(updated_at=now)
    alerts = [a for a in previous.alerts if a.published_at <= now < a.expires_at]
    notes = [h for h in previous.history if 0 <= (day - h.edition_date).days <= 6]
    # A small public ledger remembers recently expired signals too. Updating an
    # RSS timestamp must not keep the same event red indefinitely after expiry.
    remembered = {a.id: a for a in previous.alerts}
    memory_items = health.get('alert_memory', [])
    for item in (memory_items[:36] if isinstance(memory_items, list) else []):
        try:
            alert = UrgentAlert.model_validate(item)
            if now - timedelta(days=1) < alert.published_at <= now:
                remembered.setdefault(alert.id, alert)
        except (ValueError, TypeError):
            continue

    if not history_only:
        try:
            candidates, feed_health = collector([f for f in feeds if f.language == 'fr'], now)
            alerts = select_alerts(candidates, now, list(remembered.values()))
            health['feeds'] = feed_health
            health['alerts_state'] = 'checked'
            health.pop('alerts_error', None)
        except Exception as exc:
            # Keep only already validated, still-live alerts during an outage.
            # Never write arbitrary exception bodies, HTTP content or secrets.
            health['alerts_state'] = 'source_error'
            health['alerts_error'] = type(exc).__name__[:60]

    if not alerts_only:
        health['history_date'] = str(day)
        if any(h.edition_date == day for h in notes):
            health['history_state'] = 'cached'
        else:
            try:
                manual = reviewed_note(day, reviewed_path)
                if manual:
                    notes.append(manual)
                    health['history_state'] = 'reviewed'
                    health.pop('history_error', None)
                elif health.get('history_attempted_for') == str(day):
                    health['history_state'] = 'attempt_already_used'
                else:
                    if provider is None:
                        from .providers import configured
                        provider = configured()
                    if provider is None:
                        health['history_state'] = 'provider_unconfigured'
                    else:
                        packet, sources = history_loader(day)
                        # Persist BEFORE requesting generated text. This marker
                        # also survives a later exception/invalid model output.
                        health['history_attempted_for'] = str(day)
                        health['history_state'] = 'attempt_started'
                        _write_health(health_path, health)
                        notes.append(historical_note(provider.run_history(packet), packet, sources))
                        health['history_state'] = 'generated'
                        health.pop('history_error', None)
            except Exception as exc:
                health['history_state'] = 'unavailable'
                health['history_error'] = type(exc).__name__[:60]
            finally:
                if provider is not None and hasattr(provider, 'usage_stats'):
                    health['history_ai_usage'] = provider.usage_stats()
    health['alert_count'] = len(alerts)
    remembered.update({a.id: a for a in alerts})
    health['alert_memory'] = [a.model_dump(mode='json') for a in
        sorted(remembered.values(), key=lambda a: a.published_at, reverse=True)
        if now - timedelta(days=1) < a.published_at <= now][:36]
    health['history_available'] = any(h.edition_date == day for h in notes)
    extras = Extras(updated_at=now, alerts=alerts,
                    history=sorted(notes, key=lambda h: h.edition_date, reverse=True)[:7])
    atomic(path, (extras.model_dump_json(indent=2) + '\n').encode())
    _write_health(health_path, health)
    return extras


def main():
    parser = argparse.ArgumentParser(description='Refresh optional urgent alerts and historical notes')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--alerts-only', action='store_true', help='RSS only; no AI calls')
    mode.add_argument('--history-only', action='store_true', help='One cached historical note for today')
    parser.add_argument('--site', type=Path, default=ROOT / 'site')
    args = parser.parse_args()
    extras = update_extras(args.site, datetime.now(timezone.utc), load(ROOT / 'config' / 'sources.json'),
                           alerts_only=args.alerts_only, history_only=args.history_only)
    print(f'Suppléments : {len(extras.alerts)} alerte(s) active(s), {len(extras.history)} note(s) conservée(s).')


if __name__ == '__main__':
    main()
