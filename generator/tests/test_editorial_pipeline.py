"""Editorial contract regressions with synthetic articles and no external calls."""
from dataclasses import replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import sys

import pytest

from generator.app import main as runner
from generator.app.briefing import is_roundup
from generator.app.diversity import article_region, is_morocco_article
from generator.app.ingest import Candidate
from generator.app.models import Article, Edition, Source
from generator.app.publish import put, validate
from generator.app.sources import Feed


NOW=datetime(2026,11,20,21,0,tzinfo=timezone.utc)
TODAY=NOW.date()
FEED=Feed('test','Source fictive','https://www.rfi.fr/fr/rss','fr','Monde',('www.rfi.fr',),'rss')


def words(key,count):
    # Separate synthetic vocabularies prevent accidental lexical duplicates.
    return [key+chr(97+(i//26)%26)+chr(97+i%26) for i in range(count)]


def article(key,title=None,category='Monde',count=500,day=TODAY,paragraph_count=6):
    title=title or f'Des observations détaillées concernent le dossier {key}'
    body=title.split()+words(key,count-len(title.split()))
    paragraphs=[]
    for i in range(paragraph_count):
        left=i*count//paragraph_count
        right=(i+1)*count//paragraph_count
        paragraphs.append(' '.join(body[left:right]))
    return Article(id=f'fixture-{key}-article',category=category,title=title,paragraphs=paragraphs,
                   source_refs=[Source(publisher='Source fictive',url=f'https://www.rfi.fr/fr/test/{key}',
                                       language='fr',published_at=datetime.combine(day,NOW.timetz()))],
                   word_count=count,minutes=max(2,min(7,round(count/170))))


def france(key='francebrief',day=TODAY,count=350):
    return article(key,'Tour de l’actu en France — actualités du jour','France',count,day,4)


def uk(key='britishnews',day=TODAY):
    return article(key,'Le Royaume-Uni annonce un accord pour les échanges commerciaux','Royaume-Uni',day=day)


def edition(articles,day=TODAY):
    return Edition(edition_id=f'{day}-fr-FR',edition_date=day,generated_at=NOW,
                   status='complete' if len(articles)>=6 else 'partielle',articles=articles,
                   minutes=sum(a.minutes for a in articles))


def candidate(key,title,category='Monde'):
    return Candidate(f'candidate-{key}-news',title,'Une description technique fictive sans aucune actualité réelle.',
                     f'https://www.rfi.fr/fr/test/candidate-{key}',NOW,replace(FEED,category=category))


def facts_for(c,*_):
    facts=[{'id':f'{c.id}:{i}','source_id':c.id,'text':' '.join(words('evidence'+chr(97+i),80))}
           for i in range(6)]
    sources=[{'id':c.id,'publisher':FEED.publisher,'url':c.url,'language':'fr','published_at':NOW.isoformat()}]
    return facts,sources


class Provider:
    max_calls=18
    usage_neurons=0
    def __init__(self,outputs=()):self.calls=0;self.outputs=list(outputs)
    def run(self,packet):
        self.calls+=1
        return self.outputs.pop(0)


def complete_articles(prefix='',day=TODAY):
    return [france(prefix+'brief',day),uk(prefix+'british',day),
            *[article(prefix+key,day=day) for key in ('asteroid','energy','forest','water','education')]]


def freeze_now(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(runner,'datetime',FixedDateTime)


def test_repair_removes_same_day_and_previous_day_repetitions_and_hidden_morocco():
    earlier=article('oldstory','Le classement de cette université progresse dans une étude internationale')
    repeated=earlier.model_copy(update={'id':'fixture-repeated-prior',
        'source_refs':[earlier.source_refs[0].model_copy(update={'url':'https://www.rfi.fr/fr/test/another-source'})]})
    first=article('distinct','Des astronomes détectent des pulsars dans une autre galaxie')
    repeated_today=first.model_copy(update={'id':'fixture-repeated-today',
        'source_refs':[first.source_refs[0].model_copy(update={'url':'https://www.rfi.fr/fr/test/other-report'})]})
    morocco=article('hospital','Laâyoune : les travaux du nouvel hôpital avancent','Santé')
    hidden=article('troops','Le Maroc propose une participation à la mission de Gaza','Monde musulman')
    existing=edition([morocco,hidden,repeated,first,repeated_today,uk(),france(),article('volcano')])
    kept,reasons=runner.seed_articles(existing,[earlier])
    ids={a.id for a in kept}
    assert morocco.id in ids and first.id in ids
    assert not ids & {hidden.id,repeated.id,repeated_today.id}
    assert sum(is_morocco_article(a) for a in kept)==1
    assert next(a for a in kept if a.id==morocco.id).category=='Maroc'
    assert any('déjà traité' in reason for reason in reasons)


def test_edition_can_be_complete_without_any_morocco_article():
    articles=complete_articles()
    assert runner.complete(articles)
    assert sum(is_morocco_article(a) for a in articles)==0
    assert len(articles)==7 and sum(a.minutes for a in articles)==20


def test_full_old_edition_reserves_and_fills_france_and_uk_slots(monkeypatch,tmp_path):
    old=[article('older'+chr(97+i),count=450) for i in range(8)]
    put(tmp_path,edition(old),NOW)
    uk_candidate=candidate('british','Le Royaume-Uni présente un accord commercial','Royaume-Uni')
    round_candidate=candidate('french','Tour de l’actu en France — actualités du jour','France')
    monkeypatch.setattr(runner,'collect',lambda feeds,now:([uk_candidate],{}))
    monkeypatch.setattr(runner,'make_facts',facts_for)
    monkeypatch.setattr(runner,'find',lambda *args:None)
    monkeypatch.setattr(runner,'prepare_roundup',lambda *args,**kwargs:(round_candidate,[],[],[]))
    def draft(c,facts,sources,provider,seen,errors,**kwargs):
        provider.calls+=1
        return (france() if kwargs.get('mode')=='tour_france' else uk()),[]
    monkeypatch.setattr(runner,'draft',draft)
    result,_,_,_=runner.generate(tmp_path,NOW,Provider(),[FEED])
    assert result is not None and len(result.articles)<=8
    assert sum(is_roundup(a) for a in result.articles)==1
    assert sum(article_region(a)=='Royaume-Uni' for a in result.articles)==1
    assert len({a.id for a in result.articles})==len(result.articles)
    assert {a.id for a in result.articles} & {a.id for a in old}


def test_generated_morocco_story_cannot_bypass_cap_with_foreign_rss_category(monkeypatch,tmp_path):
    morocco=article('moroccoseed','Laâyoune : les travaux du nouvel hôpital avancent','Santé')
    put(tmp_path,edition([france(),uk(),morocco,article('seedastronomy')]),NOW)
    candidates=[candidate('hidden','Une réforme du financement sanitaire est présentée'),
                candidate('space','Des satellites observent la surface de Vénus'),
                candidate('agreement','Un accord diplomatique facilite les échanges régionaux'),
                candidate('birds','Des chercheurs étudient les migrations des oiseaux')]
    generated={candidates[0].id:article('hiddenmorocco','Dakhla : le ministre présente la réforme des soins','Santé'),
               candidates[1].id:article('distinctspace'),candidates[2].id:article('distinctagreement'),
               candidates[3].id:article('distinctbirds')}
    monkeypatch.setattr(runner,'collect',lambda feeds,now:(candidates,{}))
    monkeypatch.setattr(runner,'make_facts',facts_for)
    monkeypatch.setattr(runner,'find',lambda *args:None)
    def draft(c,facts,sources,provider,seen,errors,**kwargs):
        provider.calls+=1
        return generated[c.id],[]
    monkeypatch.setattr(runner,'draft',draft)
    result,_,_,_=runner.generate(tmp_path,NOW,Provider(),[FEED])
    assert result is not None
    assert sum(is_morocco_article(a) for a in result.articles)==1
    assert generated[candidates[0].id].id not in {a.id for a in result.articles}
    assert len(result.articles)==7


def roundup_inputs(count):
    c=candidate('roundup','Tour de l’actu en France — actualités du jour','France')
    facts=[];sources=[];topics=[]
    for i in range(4):
        source_id=f'topic-{i}'
        fact_id=f'{source_id}:0'
        facts.append({'id':fact_id,'source_id':source_id,'text':' '.join(words('source'+chr(97+i),80))})
        sources.append({'id':source_id,'publisher':'Source fictive','url':f'https://www.rfi.fr/fr/topic/{i}',
                        'language':'fr','published_at':NOW.isoformat()})
        topics.append({'id':source_id,'title':f'Sujet fictif {i}','fact_ids':[fact_id],'source_ids':[source_id]})
    output_article=france(count=count)
    output={'titre':c.title,'paragraphes':output_article.paragraphs,
            'preuves':[[f'topic-{i}:0'] for i in range(4)]}
    return c,facts,sources,topics,output


def test_france_roundup_over_400_words_is_revised_and_never_published_oversized():
    c,facts,sources,topics,too_long=roundup_inputs(401)
    _,_,_,_,corrected=roundup_inputs(400)
    provider=Provider([too_long,corrected])
    result,records=runner.draft(c,facts,sources,provider,[],[],mode='tour_france',topics=topics)
    assert provider.calls==2
    assert result is not None and result.word_count==400 and len(records)==4


@pytest.mark.parametrize('count',[299,401])
def test_france_roundup_outside_real_word_limits_is_rejected_after_bounded_retries(count):
    c,facts,sources,topics,output=roundup_inputs(count)
    provider=Provider([output,output])
    result,records=runner.draft(c,facts,sources,provider,[],[],mode='tour_france',topics=topics)
    assert result is None and records==[] and provider.calls==2


def test_new_source_url_does_not_make_repeated_generated_content_new():
    previous=article('repeated','Des astronomes détectent des pulsars dans une autre galaxie')
    c=candidate('newurl','Une nouvelle source décrit une observation scientifique')
    facts,sources=facts_for(c)
    output={'titre':previous.title,'paragraphes':previous.paragraphs,'preuves':[[facts[0]['id']]]*6}
    provider=Provider([output,output])
    errors=[]
    result,_=runner.draft(c,facts,sources,provider,[previous],errors)
    assert result is None and provider.calls==2
    assert any('déjà traité' in message for message in errors)


def test_repairing_older_day_updates_archive_without_rolling_latest_back(tmp_path):
    yesterday=date(2026,11,19)
    put(tmp_path,edition([article('nineteen',day=yesterday)],yesterday),NOW)
    put(tmp_path,edition([article('twenty')]),NOW)
    latest_before=(tmp_path/'latest.json').read_bytes()
    old_hash=sha256((tmp_path/'editions/2026-11-19.json').read_bytes()).hexdigest()
    correction=edition([article('correctednineteen',day=yesterday)],yesterday)
    put(tmp_path,correction,NOW)
    assert (tmp_path/'latest.json').read_bytes()==latest_before
    assert json.loads(latest_before)['date']=='2026-11-20'
    entries={m['date']:m for m in json.loads((tmp_path/'archive.json').read_text())['editions']}
    new_raw=(tmp_path/'editions/2026-11-19.json').read_bytes()
    assert entries['2026-11-19']['sha256']==sha256(new_raw).hexdigest()!=old_hash
    validate(tmp_path)


def test_two_date_repair_publishes_each_correction_and_keeps_newest_manifest(monkeypatch,tmp_path):
    yesterday=date(2026,11,19)
    for day,key in ((yesterday,'oldnineteen'),(TODAY,'oldtwenty')):
        put(tmp_path,edition([article(key,day=day)],day),NOW)
    calls=[]
    def generate(site,now,provider,feeds,*,target_day,memory_sink):
        calls.append(target_day)
        memory_sink[str(target_day)]=[]
        prefix='nineteen' if target_day==yesterday else 'twenty'
        return edition(complete_articles(prefix,target_day),target_day),{}, {'calls':0},'Corrected fixture'
    freeze_now(monkeypatch)
    monkeypatch.setattr(sys,'argv',['journal','--output',str(tmp_path),'--repair-dates','2026-11-19,2026-11-20'])
    monkeypatch.setattr(runner,'generate',generate)
    monkeypatch.setattr(runner,'configured',lambda:Provider())
    monkeypatch.setattr(runner,'load',lambda path:[FEED])
    assert runner.main()==0
    assert calls==[yesterday,TODAY]
    assert json.loads((tmp_path/'latest.json').read_text())['date']=='2026-11-20'
    assert all(len(Edition.model_validate_json(p.read_bytes()).articles)==7
               for p in (tmp_path/'editions').glob('*.json'))
    validate(tmp_path)


def test_complete_edition_rerun_is_idempotent_and_does_not_call_ai(monkeypatch,tmp_path):
    put(tmp_path,edition(complete_articles()),NOW)
    before={p.name:p.read_bytes() for p in [tmp_path/'latest.json',tmp_path/'archive.json',
                                          tmp_path/'editions/2026-11-20.json']}
    freeze_now(monkeypatch)
    monkeypatch.setattr(sys,'argv',['journal','--output',str(tmp_path)])
    monkeypatch.setattr(runner,'load',lambda path:[FEED])
    monkeypatch.setattr(runner,'configured',lambda:pytest.fail('A conforming edition must not consume another AI call'))
    monkeypatch.setattr(runner,'generate',lambda *args,**kwargs:pytest.fail('A conforming edition must remain unchanged'))
    assert runner.main()==0
    after={p.name:p.read_bytes() for p in [tmp_path/'latest.json',tmp_path/'archive.json',
                                         tmp_path/'editions/2026-11-20.json']}
    assert after==before
