"""Regression cases for repeated events and geography hidden by subject rubrics."""
from types import SimpleNamespace

from generator.app.diversity import (
    article_region, candidate_region, canonical_url, clean_articles,
    duplicates, is_morocco_article,
)


def article(id_, title, text, *, category='Monde', url=None, publisher='Journal'):
    return SimpleNamespace(
        id=id_, title=title, paragraphs=[text], category=category,
        source_refs=[SimpleNamespace(
            url=url or f'https://news.example.net/story/{id_}', publisher=publisher)],
    )


def hospital_pair():
    return (
        article('laayoune-first', 'Laâyoune : le ministre de la Santé accélère deux projets hospitaliers',
                'Youns Bjijou visite les chantiers hospitaliers de Laâyoune. Le ministre demande '
                'une accélération du futur centre universitaire et de la réhabilitation du centre '
                'Moulay Hassan. Les équipements et les travaux doivent préparer leur ouverture.',
                category='Santé'),
        article('laayoune-second', 'Laâyoune : visite ministérielle et avancées des projets hospitaliers du Sud',
                'À Laâyoune, Youns Bjijou inspecte le chantier du centre universitaire et la '
                'réhabilitation du centre Moulay Hassan. Le ministre suit les travaux hospitaliers '
                'et les équipements en préparation de leur ouverture.', category='Santé'),
    )


def test_different_publishers_urls_and_wording_do_not_hide_laayoune_duplicate():
    first, second = hospital_pair()
    assert first.source_refs[0].url != second.source_refs[0].url
    assert duplicates(first, second)
    kept, reasons = clean_articles([first, second], max_morocco=None)
    assert kept == [first]
    assert second.id in reasons[0] and first.id in reasons[0]


def test_fifa_reaction_next_day_is_still_same_news():
    first = article('fifa-18', 'Le Maroc franchit le Top 5 du classement FIFA pour la première fois',
                    'Les Lions de l’Atlas atteignent la cinquième place mondiale du classement '
                    'de la FIFA. La progression internationale établit un record historique '
                    'pour la sélection marocaine, grâce aux résultats de ses rencontres.')
    next_day = article('fifa-19', 'Le Maroc franchit le Top 5 du classement FIFA : un tournant historique',
                       'La presse internationale salue la cinquième place mondiale de la sélection '
                       'marocaine. Ce record historique dans le classement FIFA récompense '
                       'les résultats des Lions de l’Atlas, désormais dans le Top 5.')
    assert duplicates(first, next_day)
    kept, reasons = clean_articles([next_day], [first])
    assert kept == [] and 'déjà traité' in reasons[0]


def test_same_minister_and_city_do_not_make_distinct_events_duplicates():
    summit = article('dakhla-summit', 'Refonte du système de santé marocain présentée à Dakhla',
                     'Youns Bjijou expose la réforme nationale pendant le Global Summit de Dakhla. '
                     'Son discours traite de l’assurance maladie, de la prévention, de la '
                     'gouvernance et de la coopération avec les délégations africaines.', category='Santé')
    hospital = article('dakhla-hospital', 'Nouvel hôpital régional de Dakhla : état d’avancement et perspectives',
                       'Youns Bjijou inspecte le chantier hospitalier de Dakhla avec les autorités '
                       'locales. La construction avance, les services de chirurgie et d’imagerie '
                       'attendent leur équipement. La capacité d’accueil et le financement sont présentés.',
                       category='Santé')
    assert not duplicates(summit, hospital)
    kept, reasons = clean_articles([summit, hospital])
    assert kept == [summit]
    assert 'maximum quotidien Maroc' in reasons[0]


def test_different_hospital_in_another_city_is_not_a_repeat():
    laayoune, _ = hospital_pair()
    dakhla = article('hospital-dakhla', 'Le nouvel hôpital de Dakhla approche de son ouverture',
                    'Youns Bjijou inspecte la construction hospitalière de Dakhla. Des services '
                    'de chirurgie, d’imagerie et de pédiatrie doivent accueillir les patients. '
                    'Les autorités évaluent les équipements et le calendrier du chantier.', category='Santé')
    assert not duplicates(laayoune, dakhla)


