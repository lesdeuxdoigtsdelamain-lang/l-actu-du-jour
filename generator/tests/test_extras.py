"""Offline fixtures exercise publication, expiry, evidence and quota boundaries."""
from datetime import date, datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from generator.app.extras import (ExtraSource, Extras, HistoricalNote, UrgentAlert,
    historical_note, load_history_event, reviewed_note, select_alerts, severe_kind, update_extras)
from generator.app.ingest import Candidate
from generator.app.sources import Feed

NOW = datetime(2026, 11, 25, 17, 0, tzinfo=timezone.utc)
SOURCE = ExtraSource(publisher='Source de test', url='https://fr.wikipedia.org/wiki/Laboratoire')
FEED1 = Feed('a', 'Média A', 'https://www.france24.com/fr/rss', 'fr', 'Monde',
             ('www.france24.com',), 'rss')
FEED2 = Feed('b', 'Média B', 'https://www.rfi.fr/fr/rss', 'fr', 'Monde',
             ('www.rfi.fr',), 'rss')
TITLE = 'Un puissant séisme de magnitude 7,1 frappe la ville de Sendai au Japon'
LINES = [
    'Le laboratoire expérimental ouvre ses portes dans la ville fictive de Portville.',
    'Des chercheurs spécialisés rejoignent cette installation pour conduire leurs travaux dans des salles communes.',
    'Les équipes disposent de nouveaux instruments pour examiner ensemble les matériaux et leurs propriétés.',
    'Le projet réunit plusieurs instituts qui partageaient auparavant leurs équipements entre différents bâtiments.',
    'La direction organise ensuite des programmes de formation destinés aux techniciens et aux nouveaux chercheurs.',
    'Cette organisation permet de regrouper les expériences et de mieux coordonner les échanges entre équipes.'
]


def note_packet():
    facts = [{'id': f'H{i + 1}', 'text': line} for i, line in enumerate(LINES)]
    facts[0]['text'] = '25 novembre 1960 : ' + facts[0]['text']
    return {'edition_date': '2026-11-25', 'event_date': '1960-11-25',
            'event_summary': LINES[0], 'facts': facts}


def output():
    return {'lines': list(LINES), 'evidence': [[f'H{i + 1}'] for i in range(6)]}


def candidate(feed=FEED1, title=TITLE, stamp=None, url=None):
    return Candidate('c' * 22, title, '', url or f'https://{feed.hosts[0]}/news/seisme',
                     stamp or NOW - timedelta(minutes=10), feed)


def alert(stamp=None):
    return UrgentAlert(id='a' * 22, title=TITLE, published_at=stamp or NOW,
        expires_at=(stamp or NOW) + timedelta(hours=3), sources=[SOURCE])


class Writer:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def run_history(self, packet):
        self.calls += 1
        if self.fail:
            raise ValueError('Synthetic failed response')
        return output()

    def usage_stats(self):
        return {'calls': self.calls}


def no_feed(feeds, now):
    return [], {}


def fixture_loader(day):
    packet = note_packet()
    packet['edition_date'] = str(day)
    return packet, [SOURCE]


def test_urgent_requires_distinct_publishers_and_recent_corroboration():
    assert select_alerts([candidate()], NOW) == []
    same_publisher = Feed('a2', 'Média A', FEED1.url, 'fr', 'Monde', FEED1.hosts, 'rss')
    assert select_alerts([candidate(), candidate(same_publisher, url='https://www.france24.com/other')], NOW) == []
    selected = select_alerts([candidate(), candidate(FEED2)], NOW)
    assert len(selected) == 1 and len(selected[0].sources) == 2
    assert selected[0].expires_at == NOW - timedelta(minutes=10) + timedelta(hours=3)
    assert select_alerts([candidate(), candidate(FEED2, stamp=NOW + timedelta(minutes=1))], NOW) == []
    assert select_alerts([candidate(), candidate(FEED2, stamp=NOW - timedelta(hours=4))], NOW) == []


@pytest.mark.parametrize('headline', [
    'Un séisme de magnitude 3,1 frappe la ville de Sendai au Japon',
    'Dix ans après, commémoration de l’attentat à Paris',
    'Le procès des auteurs de la fusillade débute demain',
    'Une attaque terroriste déjouée par les services de police',
    'Tsunami : fausse alerte à Tokyo',
    'Une cyberattaque massive bloque les entreprises de Paris',
    'Un puissant séisme frappe le Japon sans victime',
    'Le risque de tsunami pourrait augmenter selon des chercheurs',
    'Attentat en 2015 : les habitants témoignent',
])
def test_ordinary_retrospective_or_unconfirmed_stories_are_not_red(headline):
    assert severe_kind(headline) is None


