"""Synthetic fixtures check contracts and dates; they are never published."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from generator.app.hadith import (HadithFeed, HadithNote, HadithSource,
    ReviewedCorpus, ReviewedHadith, cached_feed, load_corpus, note_for_day,
    update_hadith)
from generator.app.publish import validate

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc)
ORDINALS = ['الأول', 'الثاني', 'الثالث', 'الرابع', 'الخامس', 'السادس', 'السابع', 'الثامن']


def fixture_entry(index=0):
    return {'id': f'synthetic-text-{index}',
            'arabic': f'هذا نص اختباري اصطناعي {ORDINALS[index]}',
            'narrator_ar': 'راو للاختبار فقط',
            'reference_ar': 'مرجع اصطناعي للاختبار فقط',
            'authenticity_fr': 'Authentique — étiquette de test, sans attribution réelle.',
            'translation_fr': 'Texte synthétique uniquement destiné aux tests du logiciel.',
            'explanation_fr': 'Cette explication synthétique sert uniquement à vérifier la structure et les limites du logiciel.',
            'sources': [{'publisher': 'Source de test', 'scholar': 'Nom de test',
                         'url': f'https://binbaz.org.sa/audios/fixture-{index}', 'language': 'ar'}]}


def fixture_corpus():
    return {'schema_version': 1, 'reviewed_at': '2026-06-01',
            'entries': [fixture_entry(index) for index in range(8)]}


def corpus_file(tmp_path):
    path = tmp_path / 'reviewed-fixture.json'
    path.write_text(json.dumps(fixture_corpus(), ensure_ascii=False), encoding='utf-8')
    return path


def test_full_rotation_is_stable_and_contains_no_repetition():
    corpus = ReviewedCorpus.model_validate(fixture_corpus())
    start = date(2026, 11, 24)
    cycle = [note_for_day(corpus, start + timedelta(days=n)) for n in range(8)]
    assert len({note.id for note in cycle}) == 8
    assert note_for_day(corpus, start) == cycle[0]
    assert note_for_day(corpus, start + timedelta(days=8)).id == cycle[0].id
    assert {note.arabic for note in cycle} == {entry.arabic for entry in corpus.entries}


def test_publication_has_seven_london_dates_and_consumes_no_ai(tmp_path, monkeypatch):
    def refuse_network(*args, **kwargs):
        raise AssertionError('Daily hadith must not need a network or model request')

    monkeypatch.setattr(httpx.Client, 'send', refuse_network)
    path = corpus_file(tmp_path)
    site = tmp_path / 'site'
    feed = update_hadith(site, NOW, corpus_path=path)
    assert [note.edition_date for note in feed.hadiths] == [
        date(2026, 11, 24) - timedelta(days=n) for n in range(7)]
    assert HadithFeed.model_validate_json((site / 'hadith.json').read_bytes()) == feed
    health = json.loads((site / 'hadith-health.json').read_bytes())
    assert health['ai_calls'] == 0 and health['corpus_size'] == 8
    assert health['selected_id'] == feed.hadiths[0].id and health['state'] == 'ok'
    validate(site)


def test_summer_rotation_uses_london_midnight_not_utc(tmp_path):
    now = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)
    feed = update_hadith(tmp_path / 'site', now, corpus_path=corpus_file(tmp_path))
    assert feed.hadiths[0].edition_date == date(2026, 7, 2)


def test_invalid_review_preserves_previous_valid_file_byte_for_byte(tmp_path):
    path = corpus_file(tmp_path)
    site = tmp_path / 'site'
    original = update_hadith(site, NOW, corpus_path=path)
    raw = (site / 'hadith.json').read_bytes()
    path.write_text('{corrupt json', encoding='utf-8')
    retained = update_hadith(site, NOW + timedelta(days=1), corpus_path=path)
    assert retained == original
    assert (site / 'hadith.json').read_bytes() == raw
    health = json.loads((site / 'hadith-health.json').read_bytes())
    assert health['state'] == 'retained' and health['ai_calls'] == 0
    assert health['selected_id'] is None


def test_invalid_review_with_no_valid_cache_fails_without_publishing(tmp_path):
    site = tmp_path / 'site'
    path = tmp_path / 'missing.json'
    with pytest.raises(OSError):
        update_hadith(site, NOW, corpus_path=path)
    assert not (site / 'hadith.json').exists()


@pytest.mark.parametrize('url', [
    'http://binbaz.org.sa/audios/123',
    'https://binbaz.org.sa.example.com/audios/123',
    'https://mirror.binbaz.org.sa/audios/123',
    'https://evil.example/?source=binbaz.org.sa',
    'https://binbaz.org.sa@evil.example/audios/123',
    'https://binbaz.org.sa:444/audios/123',
    'https://binbaz.org.sa/',
])
def test_only_reviewed_official_https_source_urls_are_allowed(url):
    source = fixture_entry()['sources'][0]
    with pytest.raises(ValidationError):
        HadithSource.model_validate({**source, 'url': url})


@pytest.mark.parametrize('key,value', [
    ('arabic', 'A French phrase without Arabic.'),
    ('narrator_ar', '12345'),
    ('reference_ar', 'Sahih collection'),
    ('arabic', '<b>نص عربي</b>'),
    ('translation_fr', 'Première ligne\nAutre ligne'),
    ('explanation_fr', 'Un texte\u202e masqué par un contrôle.'),
    ('explanation_fr', 'mot ' * 101),
    ('authenticity_fr', 'Faible, attribution non vérifiée'),
    ('id', 'INVALID_uppercase_id'),
])
def test_original_language_plain_text_length_and_authenticity_are_validated(key, value):
    with pytest.raises(ValidationError):
        ReviewedHadith.model_validate({**fixture_entry(), key: value})


def test_corpus_rejects_duplicates_even_when_vowel_marks_differ():
    data = fixture_corpus()
    data['entries'][1]['arabic'] = data['entries'][0]['arabic'] + '\u064e'
    with pytest.raises(ValidationError, match='Repeated Arabic'):
        ReviewedCorpus.model_validate(data)
    data = fixture_corpus()
    data['entries'][1]['id'] = data['entries'][0]['id']
    with pytest.raises(ValidationError, match='Repeated hadith id'):
        ReviewedCorpus.model_validate(data)


def test_feed_rejects_future_old_and_duplicate_dates():
    corpus = ReviewedCorpus.model_validate(fixture_corpus())
    today = date(2026, 11, 24)
    for notes in [[note_for_day(corpus, today + timedelta(days=1))],
                  [note_for_day(corpus, today - timedelta(days=7))],
                  [note_for_day(corpus, today), note_for_day(corpus, today)]]:
        with pytest.raises(ValidationError):
            HadithFeed(updated_at=NOW, hadiths=notes)
    with pytest.raises(ValidationError):
        HadithFeed(updated_at=NOW.replace(tzinfo=None), hadiths=[note_for_day(corpus, today)])
    with pytest.raises(ValidationError):
        HadithNote(**fixture_entry(), edition_date='2026-1-24')


def test_future_cache_is_not_a_valid_fallback(tmp_path):
    corpus = ReviewedCorpus.model_validate(fixture_corpus())
    tomorrow = NOW + timedelta(days=1)
    path = tmp_path / 'hadith.json'
    path.write_text(HadithFeed(updated_at=tomorrow,
        hadiths=[note_for_day(corpus, tomorrow.date())]).model_dump_json(), encoding='utf-8')
    assert cached_feed(path, NOW) is None


def test_earlier_installed_clients_keep_their_existing_contracts(tmp_path):
    # Hadith is optional, and validating it never requires edition/extras changes.
    validate(tmp_path)
    update_hadith(tmp_path / 'site', NOW, corpus_path=corpus_file(tmp_path))
    assert not (tmp_path / 'site' / 'extras.json').exists()
    assert not (tmp_path / 'site' / 'latest.json').exists()


def test_hadith_schema_matches_the_validated_wire_contract():
    assert json.loads((ROOT / 'schema' / 'hadith.schema.json').read_text()) == HadithFeed.model_json_schema()


def test_checked_in_corpus_is_valid_and_covers_at_least_one_week():
    corpus = load_corpus()
    assert len(corpus.entries) >= 7
    assert all(source.language == 'ar' for entry in corpus.entries for source in entry.sources)
