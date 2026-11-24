"""Published JSON contract for the Android app. No HTML and no secrets."""
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re

CATEGORIES = ('Monde','Maroc','Monde musulman','Royaume-Uni','France',
              'Économie','Science et technologie','Santé','Société','Environnement')
Category = Literal['Monde','Maroc','Monde musulman','Royaume-Uni','France',
                   'Économie','Science et technologie','Santé','Société','Environnement']


def safe_https(url: str) -> str:
    u = urlsplit(url)
    if (len(url) > 2048 or any(c.isspace() for c in url) or u.scheme != 'https'
        or not u.hostname or u.username or u.password or u.port not in (None, 443)
        or u.hostname in ('localhost','example.com','example.org')
        or re.fullmatch(r'[\d.:]+',u.hostname)):
        raise ValueError('HTTPS public address required')
    return url

class Wire(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Source(Wire):
    publisher: str = Field(min_length=2,max_length=90)
    url: str
    language: str = Field(pattern=r'^(fr|ar|en|es|other)$')
    published_at: datetime
    @field_validator('url')
    @classmethod
    def url_ok(cls, v): return safe_https(v)

class Image(Wire):
    url: str
    alt: str = Field(min_length=5,max_length=160)
    credit: str = Field(min_length=2,max_length=140)
    license: str = Field(min_length=2,max_length=120)
    page: str
    @field_validator('url','page')
    @classmethod
    def url_ok(cls, v): return safe_https(v)
    @model_validator(mode='after')
    def rights(self):
        if urlsplit(self.url).hostname != 'upload.wikimedia.org':
            raise ValueError('Only independently licensed Commons images')
        if urlsplit(self.page).hostname != 'commons.wikimedia.org':
            raise ValueError('Source page required')
        return self

class Article(Wire):
    id: str = Field(min_length=12,max_length=64)
    category: Category
    title: str = Field(min_length=18,max_length=180)
    paragraphs: list[str] = Field(min_length=4,max_length=13)
    image: Image | None = None
    source_refs: list[Source] = Field(min_length=1,max_length=5)
    word_count: int = Field(ge=280,le=780)
    minutes: int = Field(ge=2,le=7)
    @model_validator(mode='after')
    def validate_text(self):
        body=' '.join(self.paragraphs)
        count=len(body.split())
        if count != self.word_count:
            raise ValueError('Word count inconsistent')
        if not 280 <= count <= 780:
            raise ValueError('An actual developed article is required')
        if len(self.title.split()) < 4:
            raise ValueError('Non descriptive title')
        if any(len(p.split()) < 18 or len(p.split()) > 160 for p in self.paragraphs):
            raise ValueError('Paragraphs too short or too long')
        if any('<' in t or '>' in t or '\x00' in t for t in [self.title,*self.paragraphs]):
            raise ValueError('Markup not allowed')
        if self.minutes != max(2, min(7, round(count / 170))):
            raise ValueError('Reading minutes inconsistent')
        return self

class Edition(Wire):
    schema_version: Literal[1] = 1
    edition_id: str
    edition_date: date
    generated_at: datetime
    language: Literal['fr-FR'] = 'fr-FR'
    timezone: Literal['Europe/London'] = 'Europe/London'
    status: Literal['complete','partielle']
    minutes: int = Field(ge=2,le=50)
    articles: list[Article] = Field(min_length=1,max_length=8)
    @model_validator(mode='after')
    def validate_edition(self):
        if self.edition_id != f'{self.edition_date}-fr-FR':
            raise ValueError('Incorrect edition id')
        if len({s.id for s in self.articles}) != len(self.articles):
            raise ValueError('Repeated articles')
        if sum(a.minutes for a in self.articles) != self.minutes:
            raise ValueError('Incorrect estimated reading time')
        # The writer now publishes five/six; keep older seven/eight-item
        # archives readable instead of retroactively invalidating their hashes.
        if self.status == 'complete' and not (5 <= len(self.articles) <= 8):
            raise ValueError('Complete edition needs at least five articles')
        return self

class Manifest(Wire):
    schema_version: Literal[1] = 1
    date: date
    path: str = Field(pattern=r'^editions/\d{4}-\d\d-\d\d\.json$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    published_at: datetime