@pytest.mark.parametrize('headline', [
    'Une fusillade frappe un centre commercial à Lyon',
    'Un attentat frappe la capitale du pays',
    'Une attaque fait 20 morts dans la capitale',
    'Un séisme de magnitude 7,1 frappe la ville de Sendai',
    'Une alerte au tsunami est déclenchée au Japon',
    'Des inondations font des dizaines de morts au Bangladesh',
])
def test_severe_signals_can_enter_corroboration(headline):
    assert severe_kind(headline)


def test_refreshing_the_same_event_never_extends_its_expiry():
    first = select_alerts([candidate(), candidate(FEED2)], NOW)
    later = NOW + timedelta(hours=2)
    newer = [candidate(stamp=later), candidate(FEED2, stamp=later)]
    refreshed = select_alerts(newer, later, first)
    assert refreshed[0].id == first[0].id
    assert refreshed[0].expires_at == first[0].expires_at
    assert select_alerts(newer, NOW + timedelta(hours=3), first) == []


def test_unrelated_urgent_events_do_not_corroborate_each_other():
    assert not select_alerts([candidate(title='Un attentat frappe le centre de Paris'),
        candidate(FEED2, title='Une fusillade dans un centre commercial à Tokyo')], NOW)


@pytest.mark.parametrize('first,second', [
    ('Un séisme de magnitude 7,2 frappe le Japon', 'Un séisme de magnitude 7,2 frappe le Chili'),
    ('Un attentat fait 10 morts à Paris', 'Un attentat fait 10 morts à Madrid'),
    ('Un attentat fait 10 morts à Paris en France', 'Un attentat fait 10 morts à Lyon en France'),
    ('Un attentat fait 10 morts dans la capitale', 'Un attentat fait 10 morts dans la capitale'),
])
def test_disaster_vocabulary_and_counts_are_not_event_identity(first, second):
    assert select_alerts([candidate(title=first), candidate(FEED2, title=second)], NOW) == []


def test_history_event_is_the_same_day_and_month_in_a_past_year():
    h = HistoricalNote(edition_date='2026-11-25', event_date='1960-11-25', lines=LINES, sources=[SOURCE])
    assert h.event_date.year == 1960
    for invalid in ['1960-11-24', '2026-11-25', '2027-11-25', '1960-1-25', '1960-11-25T00:00:00Z']:
        with pytest.raises(ValidationError):
            HistoricalNote(edition_date='2026-11-25', event_date=invalid, lines=LINES, sources=[SOURCE])


def test_history_requires_six_to_ten_short_plain_lines():
    for lines in [LINES[:5], LINES + LINES, ['x' * 241, *LINES[1:]], ['<script>bad</script>', *LINES[1:]],
                  ['A line\nwith a control', *LINES[1:]], [''] + LINES[1:]]:
        with pytest.raises(ValidationError):
            HistoricalNote(edition_date='2026-11-25', event_date='1960-11-25', lines=lines, sources=[SOURCE])


