from datetime import datetime,timezone,timedelta,date
from hashlib import sha256
from pathlib import Path
import json
import httpx
import pytest
from pydantic import ValidationError
from generator.app.ingest import parse,when,plain,cluster,rank,same_event,content_tokens,Candidate
from generator.app.sources import Feed,load
from generator.app.fetch import Fetcher,FetchError
from generator.app.evidence import PageReader,make_facts
from generator.app.editor import verify,assemble
from generator.app.models import Article,Edition,Source,Manifest
from generator.app.publish import put,validate,history
from generator.app.cloudflare import Cloudflare,AiError,MODEL,SCHEMA

NOW=datetime(2026,11,17,18,0,tzinfo=timezone.utc)
FEED=Feed('franceinfo','Franceinfo','https://www.francetvinfo.fr/rss','fr','France',
          ('www.francetvinfo.fr',),'rss')
ROOT=Path(__file__).resolve().parents[2]


def rss(title='Le gouvernement annonce un plan de mesures',description='Le projet annoncé sera examiné mercredi par les partenaires concernés et discuté avec les organisations nationales.',link='https://www.francetvinfo.fr/politique/mesures',stamp='Tue, 17 Nov 2026 16:00:00 GMT'):
    return (f'<rss><channel><item><title>{title}</title><description>{description}</description>'
            f'<link>{link}</link><pubDate>{stamp}</pubDate></item></channel></rss>').encode()

def article(day=date(2026,11,17),i=1):
    # Synthetic fixture, never published as an actual news edition.
    paragraph=' '.join(['Le texte de test reste volontairement fictif et ne décrit aucun événement réel.']*6)
    ps=[paragraph]*6
    count=len(' '.join(ps).split())
    src=Source(publisher='Exemple fictif',url=f'https://www.francetvinfo.fr/exemple{i}',
               language='fr',published_at=NOW)
    return Article(id=f'aaaa{i:018d}',category='Monde',title=f'Annonce fictive pour vérifier un article de démonstration {i}',
                   paragraphs=ps,source_refs=[src],word_count=count,minutes=max(2,min(7,round(count/170))))

def edition(day=date(2026,11,17),size=6):
    a=[article(i=i+1) for i in range(size)]
    return Edition(edition_id=f'{day}-fr-FR',edition_date=day,generated_at=NOW,
                   status='complete' if size>=6 else 'partielle',articles=a,
                   minutes=sum(x.minutes for x in a))


def test_parse_french_english_arabic_titles_and_dates():
    french=parse(rss(),FEED,NOW)
    assert len(french)==1 and french[0].feed.language=='fr'
    ar=Feed('arabic','Média arabe','https://www.hespress.com/feed','ar','Maroc',('www.hespress.com',),'rss')
    story=rss(title='أخبار المغرب الاقتصادية ومستجدات اليوم',link='https://www.hespress.com/news-001',
              description='تتحدث تقارير اليوم عن المغرب وعن التجارة الدولية والتطورات الاقتصادية في المنطقة')
    assert len(parse(story,ar,NOW))==1
    assert not parse(rss(stamp='Fri, 13 Nov 2026 10:00:00 GMT'),FEED,NOW)


def test_html_and_ssrf_rejection():
    assert plain('<p>Bonjour <strong>le monde</strong> !</p>')=='Bonjour le monde !'
    assert parse(rss(link='https://evil.example.com/x'),FEED,NOW)==[]
    assert parse(rss(link='http://www.francetvinfo.fr/x'),FEED,NOW)==[]
    assert not parse(rss(title='Première exposition de mode et cinéma'),FEED,NOW)
    with pytest.raises(FetchError):
        parse(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>',FEED,NOW)


def test_fetch_rejects_redirect_to_new_host():
    fake=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/test'})))
    f=Fetcher({'www.francetvinfo.fr'},fake)
    with pytest.raises(FetchError):f.get('https://www.francetvinfo.fr/feed')


def test_robots_disallow_respected():
    fake=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'User-agent: *\nDisallow: /private\n')))
    f=Fetcher({'www.francetvinfo.fr'},fake)
    assert not f.can_read_article('https://www.francetvinfo.fr/private/secret')
    assert f.can_read_article('https://www.francetvinfo.fr/public/actualite')


def test_extractor_only_article_paragraphs():
    p=PageReader()
    p.feed('<html><nav><p>Menu de navigation avec plein de liens inutiles qui ne doivent pas être présents dans le texte.</p></nav>'
           '<article><p>Les scientifiques ont présenté plusieurs résultats nouveaux pendant une longue conférence, avec des détails sur la recherche menée au cours des dernières années.</p>'
           '<p>Les autorités doivent désormais examiner les résultats et informer les équipes concernées avant de décider de prochaines mesures.</p></article></html>')
    assert len(p.paragraphs)==2 and all('Menu de navigation' not in x for x in p.paragraphs)


