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


def test_complete_pipeline_with_quality_gates(monkeypatch,tmp_path):
    from generator.app import main as runner
    from generator.app.ingest import Candidate
    feeds=[FEED]
    titles=['Une annonce économique concerne plusieurs collectivités et communes',
            'Des chercheurs étudient les mesures sur la planète Mars',
            'Les responsables examinent un nouveau dossier de santé',
            'La diplomatie prépare des échanges entre plusieurs pays']
    candidates=[Candidate(f'{i:022d}',title,'Synthèse initiale suffisamment complète pour servir d’exemple.',
        f'https://www.francetvinfo.fr/politique/dossier-{i}',NOW,FEED) for i,title in enumerate(titles)]
    def fake_collect(feeds,now):return candidates,{'franceinfo':{'ok':4}}
    def fake_facts(c,others,http):
        source={'id':c.id,'publisher':'Franceinfo','url':c.url,'language':'fr',
                'published_at':NOW.isoformat()}
        facts=[{'id':str(i),'text':('Une institution a partagé des informations détaillées. '*12)} for i in range(7)]
        return facts,[source]
    paragraph=' '.join(['Des données sont présentées par plusieurs acteurs et les décisions restent soumises à des vérifications indépendantes.']*7)
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
    assert edition is not None and len(edition.articles)==4 and edition.status=='partielle'
    assert all(a.word_count>=280 and a.image is None and len(a.source_refs)>=1 for a in edition.articles)
    put(tmp_path,edition,NOW)
    validate(tmp_path)