def test_extra_contract_rejects_future_alerts_bad_sources_and_naive_times():
    with pytest.raises(ValidationError):
        Extras(updated_at=NOW, alerts=[alert(NOW + timedelta(minutes=1))])
    with pytest.raises(ValidationError):
        Extras(updated_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        ExtraSource(publisher='Source', url='http://127.0.0.1/secret')
    with pytest.raises(ValidationError):
        ExtraSource(publisher='Source', url='https://localhost.localdomain/secret')
    with pytest.raises(ValidationError):
        UrgentAlert(id='a' * 12, title=TITLE, published_at=NOW,
                    expires_at=NOW + timedelta(days=1), sources=[SOURCE])
    with pytest.raises(ValidationError):
        Extras(updated_at=NOW, another_unexpected_field='bad')


def test_history_evidence_rejects_unknown_ids_and_invented_numbers():
    assert len(historical_note(output(), note_packet(), [SOURCE]).lines) == 6
    missing = output()
    missing['evidence'][1] = ['H99']
    with pytest.raises(ValueError, match='evidence'):
        historical_note(missing, note_packet(), [SOURCE])
    invented = output()
    invented['lines'][1] += ' Le coût est de 988 millions.'
    with pytest.raises(ValueError, match='number'):
        historical_note(invented, note_packet(), [SOURCE])


def test_daily_history_is_cached_and_never_replaces_articles(tmp_path):
    editions = tmp_path / 'editions'
    editions.mkdir()
    existing = editions / '2026-11-25.json'
    existing.write_text('Existing edition belongs to the daily pipeline')
    writer = Writer()
    first = update_extras(tmp_path, NOW, [], provider=writer, collector=no_feed, history_loader=fixture_loader)
    second = update_extras(tmp_path, NOW + timedelta(hours=1), [], provider=writer,
                           collector=no_feed, history_loader=fixture_loader)
    assert writer.calls == 1
    assert first.history == second.history and len(second.history) == 1
    assert existing.read_text() == 'Existing edition belongs to the daily pipeline'


def test_failed_history_attempt_is_persisted_before_request_and_not_retried(tmp_path):
    class InterruptedWriter(Writer):
        def run_history(self, packet):
            marker = json.loads((tmp_path / 'extras-health.json').read_text())
            assert marker['history_attempted_for'] == '2026-11-25'
            return super().run_history(packet)
    writer = InterruptedWriter(fail=True)
    update_extras(tmp_path, NOW, [], provider=writer, collector=no_feed, history_loader=fixture_loader)
    update_extras(tmp_path, NOW + timedelta(hours=1), [], provider=writer,
                  collector=no_feed, history_loader=fixture_loader)
    assert writer.calls == 1
    assert json.loads((tmp_path / 'extras-health.json').read_text())['history_state'] == 'attempt_already_used'


def test_alert_checks_never_configure_or_call_an_ai_provider(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError('AI must not be configured for alerts')
    monkeypatch.setattr('generator.app.providers.configured', forbidden)
    writer = Writer()
    result = update_extras(tmp_path, NOW, [], alerts_only=True, provider=writer, collector=no_feed)
    assert writer.calls == 0 and result.history == []


def test_source_failure_preserves_only_unexpired_alerts(tmp_path):
    (tmp_path / 'extras.json').write_text(Extras(updated_at=NOW, alerts=[alert()]).model_dump_json())
    def unavailable(*args):
        raise RuntimeError('Synthetic feed outage')
    first = update_extras(tmp_path, NOW + timedelta(hours=1), [], alerts_only=True, collector=unavailable)
    assert len(first.alerts) == 1
    later = update_extras(tmp_path, NOW + timedelta(hours=4), [], alerts_only=True, collector=unavailable)
    assert later.alerts == []


def test_expired_event_is_not_reissued_when_rss_rewrites_timestamp(tmp_path):
    def feed_at(stamp):
        return lambda feeds, now: ([candidate(stamp=stamp), candidate(FEED2, stamp=stamp)], {})
    first = update_extras(tmp_path, NOW, [], alerts_only=True, collector=feed_at(NOW))
    assert first.alerts
    update_extras(tmp_path, NOW + timedelta(hours=4), [], alerts_only=True, collector=no_feed)
    later = update_extras(tmp_path, NOW + timedelta(hours=5), [], alerts_only=True,
                          collector=feed_at(NOW + timedelta(hours=5)))
    assert not later.alerts


def test_reviewed_november_24_entry_is_sourced_and_needs_no_model():
    note = reviewed_note(date(2026, 11, 24))
    assert note.event_date == date(1969, 11, 24) and len(note.lines) == 6
    assert any(source.publisher == 'NASA' for source in note.sources)
    assert reviewed_note(date(2026, 11, 25)) is None


def test_calendar_loader_checks_year_sources_and_context_before_generation():
    page = {'title': 'Laboratoire expérimental de Portville',
            'extract': ' '.join(LINES),
            'content_urls': {'desktop': {'page': SOURCE.url}}}
    class Calendar:
        def get(self, url, limit):
            assert url.endswith('/events/11/25') and limit <= 2000000
            return json.dumps({'events': [
                {'year': 2030, 'text': LINES[0], 'pages': [page]},
                {'year': 1960, 'text': LINES[0], 'pages': [page]},
                {'year': 1950, 'text': LINES[0], 'pages': [{**page, 'content_urls': None}]},
            ]}).encode()
    packet, sources = load_history_event(date(2026, 11, 25), Calendar())
    assert packet['event_date'] == '1960-11-25'
    assert len(sources) == 2 and all(source.url.startswith('https://fr.wikipedia.org/') for source in sources)


def test_history_prunes_to_seven_days_and_invalid_cache_is_optional(tmp_path):
    (tmp_path / 'extras.json').write_text('{broken')
    result = update_extras(tmp_path, NOW, [], alerts_only=True, collector=no_feed)
    assert result.history == []
    old = HistoricalNote(edition_date='2026-11-18', event_date='1960-11-18', lines=LINES, sources=[SOURCE])
    (tmp_path / 'extras.json').write_text(Extras(updated_at=NOW - timedelta(days=7), history=[old]).model_dump_json())
    result = update_extras(tmp_path, NOW, [], alerts_only=True, collector=no_feed)
    assert result.history == []
