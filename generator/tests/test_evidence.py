"""Evidence extraction regressions; synthetic HTML, no live requests or API keys."""
from datetime import datetime, timezone

from generator.app.evidence import PageReader, evidence, evidence_blocks, make_facts
from generator.app.ingest import Candidate
from generator.app.sources import Feed


NOW=datetime(2026,11,20,18,0,tzinfo=timezone.utc)
FEED=Feed('test','Source de test','https://www.gov.uk/news.atom','en','Royaume-Uni',
          ('www.gov.uk',),'rss')


def candidate(number=1,description=''):
    return Candidate(str(number).zfill(22),'A synthetic article for an extraction regression',
                     description,f'https://www.gov.uk/news/test-{number}',NOW,FEED)


class HTTP:
    def __init__(self,html,allowed=True):
        self.html=html
        self.allowed=allowed
        self.requested=[]
    def can_read_article(self,url):return self.allowed
    def get(self,url,limit):
        assert self.allowed, 'A robots denial must never cause a page request'
        self.requested.append(url)
        return self.html.encode()


def extract(html):
    reader=PageReader()
    reader.feed(html)
    reader.close()
    return reader.paragraphs


def test_news_about_subscriptions_advertising_or_comments_is_not_filtered_by_keywords():
    paragraphs=[
        'The regulator examined advertising practices and subscription prices during its public meeting.',
        'Les parlementaires discutent des commentaires publiés sur les réseaux et de leur modération.',
        'Les chercheurs ont recueilli des avis sur la newsletter scientifique et analysent les résultats.',
    ]
    html='<article class="political-comment">'+''.join(f'<p>{p}</p>' for p in paragraphs)+'</article>'
    assert extract(html)==paragraphs


def test_nested_ui_containers_captions_and_moderation_boilerplate_are_not_evidence():
    first='Les scientifiques présentent des résultats détaillés pour expliquer leur nouvelle méthode de recherche.'
    last='Les résultats seront examinés par une autre équipe avant leur publication définitive.'
    html=(f'<main><article><p>{first}</p>'
          '<section id="articleComments"><div><p>Ce commentaire personnel ne constitue pas une source pour cet article.</p></div></section>'
          '<div class="article-share"><p>Share this article with your friends and subscribe to our website today.</p></div>'
          '<figure><figcaption><p>Les responsables posent ensemble devant les photographes pendant une cérémonie.</p></figcaption></figure>'
          '<div role="complementary"><p>Un autre article recommandé présente des informations sans lien avec ce sujet.</p></div>'
          '<div hidden><p>Ce contenu masqué ne doit pas entrer dans les éléments de preuve.</p></div>'
          '<p>Les commentaires doivent respecter les règles du site et ne pas contenir de propos diffamatoires.</p>'
          '<p>Comments are moderated and must follow the community rules before publication on this website.</p>'
          '<p>التعليقات لا تعبر عن رأي الموقع ويجب احترام قواعد النشر وعدم الإساءة إلى الآخرين.</p>'
          f'<p>{last}</p></article><footer><p>Texte du pied de page sans rapport avec cette information.</p></footer></main>')
    assert extract(html)==[first,last]


def test_void_tags_and_omitted_paragraph_end_tags_do_not_hide_following_body():
    html=('<article><p>Une première phrase factuelle contient suffisamment de mots pour être conservée.'
          '<img src="image.jpg"><br><span>Un complément appartient à cette même information.</span>'
          '<p>Cette seconde phrase doit être séparée correctement malgré une balise de fermeture omise.'
          '<div aria-hidden><p>Une valeur aria vide ne doit pas provoquer une erreur du parseur.</p></div></article>')
    blocks=extract(html)
    assert len(blocks)==3
    assert 'Un complément' in blocks[0]
    assert blocks[1].startswith('Cette seconde')


def test_related_article_cards_and_source_promotions_cannot_pad_a_short_news_video():
    lead='Cette courte vidéo présente uniquement une annonce factuelle détaillée dans cette introduction.'
    unrelated='Cette autre actualité apparaît seulement dans une carte recommandée et ne documente pas le sujet principal.'
    wrappers=('card-article-container', 'shuffle-header__next', 'preferred-source',
              'generic-vertical-rebound', 'carousel shuffle-videos', 'card-shuffle--white')
    html=f'<main><article class="main-article"><p>{lead}</p></article>'
    for css in wrappers:
        html+=f'<div class="{css}"><article><p>{unrelated}</p></article></div>'
    html+='</main>'
    assert extract(html)==[lead]


def test_bbc_related_links_and_social_promotion_are_separate_from_article_evidence():
    paragraph='The BBC investigation describes the findings and includes public comments about the evidence.'
    html=(f'<article><p>{paragraph}</p><ul>'
          '<li class="ssrcss-anyhash-LinkItem anotherhash"><p class="ssrcss-anyhash-LinkHeadline anotherhash">'
          'A completely unrelated recommended headline must not become a fact in this story.</p></li></ul>'
          '<p><i>Go to BBCAfrica.com for more news from the African continent. Follow us on Twitter for updates.</i></p>'
          '<p>Go to BBCAfrica.com , external for more news from the African continent.</p>'
          '<p>Follow us on Facebook and Instagram for news and all the latest updates.</p></article>')
    assert extract(html)==[paragraph]


def test_many_short_body_paragraphs_retain_full_evidence_not_just_first_nine():
    paragraphs=[f'Paragraphe {i}: '+ ' '.join(f'fait{i}-{j}' for j in range(18))+'.' for i in range(40)]
    http=HTTP('<article>'+''.join(f'<p>{p}</p>' for p in paragraphs)+'</article>')
    story=candidate(description=paragraphs[0])
    facts,sources=make_facts(story,[],http)
    words=' '.join(f['text'] for f in facts).split()
    assert words==' '.join(paragraphs).split()  # RSS lead appears exactly once.
    assert len(words)==800 and len(words)>300
    assert all(60<=len(f['text'].split())<=110 for f in facts[:-1])
    assert sources[0]['url']==story.url
    assert len(facts)<=22


def test_source_budget_counts_rss_and_body_and_global_budget_retains_each_source():
    paragraphs=[' '.join(f'word{i}-{j}' for j in range(100)) for i in range(20)]
    http=HTTP('<article>'+''.join(f'<p>{p}</p>' for p in paragraphs)+'</article>')
    stories=[candidate(i+1,description=' '.join(f'intro{i}-{j}' for j in range(80))) for i in range(5)]
    for story in stories:
        assert sum(len(block.split()) for block in evidence(story,http))==1100
    facts,sources=make_facts(stories[0],stories[1:],http)
    assert len(facts)==22 and len(sources)==5
    assert {f['source_id'] for f in facts}=={story.id for story in stories}
    assert all(sum(len(f['text'].split()) for f in facts if f['source_id']==story.id)<=1100
               for story in stories)


def test_denied_article_uses_only_existing_rss_evidence():
    description='This synthetic feed description contains enough documented information to be retained without visiting its article.'
    http=HTTP('<article><p>This must never be requested or included in the evidence.</p></article>',allowed=False)
    assert evidence(candidate(description=description),http)==[description]
    assert http.requested==[]


def test_compaction_does_not_invent_words_and_removes_duplicate_source_paragraphs():
    first=' '.join(f'first-{i}' for i in range(30))
    second=' '.join(f'second-{i}' for i in range(220))
    assert ' '.join(evidence_blocks([first,first.upper(),second])).split()==(first+' '+second).split()
    assert sum(len(p.split()) for p in evidence_blocks([second],maximum=150))==150