def test_morocco_limit_uses_story_not_ui_category():
    health, _ = hospital_pair()
    army = article('moroccan-officers', 'Renforcement de la Force de stabilité à Gaza par le Maroc',
                   'Des officiers marocains rejoignent une mission internationale.', category='Monde musulman')
    telecom = article('maroc-telecom', 'Maroc Telecom présente sa stratégie de cybersécurité en Afrique',
                      'Le groupe renforce ses dispositifs de sécurité.', category='Science et technologie')
    uk = article('uk-news', 'Le NHS précise les conditions du nouveau service en Angleterre',
                 'Le dispositif concerne les habitants du Royaume-Uni.', category='Santé')
    assert all(is_morocco_article(a) for a in [health, army, telecom])
    kept, reasons = clean_articles([health, army, telecom, uk])
    assert kept == [health, uk]
    assert len(reasons) == 2
    assert article_region(uk) == 'Royaume-Uni'


def test_hespress_publisher_does_not_turn_international_news_into_morocco_news():
    world = article('gaza-world', 'Négociations sur une nouvelle aide humanitaire à Gaza',
                    'Des délégations cherchent un accord pour acheminer davantage de nourriture '
                    'et de médicaments aux habitants de Gaza. Les discussions concernent '
                    'les points de passage et la protection des convois.',
                    category='Maroc', publisher='Hespress', url='https://www.hespress.com/world.html')
    assert not is_morocco_article(world)


def test_incidental_country_mention_does_not_define_the_article():
    world = article('global-warming', 'Les conséquences du réchauffement dans les régions polaires',
                    'Les scientifiques observent la fonte des glaciers dans plusieurs régions '
                    'arctiques. Leurs relevés portent sur les températures et le recul des glaces.')
    world.paragraphs += ['Les chercheurs discutent des mesures satellitaires et de leurs limites.']
    world.paragraphs += ['Une délégation du Maroc figure parmi les participants à la conférence.']
    assert article_region(world) is None


def test_candidate_geography_accepts_french_english_arabic_without_publisher_guess():
    assert candidate_region(SimpleNamespace(title='Moroccan officers join international Gaza mission',
                                           description='')) == 'Maroc'
    assert candidate_region(SimpleNamespace(title='مشروع مستشفى جديد في مدينة الداخلة',
                                           description='')) == 'Maroc'
    assert candidate_region(SimpleNamespace(title='UK government announces housing changes',
                                           description='')) == 'Royaume-Uni'
    assert candidate_region(SimpleNamespace(title='Réforme des transports en France',
                                           description='')) == 'France'


def test_roundup_heading_repeats_but_the_news_must_not():
    title = 'Tour de l’actu en France'
    first = article('digest-19', title,
                    'Les députés examinent le financement du transport ferroviaire et '
                    'les collectivités discutent du logement étudiant. Les organisations '
                    'syndicales présentent leurs propositions concernant les contrats de travail.')
    second = article('digest-20', title,
                     'Une découverte archéologique révèle des vestiges antiques. Les musées '
                     'organisent une présentation des objets retrouvés tandis que des chercheurs '
                     'publient les résultats de leurs analyses scientifiques.')
    assert not duplicates(first, second)
    assert clean_articles([second], [first])[0] == [second]


def test_canonical_sources_catch_reposts_with_tracking_and_changed_headline():
    first = article('original-article', 'Une annonce importante pour les transports publics',
                    'Le réseau adapte ses horaires pendant les travaux prévus sur les voies.',
                    url='https://www.example.net/transport/%C3%A9tude/?utm_source=rss#partie')
    second = article('republished-article', 'Les trajets des voyageurs vont bientôt changer',
                     'Une communication précise les changements.',
                     url='https://example.net/transport/étude?utm_campaign=mail')
    assert canonical_url(first.source_refs[0].url) == canonical_url(second.source_refs[0].url)
    assert duplicates(first, second)


def test_a_missing_optional_morocco_item_never_requires_filling_it():
    uk = article('uk-only', 'Le Royaume-Uni annonce une révision des transports',
                 'Les autorités britanniques présentent une proposition concernant le réseau.')
    assert clean_articles([uk]) == ([uk], [])


def test_later_duplicate_does_not_use_up_morocco_slot():
    first, duplicate = hospital_pair()
    fresh = article('new-maroc', 'Une découverte archéologique près de Rabat',
                    'Des archéologues ont retrouvé des céramiques anciennes dans une '
                    'enceinte historique. Le laboratoire analyse les objets pour préciser '
                    'la chronologie du site et les pratiques artisanales locales.')
    kept, reasons = clean_articles([duplicate, fresh], [first])
    assert kept == [fresh] and len(reasons) == 1