def test_grouping_distinguishes_events():
    assert same_event('UK government announces new healthcare proposal','New healthcare proposal announced by UK government')
    assert not same_event('UK government announces healthcare proposal','Scientists discover a new galaxy')
    assert any('المغرب' in c for c in content_tokens('أخبار المغرب الاقتصادي'))


def test_article_quality_denies_half_line():
    facts=[{'id':'a','text':'Les chercheurs ont présenté plusieurs données qui seront étudiées pendant la semaine prochaine.'}]
    short={'titre':'Les chercheurs présentent un nouveau résultat scientifique',
           'paragraphes':['Une phrase trop courte.']*5,'preuves':[['a']]*5}
    assert verify(short,facts,[]) is False
    with pytest.raises(ValidationError):
        Article(id='a'*14,category='Monde',title='Une nouvelle fictive pour le test',
                paragraphs=['Aucun vrai contenu.']*4,source_refs=[article().source_refs[0]],
                word_count=12,minutes=2)


def test_numbers_must_exist_in_source():
    facts=[{'id':'a','text':'Le montant annoncé est de 12 millions de dollars et le calendrier débute en novembre.'}]
    p=' '.join(['Des données publiques sont discutées pour suivre les décisions et leurs conséquences concrètes.']*6)
    ps=[p]*6
    body={'titre':'Une annonce de dépenses concerne des services publics',
          'paragraphes':ps,'preuves':[['a']]*6}
    assert verify(body,facts,[])
    body['paragraphes'][0] += ' Le projet coûte 999 millions.'
    assert not verify(body,facts,[])


def test_publish_integrity_and_archive(tmp_path):
    e=edition()
    manifest=put(tmp_path,e,NOW)
    validate(tmp_path)
    assert len(history(tmp_path,date(2026,11,18))[0])>=6
    assert len(history(tmp_path,date(2026,11,17))[0])==0
    assert manifest.sha256==sha256((tmp_path/manifest.path).read_bytes()).hexdigest()
    (tmp_path/manifest.path).write_text('corrupt')
    with pytest.raises((ValueError,ValidationError)):validate(tmp_path)


def test_archive_retains_seven_days(tmp_path):
    previous=edition(day=date(2026,11,9))
    put(tmp_path,previous,NOW)
    newer=edition(day=date(2026,11,17))
    put(tmp_path,newer,NOW)
    assert not (tmp_path/'editions/2026-11-09.json').exists()
    assert (tmp_path/'editions/2026-11-17.json').exists()


def test_no_fabricated_live_sample_in_repository():
    assert not list((ROOT/'site/editions').glob('*.json'))
    assert not (ROOT/'site/latest.json').exists()


def test_pydantic_json_schema_is_current():
    assert json.loads((ROOT/'schema/edition.schema.json').read_text())==Edition.model_json_schema()


def test_cloudflare_response_and_usage_without_logging_token():
    phrase='Un texte de synthèse rédigé en français pour fournir au lecteur des éléments de contexte vérifiés.'
    data={'titre':'Les autorités détaillent leur programme économique actuel',
          'paragraphes':[phrase]*6,'preuves':[['p1']]*6}
    def respond(req):
        assert req.headers['authorization']=='Bearer PRIVATE_KEY_DO_NOT_LOG'
        assert '/ai/run/'+MODEL in str(req.url)
        packet=json.loads(req.content)
        assert packet['response_format']['json_schema']==SCHEMA
        return httpx.Response(200,json={'success':True,'result':{'response':data,'usage':{'neurons':26.5}}})
    cf=Cloudflare('a'*32,'PRIVATE_KEY_DO_NOT_LOG',client=httpx.Client(transport=httpx.MockTransport(respond)))
    assert cf.run({'facts':[]})==data
    assert cf.calls==1 and cf.usage_neurons==26.5


def test_cloudflare_quota_error_does_not_expose_secret():
    fake=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429,content=b'token PRIVATE_KEY_DO_NOT_LOG')))
    cf=Cloudflare('a'*32,'PRIVATE_KEY_DO_NOT_LOG',client=fake)
    with pytest.raises(AiError) as exc:cf.run({})
    assert 'PRIVATE_KEY' not in str(exc.value)


def test_enabled_feed_sources_broad():
    sources=load(ROOT/'config/sources.json')
    languages={f.language for f in sources}
    assert languages=={'fr','en','ar'}
    assert any(f.publisher=='Al Jazeera Arabic' for f in sources)
    assert any(f.publisher=='Hespress' for f in sources)
    assert any(f.publisher=='Al Arabiya' for f in sources)
    assert all(f.url.startswith('https://') for f in sources)


