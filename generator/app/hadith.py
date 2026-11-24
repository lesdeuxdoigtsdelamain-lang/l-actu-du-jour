"""Publish a dated hadith from an explicitly reviewed Arabic scholarly corpus.

This module never calls an AI provider or a remote website. Arabic quotations,
attribution, grading, French translations and the scholar's explanation are
reviewed together in source control. The daily operation only selects them.
The optional sidecar does not change the strict edition or extras contracts.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import argparse
import json
import re
import unicodedata

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .models import Wire, safe_https
from .publish import atomic

ROOT = Path(__file__).resolve().parents[2]
LONDON = ZoneInfo('Europe/London')
SOURCE_HOSTS = frozenset({'binbaz.org.sa'})
CONTROL = re.compile(r'[\x00-\x1f\x7f<>]')


def text_only(value: str) -> str:
    if CONTROL.search(value):
        raise ValueError('Plain text required')
    # Explicit bidi overrides/isolates may conceal source or quotation text.
    if any(unicodedata.category(c) == 'Cf' for c in value):
        raise ValueError('Hidden formatting controls are not allowed')
    return value


def arabic_text(value: str) -> str:
    text_only(value)
    if not any(unicodedata.category(c).startswith('L') and
               ('\u0600' <= c <= '\u06ff' or '\u0750' <= c <= '\u077f' or
                '\u08a0' <= c <= '\u08ff' or '\ufb50' <= c <= '\ufdff' or
                '\ufe70' <= c <= '\ufeff') for c in value):
        raise ValueError('Original Arabic text required')
    return value


def canonical_date(value):
    if isinstance(value, str):
        if (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or
                date.fromisoformat(value).isoformat() != value):
            raise ValueError('Canonical ISO date required')
    elif not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError('ISO date required')
    return value


class HadithSource(Wire):
    publisher: str = Field(min_length=2, max_length=90)
    scholar: str = Field(min_length=2, max_length=120)
    url: str
    language: Literal['ar']

    @field_validator('publisher', 'scholar')
    @classmethod
    def names(cls, value):
        return text_only(value)

    @field_validator('url')
    @classmethod
    def official_arabic_source(cls, value):
        safe_https(value)
        parsed = urlsplit(value)
        if parsed.hostname not in SOURCE_HOSTS or parsed.path in ('', '/'):
            raise ValueError('Reviewed Arabic scholarly source required')
        return value


class ReviewedHadith(Wire):
    id: str = Field(min_length=12, max_length=64, pattern=r'^[a-z0-9_-]+$')
    arabic: str = Field(min_length=1, max_length=1600)
    narrator_ar: str = Field(min_length=2, max_length=240)
    reference_ar: str = Field(min_length=2, max_length=500)
    authenticity_fr: str = Field(min_length=10, max_length=240)
    translation_fr: str = Field(min_length=5, max_length=1600)
    explanation_fr: str = Field(min_length=10, max_length=1000)
    sources: list[HadithSource] = Field(min_length=1, max_length=3)

    @field_validator('arabic', 'narrator_ar', 'reference_ar')
    @classmethod
    def original_arabic(cls, value):
        return arabic_text(value)

    @field_validator('authenticity_fr', 'translation_fr', 'explanation_fr')
    @classmethod
    def french_text(cls, value):
        return text_only(value)

    @field_validator('authenticity_fr')
    @classmethod
    def reviewed_authenticity(cls, value):
        # A label is copied from the reviewed corpus, never inferred by a model.
        if not value.startswith('Authentique'):
            raise ValueError('Only reviewed authentic hadiths are published')
        return value

    @field_validator('explanation_fr')
    @classmethod
    def short_explanation(cls, value):
        if len(value.split()) > 100:
            raise ValueError('Hadith explanation must remain short')
        return value

    @model_validator(mode='after')
    def distinct_sources(self):
        if len({source.url for source in self.sources}) != len(self.sources):
            raise ValueError('Repeated hadith source')
        return self


class HadithNote(ReviewedHadith):
    edition_date: date

    @field_validator('edition_date', mode='before')
    @classmethod
    def dated(cls, value):
        return canonical_date(value)


class ReviewedCorpus(Wire):
    schema_version: Literal[1] = 1
    reviewed_at: date
    entries: list[ReviewedHadith] = Field(min_length=7, max_length=1000)

    @field_validator('reviewed_at', mode='before')
    @classmethod
    def dated(cls, value):
        return canonical_date(value)

    @model_validator(mode='after')
    def distinct_entries(self):
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError('Repeated hadith id in reviewed corpus')
        # Ignore vowel marks and spaces when detecting a repeated quotation.
        quotations = [''.join(c for c in unicodedata.normalize('NFKD', entry.arabic)
                              if unicodedata.category(c).startswith('L'))
                      for entry in self.entries]
        if len(set(quotations)) != len(quotations):
            raise ValueError('Repeated Arabic quotation in reviewed corpus')
        return self


class HadithFeed(Wire):
    schema_version: Literal[1] = 1
    updated_at: AwareDatetime
    hadiths: list[HadithNote] = Field(min_length=1, max_length=7)

    @model_validator(mode='after')
    def dated_notes(self):
        today = self.updated_at.astimezone(LONDON).date()
        if len({item.edition_date for item in self.hadiths}) != len(self.hadiths):
            raise ValueError('Repeated daily hadith date')
        if any(not 0 <= (today - note.edition_date).days <= 6 for note in self.hadiths):
            raise ValueError('Hadith cache outside seven-day window')
        return self


def load_corpus(path: Path | None = None) -> ReviewedCorpus:
    return ReviewedCorpus.model_validate_json(
        (path or ROOT / 'config' / 'hadith-reviewed.json').read_bytes())


def note_for_day(corpus: ReviewedCorpus, day: date) -> HadithNote:
    # Stable across runners, refreshes and time zones. A complete cycle contains
    # every reviewed entry once. New corpus revisions can change the rotation.
    selected = corpus.entries[day.toordinal() % len(corpus.entries)]
    return HadithNote(**selected.model_dump(), edition_date=day)


def cached_feed(path: Path, now: datetime) -> HadithFeed | None:
    try:
        feed = HadithFeed.model_validate_json(path.read_bytes())
        if feed.updated_at > now + timedelta(minutes=5):
            return None
        if any(note.edition_date > now.astimezone(LONDON).date() for note in feed.hadiths):
            return None
        return feed
    except (OSError, ValueError):
        return None


def hadith_health(site: Path, now: datetime, state: str, feed: HadithFeed,
                  corpus_size: int | None = None):
    today = now.astimezone(LONDON).date()
    current = next((note for note in feed.hadiths if note.edition_date == today), None)
    data = {'checked_at': now.isoformat(), 'date': str(today), 'state': state,
            'ai_calls': 0, 'corpus_size': corpus_size,
            'selected_id': current.id if current else None,
            'published_at': feed.updated_at.isoformat()}
    atomic(site / 'hadith-health.json',
           (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())


def update_hadith(site: Path, now: datetime | None = None, *,
                  corpus_path: Path | None = None) -> HadithFeed:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Timezone-aware publication time required')
    target = site / 'hadith.json'
    try:
        corpus = load_corpus(corpus_path)
        today = now.astimezone(LONDON).date()
        if corpus.reviewed_at > today:
            raise ValueError('Future corpus review date')
        feed = HadithFeed(updated_at=now, hadiths=[
            note_for_day(corpus, today - timedelta(days=offset)) for offset in range(7)])
    except (OSError, ValueError):
        previous = cached_feed(target, now)
        if previous is None:
            raise
        hadith_health(site, now, 'retained', previous)
        print('::warning::Hadith corpus unavailable or invalid; the previous validated file is retained.',
              flush=True)
        return previous
    atomic(target, (feed.model_dump_json(indent=2) + '\n').encode())
    hadith_health(site, now, 'ok', feed, len(corpus.entries))
    print(f'Hadith du {today} publié ; {len(corpus.entries)} textes relus ; aucun appel IA.', flush=True)
    return feed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=ROOT / 'site')
    args = parser.parse_args()
    update_hadith(args.site)


if __name__ == '__main__':
    main()
