"""Non-interactive publisher; no article is fabricated to fill a daily quota."""
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
from .sources import load
from .fetch import Fetcher
from .ingest import collect,cluster,rank
from .evidence import make_facts
from .cloudflare import configured,AiError
from .editor import assemble
from .commons import find
from .models import Edition
from .publish import put,validate,history,health

ROOT=Path(__file__).resolve().parents[2]
LONDON=ZoneInfo('Europe/London')

def generate(site,now,provider,feeds):
    day=now.astimezone(LONDON).date()
    candidates,status=collect(feeds,now)
    old_ids,old_titles=history(site,day)
    ranked=rank(cluster(candidates),old_ids,old_titles,limit=40)
    source_http=Fetcher(set(x for f in feeds for x in f.hosts))
    image_http=Fetcher({'commons.wikimedia.org'})
    cache_images={}
    articles=[];errors=[]
    if not provider:return None,status,{'calls':0},'Cloudflare not configured: no synthetic news'
    for c in ranked:
        if len(articles)==8 or provider.calls>=provider.max_calls:break
        facts,sources=make_facts(c,c.clustered,source_http)
        evidence_words=sum(len(f['text'].split()) for f in facts)
        # Headlines, two press excerpts, or an APOD caption are not a full report.
        if evidence_words<210 or len(facts)<4:continue
        # Input budget: 7.5k characters per story so Free Workers quota stays bounded.
        facts=facts[:17]
        while len(str(facts))>8500 and len(facts)>4:facts.pop()
        packet={'event_title':c.title,'language':'fr-FR',
                'evidence_words':evidence_words,'facts':facts,
                'source_names':[s['publisher'] for s in sources]}
        try:output=provider.run(packet)
        except AiError as e:
            errors.append(str(e));break
        article=assemble(output,c,facts,sources)
        if article is None:errors.append('Rejected: weak or unsupported article');continue
        if article.category not in cache_images:
            cache_images[article.category]=find(article.category,image_http)
        image=cache_images[article.category]
        article=article.model_copy(update={'image':image})
        articles.append(article)
    if len(articles)<3:
        return (None,status,{'calls':provider.calls,'neurons':round(provider.usage_neurons,2)},
            f'Only {len(articles)} verified long articles; not publishing a poor edition. '+('; '.join(errors[:2])))
    # Aim for 20 minutes, but never pad or misrepresent a shorter confirmed edition.
    status_label='complete' if len(articles)>=6 and sum(a.word_count for a in articles)>=2700 else 'partielle'
    e=Edition(edition_id=f'{day}-fr-FR',edition_date=day,generated_at=now,
        status=status_label,minutes=sum(a.minutes for a in articles),articles=articles)
    return e,status,{'calls':provider.calls,'neurons':round(provider.usage_neurons,2)},'Articles contrôlés automatiquement (longueur, chiffres, références)'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'site')
    p.add_argument('--force',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    now=datetime.now(timezone.utc);day=now.astimezone(LONDON).date()
    validate(args.output)
    if (args.output/'editions'/f'{day}.json').exists() and not args.force:
        print(f'Edition {day} already published. No duplicates.');return 0
    provider=configured();feeds=load(ROOT/'config/sources.json')
    edition,sources,usage,reason=generate(args.output,now,provider,feeds)
    if edition is None:
        print('No new edition: '+reason)
        if not args.dry_run:health(args.output,now,'unavailable',reason,sources,usage)
        return 2
    if not args.dry_run:
        put(args.output,edition,now)
        health(args.output,now,'success',reason,sources,usage)
        validate(args.output)
    print(f'{"DRY RUN" if args.dry_run else "PUBLISHED"}: {day} — {len(edition.articles)} full articles — {edition.minutes} min; AI {usage}')
    return 0

if __name__=='__main__':raise SystemExit(main())