def test_verified_commons_photo_and_no_unlicensed_photo():
    from generator.app.commons import find
    image_json={'query':{'pages':[{'title':'File:Rabat panorama.jpg','imageinfo':[{
        'mime':'image/jpeg',
        'thumburl':'https://upload.wikimedia.org/wikipedia/commons/thumb/Rabat.jpg',
        'extmetadata':{'LicenseShortName':{'value':'CC BY-SA 4.0'},
                       'Artist':{'value':'<b>Photographe Exemple</b>'}}}]}]}}
    client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=image_json)))
    img=find('Maroc',Fetcher({'commons.wikimedia.org'},client))
    assert img is not None and img.credit=='Photographe Exemple' and img.license=='CC BY-SA 4.0'
    image_json['query']['pages'][0]['imageinfo'][0]['extmetadata']['LicenseShortName']['value']='all rights reserved'
    assert find('Maroc',Fetcher({'commons.wikimedia.org'},client)) is None


def test_complete_pipeline_rejects_generated_duplicates(monkeypatch,tmp_path):
    from generator.app import main as runner
    from generator.app.ingest import Candidate
    from dataclasses import replace
    global_feed=replace(FEED,category='Monde')
    feeds=[global_feed]
    titles=['Une annonce économique concerne plusieurs collectivités et communes',
            'Des chercheurs étudient les mesures sur la planète Mars',
            'Les responsables examinent un nouveau dossier de santé',
            'La diplomatie prépare des échanges entre plusieurs pays']
    candidates=[Candidate(f'{i:022d}',title,'Synthèse initiale suffisamment complète pour servir d’exemple.',
        f'https://www.francetvinfo.fr/politique/dossier-{i}',NOW,global_feed) for i,title in enumerate(titles)]
    def fake_collect(feeds,now):return candidates,{'franceinfo':{'ok':4}}
    def fake_facts(c,others,http):
        source={'id':c.id,'publisher':'Franceinfo','url':c.url,'language':'fr',
                'published_at':NOW.isoformat()}
        facts=[{'id':str(i),'text':('Une institution a partagé des informations détaillées. '*12)} for i in range(7)]
        return facts,[source]
    paragraph=' '.join(['Des données sont présentées par plusieurs acteurs et les décisions restent soumises à des vérifications indépendantes.']*6)
    class Provider:
        def __init__(self):self.calls=0;self.max_calls=8;self.usage_neurons=0
        def run(self,packet):
            self.calls+=1
            return {'titre':'Une analyse détaillée des principales décisions et de leurs conséquences',
                    'paragraphes':[paragraph]*6,
                    'preuves':[['0','1']]*6}
    monkeypatch.setattr(runner,'collect',fake_collect)
    monkeypatch.setattr(runner,'make_facts',fake_facts)
    monkeypatch.setattr(runner,'find',lambda cat,http:None)
    p=Provider()
    edition,status,usage,why=runner.generate(tmp_path,NOW,p,feeds)
    assert edition is not None and len(edition.articles)==1 and edition.status=='partielle'
    assert all(a.word_count>=280 and a.image is None and len(a.source_refs)>=1 for a in edition.articles)
    put(tmp_path,edition,NOW)
    validate(tmp_path)


def test_ai_short_paragraphs_reflow_preserves_every_word_and_fact_reference():
    from generator.app.editor import reflow_paragraphs
    fragments=[' '.join(['Les responsables ont expliqué les mesures et les conséquences attendues.']*2)]*24
    data={'titre':'Des responsables présentent une annonce nouvelle et détaillée',
          'paragraphes':fragments,
          'preuves':[[f'fact-{i}'] for i in range(len(fragments))]}
    repaired=reflow_paragraphs(data)
    assert repaired is not data
    assert 4<=len(repaired['paragraphes'])<=11
    assert all(22<=len(p.split())<=160 for p in repaired['paragraphes'])
    assert ' '.join(repaired['paragraphes']).split() == ' '.join(fragments).split()
    assert set(x for refs in repaired['preuves'] for x in refs)=={f'fact-{i}' for i in range(24)}


def test_ai_insufficient_total_words_still_rejected_after_reflow():
    from generator.app.editor import reflow_paragraphs
    data={'titre':'Un sujet de test trop court','paragraphes':['Le sujet est très court.']*6,
          'preuves':[['fact-1']]*6}
    assert reflow_paragraphs(data)==data
    assert not verify(data,[{'id':'fact-1','text':'Le sujet est très court.'}],[])


