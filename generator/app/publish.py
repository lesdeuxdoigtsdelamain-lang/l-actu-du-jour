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
    atomic(site/path,raw)
    dates=[edition.edition_date.__class__.fromisoformat(f.stem) for f in (site/'editions').glob('*.json')]
    newest=max(dates)
    cutoff=edition.edition_date.__class__.fromordinal(newest.toordinal()-6)
    for old in (site/'editions').glob('*.json'):
        try: day=edition.edition_date.__class__.fromisoformat(old.stem)
        except ValueError: continue
        if day<cutoff:old.unlink()
    manifests=[]
    for current in sorted((site/'editions').glob('*.json'),reverse=True):
        data=current.read_bytes()
        e=Edition.model_validate_json(data)
        manifests.append(Manifest(date=e.edition_date,path=f'editions/{current.name}',
            sha256=sha256(data).hexdigest(),published_at=e.generated_at))
    # Separate optional index keeps the installed v1 latest contract unchanged.
    atomic(site/'archive.json',(json.dumps({'schema_version':1,
        'editions':[m.model_dump(mode='json') for m in manifests]},ensure_ascii=False,indent=2)+'\n').encode())
    man=manifests[0]
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
    if (site/'archive.json').exists():
        index=json.loads((site/'archive.json').read_text())
        entries=index['editions']
        if index['schema_version']!=1 or not 1<=len(entries)<=7:raise ValueError('Invalid archive index')
        dates=set()
        for entry in entries:
            m=Manifest.model_validate(entry)
            if m.date in dates or m.path!=f'editions/{m.date}.json':raise ValueError('Invalid archive path/date')
            dates.add(m.date)
            data=(site/m.path).read_bytes()
            if sha256(data).hexdigest()!=m.sha256:raise ValueError('Archive hash mismatch')
            if Edition.model_validate_json(data).edition_date!=m.date:raise ValueError('Archive date mismatch')

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
