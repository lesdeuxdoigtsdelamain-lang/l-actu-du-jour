from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

from generator.app.briefing import (
    domestic_france, is_roundup, prepare_roundup, topic_records,
)
from generator.app.ingest import Candidate
from generator.app.sources import Feed


DAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 17, tzinfo=timezone.utc)
FEED = Feed('france', 'Journal français', 'https://news.example.net/france/rss',
            'fr', 'France', ('news.example.net',), 'rss')
TEXTS = [
    'Les trains régionaux adaptent les horaires pour tenir compte des travaux sur les voies. '
    'Les voyageurs disposent de correspondances en gare et de bus supplémentaires. '
    'Les équipes ferroviaires rénovent les rails et remplacent les équipements de signalisation.',
    'Les universités examinent les demandes de logements étudiants pour améliorer leur attribution. '
    'Les résidences préparent les inscriptions et les dossiers de bourse. '
    'Les associations accompagnent les jeunes dans leurs démarches administratives auprès des campus.',
    'Les chercheurs publient une étude archéologique consacrée aux vestiges antiques découverts dans un village. '
    'Le laboratoire analyse les fragments de céramique. Les fouilles révèlent des ateliers artisanaux '
    'et renseignent les historiens sur les méthodes de fabrication.',
    'Les agriculteurs organisent la collecte des récoltes et négocient les conditions de stockage. '
    'Les coopératives adaptent les silos aux besoins des producteurs. Les exploitations surveillent '
    'la qualité des grains et coordonnent le transport vers les acheteurs.',
    'Les bibliothèques municipales élargissent leurs catalogues numériques pour rendre les ouvrages accessibles. '
    'Les lecteurs peuvent emprunter les romans et les dictionnaires depuis leur ordinateur. '
    'Les documentalistes assurent les formations et accompagnent les familles. '
    'Un atelier facilite la découverte des outils de recherche.',
]
TITLES = [
    'Les trains régionaux adaptent leurs horaires en France',
    'Le logement étudiant devient une priorité des universités',
    'Des vestiges antiques éclairent les techniques artisanales',
    'Les agriculteurs préparent le stockage des récoltes',
    'Les bibliothèques étendent leurs catalogues numériques',
]


def candidates():
    return [Candidate(f'topic-france-{i}', title, text, f'https://news.example.net/france/{i}', NOW, FEED)
            for i, (title, text) in enumerate(zip(TITLES, TEXTS))]


def fake_facts(candidate, others, http):
    # Synthetic repeat is deliberate unit-test length, never publication content.
    return ([{'id': candidate.id + ':0', 'source_id': candidate.id,
              'text': candidate.description + ' ' + candidate.description}],
            [{'id': candidate.id, 'publisher': candidate.feed.publisher, 'url': candidate.url,
              'language': 'fr', 'published_at': candidate.published_at.isoformat()}])


def test_roundup_has_four_independent_topics_one_source_each_and_bounded_packet():
    result = prepare_roundup(candidates(), None, DAY, make_facts_fn=fake_facts)
    assert result is not None
    candidate, facts, sources, topics = result
    assert candidate.id == 'france-tour-2026-11-20'
    assert candidate.title == 'Tour de l’actu en France — 20 novembre 2026'
    assert candidate.feed.category == 'France'
    assert len(topics) == len(sources) == 4
    assert all(len(t['source_ids']) == 1 and t['fact_ids'] for t in topics)
    assert sum(len(f['text'].split()) for f in facts) >= 280
    assert len(json.dumps({'facts': facts, 'sources': sources, 'topics': topics}, ensure_ascii=False)) <= 8500


def test_a_shortage_is_reported_instead_of_padding_roundup():
    assert prepare_roundup(candidates()[:2], None, DAY, make_facts_fn=fake_facts) is None

    def too_short(c, others, http):
        facts, sources = fake_facts(c, others, http)
        facts[0]['text'] = 'Quelques mots insuffisants pour produire un article étayé.'
        return facts, sources

    assert prepare_roundup(candidates(), None, DAY, make_facts_fn=too_short) is None


