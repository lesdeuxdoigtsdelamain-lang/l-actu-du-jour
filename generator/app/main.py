"""Non-interactive publisher; no article is fabricated to fill a daily quota."""
from datetime import datetime,timezone
from hashlib import sha256
from pydantic import ValidationError
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
from .sources import load
from .fetch import Fetcher
from .ingest import collect,cluster,rank
from .evidence import make_facts
from .providers import configured,AiError
from .editor import assemble,rejection_reason,reflow_paragraphs,allowed_numbers,unsupported_numbers
from .commons import find
from .models import Edition
from .publish import put,validate,history,health

ROOT=Path(__file__).resolve().parents[2]
LONDON=ZoneInfo('Europe/London')

def ai_usage(provider):
    if hasattr(provider,'usage_stats'):return provider.usage_stats()
    return {'calls':provider.calls,'neurons':round(provider.usage_neurons,2)}

TARGET_WORDS=3300
MIN_COMPLETE_WORDS=3060
MAX_ARTICLES=8
MIN_ARTICLES=6

def complete(articles):
    return len(articles)>=MIN_ARTICLES and sum(a.word_count for a in articles)>=MIN_COMPLETE_WORDS and sum(a.minutes for a in articles)>=18

def today_edition(site,day):
    path=site/'editions'/f'{day}.json'
    return Edition.model_validate_json(path.read_bytes()) if path.exists() else None

def generate(site,now,provider,feeds):
    day=now.astimezone(LONDON).date()
    existing=today_edition(site,day)
    articles=list(existing.articles) if existing else []
    previous_count=len(articles)
    candidates,status=collect(feeds,now)
    old_ids,old_titles=history(site,day)
    for a in articles:
        old_ids.add(a.id)
        old_titles.append(a.title)
        for src in a.source_refs:
            old_ids.add(sha256(src.url.encode()).hexdigest()[:22])
    grouped=cluster(candidates)
    grouped=[c for c in grouped if not any(x.id in old_ids for x in [c,*c.clustered])]
    ranked=rank(grouped,old_ids,old_titles,limit=65)
    print(f'Edition: {previous_count} articles déjà publiés, {len(ranked)} sujets candidats',flush=True)
    source_http=Fetcher(set(x for f in feeds for x in f.hosts))
    image_http=Fetcher({'commons.wikimedia.org'})
    cache_images={}
    errors=[]
    if not provider:return None,status,{'calls':0},'Aucun fournisseur IA configuré : pas de contenu inventé'
    for c in ranked:
        if len(articles)>=MAX_ARTICLES or (len(articles)>=MIN_ARTICLES and sum(a.word_count for a in articles)>=TARGET_WORDS and sum(a.minutes for a in articles)>=18) or provider.calls>=provider.max_calls:break
        facts,sources=make_facts(c,c.clustered,source_http)
        evidence_words=sum(len(f['text'].split()) for f in facts)
        # Headlines, two press excerpts, or an APOD caption are not a full report.
        if evidence_words<300 or len(facts)<5:continue
        # Input budget: 7.5k characters per story so Free Workers quota stays bounded.
        facts=facts[:17]
        while len(str(facts))>8500 and len(facts)>4:facts.pop()
        packet={'event_title':c.title,'language':'fr-FR',
                'evidence_words':evidence_words,'facts':facts,
                'source_names':[s['publisher'] for s in sources],
                'chiffres_autorises':sorted(allowed_numbers(facts,sources)),
                'objectif_editorial':'Viser 480 à 600 mots, avec contexte étayé et sans répétition. Omettre les chiffres non autorisés.'}
        article=None;inference_failure=False
        for attempt in range(2):
            if provider.calls>=provider.max_calls:break
            try:output=provider.run(packet)
            except AiError as e:
                errors.append(str(e));inference_failure=True;break
            try:article=assemble(output,c,facts,sources)
            except (ValidationError,ValueError,TypeError):article=None
            if article is not None:break
            repaired=reflow_paragraphs(output)
            reason=rejection_reason(repaired,facts,sources) or 'format ou preuves invalides'
            words=sum(len(p.split()) for p in repaired.get('paragraphes',[]) if isinstance(p,str))
            errors.append(f'Rejected: {reason} ({words} mots)')
            print(f'Article refusé : {reason} ({words} mots)',flush=True)
            if attempt==0:
                packet={**packet,'revision_notice':(
                    'Le premier essai a été refusé: '+reason+'. Réécris un nouvel article original '
                    'de 480 à 600 mots si les faits le permettent, en EXACTEMENT 6 paragraphes de 80 à 100 mots, '
                    'et EXACTEMENT 6 listes de preuves correspondantes, avec uniquement les '
                    'identifiants des faits reçus. Ne recopie aucun passage de 15 mots des sources. '
                    'Supprime les chiffres signalés comme non autorisés, sans les inventer en lettres. '
                    'Utilise uniquement les valeurs documentées.'),
                    'chiffres_refuses':sorted(unsupported_numbers(repaired,facts,sources))}
        if inference_failure:break
        if article is None:continue
        if article.category not in cache_images:
            cache_images[article.category]=find(article.category,image_http)
        image=cache_images[article.category]
        article=article.model_copy(update={'image':image})
        articles.append(article)
        print(f'Article accepté : {article.word_count} mots, rubrique {article.category}, fournisseur {getattr(provider,"last_provider",None) or "IA"}',flush=True)
    # Publish at least one genuinely verified long report, clearly marked partial.
    # Do not keep the reader empty merely because fewer than three pass strict QA.
    if not articles:
        return (None,status,ai_usage(provider),
            'Aucun article long vérifié; publication suspendue. '+('; '.join(errors[:2])))
    # Aim for 20 minutes, but never pad or misrepresent a shorter confirmed edition.
    status_label='complete' if complete(articles) else 'partielle'
    e=Edition(edition_id=f'{day}-fr-FR',edition_date=day,generated_at=now,
        status=status_label,minutes=sum(a.minutes for a in articles),articles=articles)
    explanation=('Édition partielle : '+str(len(articles))+' article(s) long(s) vérifié(s) sur 6 à 8 attendus. ' if status_label=='partielle' else 'Édition complète. ')
    return e,status,ai_usage(provider),explanation+'Contrôles : longueur, chiffres, références.'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'site')
    p.add_argument('--force',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    now=datetime.now(timezone.utc);day=now.astimezone(LONDON).date()
    validate(args.output)
    previous=today_edition(args.output,day)
    if previous and complete(previous.articles) and not args.force:
        print(f'Edition {day} already complete. No duplicates.');return 0
    if previous and len(previous.articles)>=MAX_ARTICLES:
        print(f'Edition {day} already has eight articles. No duplicate replacement.');return 0
    provider=configured();feeds=load(ROOT/'config/sources.json')
    edition,sources,usage,reason=generate(args.output,now,provider,feeds)
    if edition is None:
        print('No new edition: '+reason)
        if not args.dry_run:health(args.output,now,'unavailable',reason,sources,usage)
        return 2
    if previous and len(edition.articles)<=len(previous.articles):
        print('Aucun nouvel article accepté : édition précédente conservée.');return 0
    if not args.dry_run:
        put(args.output,edition,now)
        health(args.output,now,'success',reason,sources,usage)
        validate(args.output)
    print(f'{"DRY RUN" if args.dry_run else "PUBLISHED"}: {day} — {len(edition.articles)} full articles — {edition.minutes} min; AI {usage}')
    return 0

if __name__=='__main__':raise SystemExit(main())
