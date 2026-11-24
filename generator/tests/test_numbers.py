import pytest

from generator.app.editor import allowed_numbers, normalized_number, unsupported_numbers, strip_inline_fact_refs


@pytest.mark.parametrize('text,language,expected', [
    ('80,000', 'en', '80000'),
    ('80 000', 'fr', '80000'),
    ('80\u202f000', 'fr', '80000'),
    ('1\u00a0234\u00a0567,89', 'fr', '1234567.89'),
    ('8.8', 'en', '8.8'),
    ('8,8', 'fr', '8.8'),
    ('2,07', 'fr', '2.07'),
    ('1,234', 'fr', '1.234'),
    ('1,234', 'en', '1234'),
    ('1.234,56', 'es', '1234.56'),
    ('٨٠٬٠٠٠', 'ar', '80000'),
    ('٢٫٠٧', 'ar', '2.07'),
    ('۱۲٫۵٪', 'ar', '12.5%'),
    ('8,80 %', 'fr', '8.8%'),
    ('−3,5', 'fr', '-3.5'),
    ('2,07', None, '2.07'),
    ('1,234', None, None),
    ('1.234', None, None),
    ('1,23', 'en', None),
    ('1,234.56', 'fr', None),
])
def test_canonical_numbers_respect_source_conventions(text, language, expected):
    assert normalized_number(text, language) == expected


def source(id_, language):
    return {'id': id_, 'language': language}


def result(text):
    return {'titre': 'Le point sur les données annoncées', 'paragraphes': [text]}


def test_english_thousands_and_decimals_translate_to_french_without_false_rejection():
    facts = [{'id': 'en:0', 'source_id': 'en', 'text': 'The service helped 80,000 people with an increase of 8.8%.'}]
    sources = [source('en', 'en')]
    assert allowed_numbers(facts, sources) == {'80000', '8.8%'}
    assert unsupported_numbers(result('Le service compte 80 000 personnes, soit une hausse de 8,8 %.'), facts, sources) == set()


def test_decimal_two_point_zero_seven_never_authorizes_207_or_unit_conversion():
    facts = [{'id': 'fr:0', 'source_id': 'fr', 'text': 'Le terrain couvre 2,07 hectares.'}]
    sources = [source('fr', 'fr')]
    assert allowed_numbers(facts, sources) == {'2.07'}
    assert unsupported_numbers(result('La superficie atteint 207 mètres carrés.'), facts, sources) == {'207'}
    assert unsupported_numbers(result('La superficie atteint 20 700 mètres carrés.'), facts, sources) == {'20700'}
    assert unsupported_numbers(result('La superficie atteint 2,07 hectares.'), facts, sources) == set()


def test_fact_language_is_resolved_by_source_id_not_other_packet_sources():
    facts = [
        {'id': 'en:0', 'source_id': 'en', 'text': 'A total of 1,234 entries.'},
        {'id': 'fr:0', 'source_id': 'fr', 'text': 'Une mesure de 2,345 unités.'},
        {'id': 'ar:0', 'source_id': 'ar', 'text': 'تم تسجيل ٨٠٬٠٠٠ حالة ونسبة ٢٫٥٪'},
    ]
    sources = [source('en', 'en'), source('fr', 'fr'), source('ar', 'ar')]
    assert allowed_numbers(facts, sources) == {'1234', '2.345', '80000', '2.5%'}
    assert unsupported_numbers(result('Les valeurs publiées sont 1 234, puis 2,345 et 80 000, avec 2,5 %.'), facts, sources) == set()


def test_unknown_ambiguous_source_values_do_not_authorize_either_interpretation():
    facts = [{'id': 'x:0', 'text': 'A value reported as 1,234.'}]
    assert allowed_numbers(facts, []) == set()
    assert unsupported_numbers(result('Une valeur de 1 234.'), facts, []) == {'1234'}
    assert unsupported_numbers(result('Une valeur de 1,234.'), facts, []) == {'1.234'}


def test_percentage_and_negative_sign_are_not_silently_erased():
    facts = [{'id': 'fr:0', 'source_id': 'fr', 'text': 'Une variation de -3,5 % est annoncée.'}]
    sources = [source('fr', 'fr')]
    assert allowed_numbers(facts, sources) == {'-3.5%'}
    assert unsupported_numbers(result('Une variation de 3,5 %.'), facts, sources) == {'3.5%'}
    assert unsupported_numbers(result('Une variation de -3,5.'), facts, sources) == {'-3.5'}


def test_publication_clock_does_not_supply_arbitrary_reported_quantities():
    sources = [{'id': 'a', 'language': 'fr', 'published_at': '2026-11-20T17:48:59Z'}]
    assert allowed_numbers([], sources) == {'2026', '11', '20'}
    assert unsupported_numbers(result('Les chiffres sont 48 et 59.'), [], sources) == {'48', '59'}


def test_malformed_output_numbers_fail_closed():
    facts = [{'id': 'fr:0', 'source_id': 'fr', 'text': 'Le montant est de 1234,56 euros.'}]
    refused = unsupported_numbers(result('Le montant est de 1,234.56 euros.'), facts, [source('fr', 'fr')])
    assert refused and any(value.startswith('format ambigu:') for value in refused)


def test_exact_known_inline_fact_ids_are_removed_but_real_figures_remain_checked():
    fact_id = '2989abcedf01234567890ab:0'
    facts = [{'id': fact_id, 'text': 'Une décision est annoncée.'}]
    raw = {'titre': 'Un titre informatif sans identifiant',
           'paragraphes': [f'Une décision est annoncée [{fact_id}]. Le coût atteint 999 euros.'],
           'preuves': [[fact_id]]}
    cleaned = strip_inline_fact_refs(raw, facts)
    assert cleaned['paragraphes'] == ['Une décision est annoncée. Le coût atteint 999 euros.']
    assert cleaned['preuves'] == raw['preuves']
    assert fact_id in raw['paragraphes'][0]
    assert unsupported_numbers(cleaned, facts, []) == {'999'}


def test_unknown_or_unproved_bracketed_references_and_dates_are_never_erased():
    fact_id = '2989abcedf01234567890ab:0'
    facts = [{'id': fact_id, 'text': 'Une décision est annoncée.'}]
    raw = {'paragraphes': [f'Date [2025]. Identifiant [{fact_id}]. Inconnu [abc999xyz:1].'],
           'preuves': [['other-proof']]}
    assert strip_inline_fact_refs(raw, facts) == raw


def test_inline_citations_can_contain_multiple_exact_proven_ids():
    ids = ['abcde0123456789abcde012:0', 'abcde0123456789abcde012:1']
    facts = [{'id': q, 'text': 'Texte factuel.'} for q in ids]
    raw = {'paragraphes': [f'Annonce publique [sources: {ids[0]}, {ids[1]}].'], 'preuves': [ids]}
    assert strip_inline_fact_refs(raw, facts)['paragraphes'] == ['Annonce publique.']