def test_three_topics_allowed_only_with_enough_evidence():
    def deeper(c, others, http):
        facts, sources = fake_facts(c, others, http)
        facts[0]['text'] = c.description + ' ' + c.description + ' ' + c.description
        return facts, sources

    result = prepare_roundup(candidates()[:3], None, DAY, make_facts_fn=deeper)
    assert result is not None and len(result[3]) == 3


def test_france_feed_does_not_allow_foreign_or_morocco_news_in_digest():
    selected = candidates()
    foreign = [
        'Macron rencontre les dirigeants en Ukraine',
        'Le nouvel hôpital de Laâyoune approche de son ouverture',
        'Les autorités du Royaume-Uni révisent leurs dispositifs',
        'Le président sénégalais annonce un nouveau décret',
        'L’Assemblée nationale au Sénégal débat du budget',
    ]
    for title in foreign:
        c = Candidate('foreign-item', title, '', 'https://news.example.net/foreign', NOW, FEED)
        assert not domestic_france(c)
    world_feed = Feed('world-fr', 'Journal français', FEED.url, 'fr', 'Monde', FEED.hosts, 'rss')
    world = Candidate('foreign-world', 'Un nouveau programme de recherche au Brésil', '',
                      'https://news.example.net/world', NOW, world_feed)
    assert not domestic_france(world)
    assert all(domestic_france(c) for c in selected)


def test_previous_sources_and_rephrased_duplicate_topics_are_skipped():
    pool = candidates()
    first = pool[0]
    duplicate = Candidate('same-trains-new-id', 'Les horaires ferroviaires changent pour les voyageurs',
                          first.description, 'https://news.example.net/other/train', NOW, FEED)
    prepared = prepare_roundup([first, duplicate, *pool[1:]], None, DAY, make_facts_fn=fake_facts)
    assert prepared is not None
    assert duplicate.id not in {t['id'] for t in prepared[3]}
    previous = [{'id': first.id, 'title': first.title, 'paragraphs': [first.description],
                 'source_refs': [{'url': first.url}]}]
    prepared = prepare_roundup(pool, None, DAY, make_facts_fn=fake_facts, previous_articles=previous)
    assert prepared is not None
    assert first.id not in {t['id'] for t in prepared[3]}


def test_memories_contain_only_generated_paragraphs_and_public_source_metadata():
    _, facts, sources, topics = prepare_roundup(candidates(), None, DAY, make_facts_fn=fake_facts)
    paragraphs = [f'Brève française originale portant sur le sujet {i}. Autres détails générés et publiés.'
                  for i in range(4)]
    article = SimpleNamespace(title='Tour de l’actu en France', paragraphs=paragraphs)
    proofs = [topic['fact_ids'] for topic in topics]
    records = topic_records(article, proofs, topics, facts, sources)
    assert len(records) == 4
    assert [record['paragraphs'] for record in records] == [[p] for p in paragraphs]
    assert all(record['category'] == 'France' for record in records)
    assert all('id' not in source for record in records for source in record['source_refs'])
    encoded = json.dumps(records, ensure_ascii=False)
    assert facts[0]['text'] not in encoded
    assert all(record['title'].startswith('Brève française originale') for record in records)


def test_missing_or_unknown_proofs_cannot_create_false_memory():
    _, facts, sources, topics = prepare_roundup(candidates(), None, DAY, make_facts_fn=fake_facts)
    article = {'paragraphs': ['Un seul paragraphe généré.']}
    assert topic_records(article, [], topics, facts, sources) == []
    assert topic_records(article, [['unknown-fact']], topics, facts, sources) == []
    records = topic_records(article, [topics[0]['fact_ids']], topics, facts, sources)
    assert len(records) == 1 and records[0]['id'] == topics[0]['id']


def test_roundup_heading_recognized_with_curly_quotes_and_date():
    assert is_roundup({'title': 'Tour de l’actu en France — 20 novembre 2026'})
    assert is_roundup(SimpleNamespace(title="Tour de l'actualité en France"))
    assert not is_roundup(SimpleNamespace(title='Le Tour de France ouvre une nouvelle étape'))
