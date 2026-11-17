"""Validate, atomically publish dated editions and integrity manifests."""
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
import os
from .models import Edition, Manifest

def atomic(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_bytes(raw)
    os.replace(tmp,path)

def put(site:Path,edition:Edition,now:datetime):
    raw=(edition.model_dump_json(indent=2)+'\n').encode()
    path=f'editions/{edition.edition_date}.json'
    man=Manifest(date=edition.edition_date,path=path,sha256=sha256(raw).hexdigest(),published_at=now)
    atomic(site/path,raw)
    cutoff=edition.edition_date.__class__.fromordinal(edition.edition_date.toordinal()-6)
    for old in (site/'editions').glob('*.json'):
        try: day=edition.edition_date.__class__.fromisoformat(old.stem)
        except ValueError: continue
        if day<cutoff:old.unlink()
    atomic(site/'latest.json',(man.model_dump_json(indent=2)+'\n').encode())
    return man

def validate(site:Path):
    for f in (site/'editions').glob('*.json'):
        e=Edition.model_validate_json(f.read_bytes())
        if f.name!=f'{e.edition_date}.json':raise ValueError('Mismatch edition filename')
    if (site/'latest.json').exists():
        m=Manifest.model_validate_json((site/'latest.json').read_bytes())
        data=(site/m.path).read_bytes()
        if sha256(data).hexdigest()!=m.sha256:raise ValueError('Edition hash mismatch')
        e=Edition.model_validate_json(data)
        if e.edition_date!=m.date:raise ValueError('Date mismatch')

def history(site,today,lookback=7):
    ids=set();titles=[]
    for path in (site/'editions').glob('*.json'):
        try:e=Edition.model_validate_json(path.read_bytes())
        except ValueError:continue
        days=(today-e.edition_date).days
        if 1<=days<=lookback:
            for a in e.articles:
                ids.add(a.id);titles.append(a.title)
                for ref in a.source_refs:
                    ids.add(sha256(ref.url.encode()).hexdigest()[:22])
    return ids,titles

def health(site,now,state,reason,feeds,usage):
    # No API credentials, request bodies or article evidence ever leave CI logs.
    atomic(site/'health.json',(json.dumps({'checked_at':now.isoformat(),
        'state':state,'reason':reason[:180],'feeds':feeds,'ai_usage':usage},ensure_ascii=False,indent=2)+'\n').encode())
