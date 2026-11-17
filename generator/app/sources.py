"""Whitelist of known publisher feeds. Candidates are never presumed reusable photos."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import json

@dataclass(frozen=True)
class Feed:
    id: str
    publisher: str
    url: str
    language: str
    category: str
    hosts: tuple[str,...]
    policy: str  # 'rss' facts only, 'open' paragraph evidence explicitly reusable


def load(path: Path):
    out=[]
    for item in json.loads(path.read_text(encoding='utf-8'))['feeds']:
        if not item.get('enabled',True): continue
        hosts=set(item['hosts']) | {urlsplit(item['url']).hostname}
        assert item['policy'] in ('rss','open')
        out.append(Feed(item['id'],item['publisher'],item['url'],item['language'],
                        item['category'],tuple(sorted(hosts)),item['policy']))
    return out