def test_edition_length_target():
    from types import SimpleNamespace
    from generator.app.main import complete
    short=[SimpleNamespace(word_count=372,minutes=2)]*5
    assert not complete(short)
    assert not complete([SimpleNamespace(word_count=350,minutes=2)]*8)
    long=SimpleNamespace(word_count=500,minutes=3,title='Une annonce générale développée',paragraphs=[])
    uk=SimpleNamespace(word_count=500,minutes=3,title='Le Royaume-Uni annonce une décision publique',paragraphs=[])
    fr=SimpleNamespace(word_count=350,minutes=2,title='Tour de l’actu en France',paragraphs=[])
    assert not complete([long]*7)
    assert complete([long]*3+[uk,fr])
    assert complete([long]*4+[uk,fr])
    assert not complete([long]*5+[uk,fr])

def test_numeric_revision_reports_only_unsupported_values():
    from generator.app.editor import unsupported_numbers,allowed_numbers
    facts=[{'id':'a','text':'Le montant annoncé est de 12 millions.'}]
    output={'titre':'Une annonce économique','paragraphes':['Le montant est de 12 millions, et pas 777 millions.']}
    assert allowed_numbers(facts,[])=={'12'}
    assert unsupported_numbers(output,facts,[])=={'777'}

def test_legacy_partial_edition_reserves_required_slots_under_the_six_article_cap(monkeypatch,tmp_path):
    from generator.app import main as runner
    from dataclasses import replace
    global_feed=replace(FEED,category='Monde')
    previous=edition(size=5)
    # Distinct synthetic reports: identical boilerplate is now correctly deduplicated.
    phrases=[
      'Astronomes télescopes galaxies observations cosmiques poussières orbitales étoiles lointaines.',
      'Banques crédits entreprises financement investissement épargne dépôts prêts garanties financières.',
      'Forêts arbres biodiversité oiseaux plantes habitats humides graines réserves naturelles.',
      'Volcans lave magma géologues cratères roches sismiques tremblements tectoniques souterrains.',
      'Navires ports marchandises cargaisons transport maritime conteneurs fret douanes commerciales.']
    updated=[]
    for a,phrase in zip(previous.articles,phrases):
        paragraphs=[' '.join([phrase]*9)]*6
        wc=len(' '.join(paragraphs).split())
        updated.append(a.model_copy(update={'title':'Dossier fictif : '+phrase,
            'paragraphs':paragraphs,'word_count':wc,'minutes':max(2,min(7,round(wc/170)))}))
    previous=previous.model_copy(update={'articles':updated,'minutes':sum(a.minutes for a in updated)})
    put(tmp_path,previous,NOW)
    titles=['Une annonce municipale concerne les transports des habitants',
            'Des chercheurs étudient une nouvelle méthode scientifique',
            'La diplomatie prépare un accord avec plusieurs États']
    candidates=[Candidate(f'{i+20:022d}',title,'Exemple de description pour un test local.',
                   f'https://www.francetvinfo.fr/nouveautes/{i}',NOW,global_feed) for i,title in enumerate(titles)]
    monkeypatch.setattr(runner,'collect',lambda feeds,now:(candidates,{}))
    def facts(c,others,http):
        return ([{'id':str(i),'text':'Une institution a partagé des informations détaillées. '*12} for i in range(7)],
                [{'id':c.id,'publisher':'Franceinfo','url':c.url,'language':'fr','published_at':NOW.isoformat()}])
    monkeypatch.setattr(runner,'make_facts',facts)
    monkeypatch.setattr(runner,'find',lambda cat,http:None)
    class Provider:
        max_calls=18
        calls=0
        usage_neurons=0
        def run(self,packet):
            self.calls+=1
            return {'titre':packet['event_title'],
                    'paragraphes':['Des données sont présentées par plusieurs acteurs et les décisions restent soumises à des vérifications indépendantes. '*6]*6,
                    'preuves':[['0','1']]*6}
    result,_,_,_=runner.generate(tmp_path,NOW,Provider(),[global_feed])
    assert result is not None
    assert result.status=='partielle'
    assert [a.id for a in result.articles]==[a.id for a in previous.articles[:4]]
    assert len(result.articles)+runner.slots_missing(result.articles)<=6


def test_five_article_wire_contract_keeps_legacy_archives_readable():
    assert Edition.model_validate(edition(size=5).model_copy(update={'status':'complete'}).model_dump()).status=='complete'
    assert Edition.model_validate(edition(size=8).model_dump()).status=='complete'
    with pytest.raises(ValidationError):
        Edition.model_validate(edition(size=4).model_copy(update={'status':'complete'}).model_dump())


def test_english_people_is_not_an_entertainment_keyword():
    assert parse(rss(title='UK agreement creates economic opportunities for young people'),FEED,NOW)
    assert not parse(rss(title='Le Maroc entre dans le Top 5 du classement FIFA'),FEED,NOW)
