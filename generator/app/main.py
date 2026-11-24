"""Publish distinct, geographically balanced daily editions without inventing filler."""
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from collections import Counter
import argparse
import json
import re
from pydantic import ValidationError
from .sources import load
from .fetch import Fetcher
from .ingest import collect, cluster, rank, Candidate, EXCLUDE
from .evidence import make_facts
from .providers import configured, AiError
from .editor import assemble, rejection_reason, reflow_paragraphs, allowed_numbers, unsupported_numbers, strip_inline_fact_refs
from .commons import find
from .models import Edition
from .publish import put, validate, history, health, atomic
from .diversity import clean_articles, duplicates, article_region, candidate_region, is_morocco_article, canonical_url
from .briefing import prepare_roundup, is_roundup, topic_records

ROOT = Path(__file__).resolve().parents[2]
LONDON = ZoneInfo('Europe/London')
TARGET_WORDS = 3300
MIN_COMPLETE_WORDS = 3060
MAX_ARTICLES = 8
MIN_ARTICLES = 6


def ai_usage(provider, errors=()):
    if not provider:
        return {'calls': 0}
    if hasattr(provider, 'usage_stats'):
        usage = provider.usage_stats()
    else:
        usage = {'calls': provider.calls, 'neurons': round(provider.usage_neurons, 2)}
    rejections = Counter(message.removeprefix('Rejected: ').rsplit(' (', 1)[0]
                         for message in errors if message.startswith('Rejected: '))
    usage['quality_rejections'] = dict(rejections)
    usage['quality_rejections_total'] = sum(rejections.values())
    if provider.calls >= provider.max_calls:
        usage['local_attempt_limit_reached'] = True
    return usage


def complete(articles):
    """A long but unbalanced edition is not complete."""
    return (MIN_ARTICLES <= len(articles) <= MAX_ARTICLES
            and sum(a.word_count for a in articles) >= MIN_COMPLETE_WORDS
            and sum(a.minutes for a in articles) >= 18
            and sum(is_roundup(a) and 300 <= a.word_count <= 400 for a in articles) == 1
            and any(article_region(a) == 'Royaume-Uni' for a in articles)
            and sum(is_morocco_article(a) for a in articles) <= 1)


def today_edition(site, day):
    path = site / 'editions' / f'{day}.json'
    return Edition.model_validate_json(path.read_bytes()) if path.exists() else None


def earlier_articles(site, day):
    articles = []
    for path in sorted((site / 'editions').glob('*.json')):
        edition = Edition.model_validate_json(path.read_bytes())
        if 1 <= (day - edition.edition_date).days <= 7:
            articles.extend(edition.articles)
    return articles


def load_memory(site):
    path = site / 'editorial-memory.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Invalid editorial memory')
    return data


def previous_topics(memory, day):
    return [topic for stamp, topics in memory.items()
            if 1 <= (day - date.fromisoformat(stamp)).days <= 7 for topic in topics]


def slots_missing(articles):
    return int(not any(is_roundup(a) for a in articles)) + int(
        not any(article_region(a) == 'Royaume-Uni' for a in articles))


def seed_articles(existing, previous):
    if not existing:
        return [], []
    kept, reasons = clean_articles(existing.articles, previous)
    # Old individual French stories give way to one daily domestic roundup.
    kept = [a for a in kept if not EXCLUDE.search(a.title)
            and all(s.published_at.astimezone(LONDON).date() <= existing.edition_date for s in a.source_refs)
            and (article_region(a) != 'France' or is_roundup(a))
            and (not is_roundup(a) or 300 <= a.word_count <= 400)]
    # Reserve the required slots even when an old edition already had eight items.
    while len(kept) + slots_missing(kept) > MAX_ARTICLES:
        index = next((i for i in reversed(range(len(kept)))
                      if not is_roundup(kept[i]) and article_region(kept[i]) != 'Royaume-Uni'), None)
        if index is None:
            break
        removed = kept.pop(index)
        reasons.append(f'{removed.id}: place réservée aux rubriques France et Royaume-Uni')
    # An eight-item legacy edition must not be frozen below the reading target.
    # Replace only its shortest non-required reports, leaving room for real depth.
    if len(existing.articles) == MAX_ARTICLES:
        while (sum(a.word_count for a in kept) + 550 * (MAX_ARTICLES-len(kept)) < MIN_COMPLETE_WORDS
               or sum(a.minutes for a in kept) + 3 * (MAX_ARTICLES-len(kept)) < 18):
            eligible = [a for a in kept if not is_roundup(a) and article_region(a) != 'Royaume-Uni'
                        and a.word_count < 450]
            if not eligible:
                break
            shortest = min(eligible, key=lambda a:a.word_count)
            kept.remove(shortest)
            reasons.append(f'{shortest.id}: remplacement d’un article trop court pour la durée visée')
    # Show the country explicitly instead of hiding Morocco in health/business.
    kept = [a.model_copy(update={'category': article_region(a)})
            if article_region(a) in ('Maroc', 'Royaume-Uni') else a for a in kept]
    return kept, reasons


def repair_candidates(feeds, day):
    """Dated, reviewed source metadata makes a historical correction reproducible.

    No model output or credentials are stored here. Source pages are fetched with
    exactly the same host, robots and evidence checks as live feed candidates.
    """
    path = ROOT / 'config' / 'repair-sources.json'
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding='utf-8')).get(str(day), [])
    by_id = {f.id: f for f in feeds}
    result = []
    for item in records:
        feed = by_id[item['feed_id']]
        stamp = datetime.fromisoformat(item['published_at'].replace('Z', '+00:00'))
        url = item['url']
        u = urlsplit(url)
        if (stamp.tzinfo is None or stamp.astimezone(LONDON).date() != day
                or u.scheme != 'https' or u.hostname not in feed.hosts
                or u.username or u.password or u.port not in (None, 443)):
            raise ValueError('Invalid historical source metadata')
        result.append(Candidate(sha256(url.encode()).hexdigest()[:22], item['title'],
                                item.get('description', ''), url, stamp, feed))
    return result


def english_title(title):
    words = set(re.findall(r"[a-zàâçéèêëîïôùûü]+", title.casefold()))
    english = words & {'the','and','for','with','after','before','why','how','while','says','saw',
                       'what','which','their','against','its','has','have','will','from','back','hope','boosting'}
    french = words & {'le','la','les','un','une','de','du','des','en','au','aux','et','à','pour',
                      'dans','sur','après','avec','son','sa','ces','ce','qui','vers'}
    return len(english) >= 2 and len(english) > len(french)


def draft(c, facts, sources, provider, seen, errors, *, mode='article', topics=()):
    """Generate once, with at most one targeted revision; validate actual length."""
    minimum, maximum = (300, 400) if mode == 'tour_france' else (450, 650)
    packet = {
        'event_title': c.title,
        'mode_editorial': mode, 'language': 'fr-FR',
        'evidence_words': sum(len(f['text'].split()) for f in facts),
        'facts': facts, 'sujets': topics,
        'source_names': [s['publisher'] for s in sources],
        'chiffres_autorises': sorted(allowed_numbers(facts, sources)),
        'sujets_deja_traites': [getattr(a, 'title', a.get('title', '') if isinstance(a, dict) else '')
                               for a in seen][-60:],
        'objectif_editorial': f'{minimum} à {maximum} mots réels, sans répétition ni ajout non sourcé.',
    }
    # Only a roundup has a prescribed (already French) title. Passing an English
    # source headline as titre_attendu caused the model to copy it verbatim.
    if mode == 'tour_france':
        packet['titre_attendu'] = c.title
    for attempt in range(2):
        if provider.calls >= provider.max_calls:
            break
        output = provider.run(packet)
        repaired = reflow_paragraphs(strip_inline_fact_refs(output, facts))
        words = sum(len(p.split()) for p in repaired.get('paragraphes', []) if isinstance(p, str))
        reason = rejection_reason(repaired, facts, sources)
        if reason is None and english_title(repaired.get('titre','')):
            reason = 'le titre doit être rédigé en français, pas repris de la source anglaise'
        article = None
        if reason is None:
            try:
                article = assemble(repaired, c, facts, sources, min_words=minimum,
                                   max_words=maximum, category='France' if mode == 'tour_france' else None)
            except (ValidationError, ValueError, TypeError):
                reason = 'format invalide'
        if article is None and reason is None:
            reason = f'longueur réelle {words}, attendu {minimum} à {maximum} mots'
        records = []
        if article is not None:
            if mode == 'tour_france':
                article = article.model_copy(update={'title': c.title})
                records = topic_records(article, repaired['preuves'], topics, facts, sources)
                owners = [{i for i, topic in enumerate(topics) if set(refs) & set(topic['fact_ids'])}
                          for refs in repaired['preuves']]
                if (len(article.paragraphs) != 4 or len(records) != len(topics)
                        or any(len(owner) != 1 for owner in owners)):
                    reason = 'le tour de France doit couvrir tous les sujets distincts en quatre paragraphes'
                elif any(any(duplicates(record, old) for old in seen) for record in records):
                    reason = 'une brève française reprend un sujet déjà publié'
            if any(duplicates(article, old) for old in seen):
                reason = 'événement déjà traité dans le journal'
            if reason is None:
                return article, records
        errors.append(f'Rejected: {reason} ({words} mots)')
        print(f'Article refusé : {reason} ({words} mots)', flush=True)
        if reason and ('déjà traité' in reason or 'déjà publié' in reason):
            # A second wording cannot turn the same event into a new story.
            return None, []
        packet['revision_notice'] = (
            f'Premier essai refusé : {reason}. Rédige une nouvelle version entre {minimum} et {maximum} mots. '
            'Respecte le mode éditorial, les faits et leurs unités. Ne complète jamais avec un fait inventé.')
        packet['chiffres_refuses'] = sorted(unsupported_numbers(repaired, facts, sources))
        if packet['chiffres_refuses']:
            print('Valeurs numériques refusées : ' + ', '.join(packet['chiffres_refuses']), flush=True)
    return None, []


def generate(site, now, provider, feeds, *, target_day=None, memory_sink=None):
    day = target_day or now.astimezone(LONDON).date()
    cutoff = min(now, datetime.combine(day, time.max, tzinfo=LONDON).astimezone(timezone.utc))
    existing = today_edition(site, day)
    memory = load_memory(site)
    previous = [*earlier_articles(site, day), *previous_topics(memory, day)]
    articles, removals = seed_articles(existing, previous)
    errors = []
    candidates, status = collect(feeds, cutoff)
    reviewed = []
    if target_day is not None:
        reviewed = repair_candidates(feeds, day)
        candidates = [*reviewed, *candidates]
    # The feed parser's small live clock tolerance must never import tomorrow's
    # stories into a historical edition being repaired.
    candidates = [c for c in candidates if c.published_at <= cutoff]
    old_ids, old_titles = history(site, day)
    old_urls = {canonical_url(s.url) for a in [*earlier_articles(site, day), *articles] for s in a.source_refs}
    for topic in previous_topics(memory, day):
        old_urls.update(canonical_url(s['url']) for s in topic.get('source_refs', []))
        old_titles.append(topic['title'])
    for a in articles:
        old_ids.add(a.id)
        old_titles.append(a.title)
    grouped = cluster(candidates)
    grouped = [c for c in grouped if not any(canonical_url(x.url) in old_urls for x in [c, *c.clustered])]
    ranked = rank(grouped, old_ids, old_titles, limit=len(grouped) if reviewed else 100)
    if reviewed:
        priority = {canonical_url(c.url): i for i, c in enumerate(reviewed)}
        ranked.sort(key=lambda c: min((priority.get(canonical_url(x.url), len(priority))
                                       for x in [c, *c.clustered]), default=len(priority)))
        ranked = ranked[:100]
    print(f'Édition {day}: {len(articles)} articles conservés, {len(removals)} retraits, {len(ranked)} candidats', flush=True)
    for reason in removals:
        print('Correction : ' + reason, flush=True)
    source_http = Fetcher({x for f in feeds for x in f.hosts})
    image_http = Fetcher({'commons.wikimedia.org'})
    images = {}
    records = memory.get(str(day), []) if any(is_roundup(a) for a in articles) else []
    blocked_urls = set(old_urls)

    def accept(article, new_records=()):
        region = article_region(article)
        if region in ('Maroc', 'Royaume-Uni'):
            article = article.model_copy(update={'category': region})
        if article.category not in images:
            images[article.category] = find(article.category, image_http)
        article = article.model_copy(update={'image': images[article.category]})
        articles.append(article)
        blocked_urls.update(canonical_url(s.url) for s in article.source_refs)
        records.extend(new_records)
        print(f'Article accepté : {article.word_count} mots, {article.category}, {article.id}', flush=True)

    if provider:
        try:
            # France and UK receive the first available slots and inference budget.
            if not any(is_roundup(a) for a in articles):
                todays = [c for c in ranked if c.published_at.astimezone(LONDON).date() == day]
                prepared = prepare_roundup(todays, source_http, day, make_facts_fn=make_facts,
                                           previous_articles=[*previous, *articles])
                if prepared is not None:
                    c, facts, sources, topics = prepared
                    article, new_records = draft(c, facts, sources, provider, [*previous, *articles], errors,
                                                 mode='tour_france', topics=topics)
                    if article is not None:
                        accept(article, new_records)
                else:
                    errors.append('Tour France : moins de trois sujets distincts suffisamment documentés')

            uk = [c for c in ranked if candidate_region(c) == 'Royaume-Uni'
                  or (c.feed.category == 'Royaume-Uni' and candidate_region(c) not in ('Maroc', 'France'))]
            other = [c for c in ranked if c not in uk]
            for c in [*uk, *other]:
                if provider.calls >= provider.max_calls:
                    break
                if len(articles) >= MAX_ARTICLES:
                    break
                if complete(articles) and (sum(a.word_count for a in articles) >= TARGET_WORDS
                                           or sum(a.minutes for a in articles) >= 19):
                    break
                region = candidate_region(c)
                # The French domestic news belongs in the single daily roundup.
                if region == 'France' or c.feed.category == 'France':
                    continue
                if any(canonical_url(x.url) in blocked_urls for x in [c, *c.clustered]):
                    continue
                if region == 'Maroc' and any(is_morocco_article(a) for a in articles):
                    continue
                uk_missing = not any(article_region(a) == 'Royaume-Uni' for a in articles)
                if len(articles) >= MAX_ARTICLES - slots_missing(articles) and not (uk_missing and c in uk):
                    continue
                # Keep UK represented without allowing it to crowd out other regions.
                if c in uk and not uk_missing:
                    continue
                facts, sources = make_facts(c, c.clustered, source_http)
                facts = facts[:22]
                while len(str(facts)) > 8500 and len(facts) > 3:
                    facts.pop()
                if sum(len(f['text'].split()) for f in facts) < 400 or len(facts) < 3:
                    continue
                article, _ = draft(c, facts, sources, provider, [*previous, *articles, *records], errors)
                if article is None:
                    continue
                if article_region(article) == 'France':
                    continue
                if is_morocco_article(article) and any(is_morocco_article(a) for a in articles):
                    print('Article écarté : maximum quotidien Maroc atteint', flush=True)
                    continue
                if c in uk and article_region(article) != 'Royaume-Uni':
                    print('Article écarté : sujet non consacré au Royaume-Uni', flush=True)
                    continue
                if len(articles) >= MAX_ARTICLES - slots_missing(articles) and not (
                        uk_missing and article_region(article) == 'Royaume-Uni'):
                    continue
                accept(article)
        except AiError as exc:
            errors.append(str(exc))
            print('Rédaction interrompue : ' + str(exc), flush=True)
    if not articles:
        return None, status, ai_usage(provider, errors), 'Aucun article publiable. ' + '; '.join(errors[:2])
    # Enforce the editorial gate again after generation, before the public write.
    articles, final_removals = clean_articles(articles, previous)
    if not articles:
        return None, status, ai_usage(provider, errors), 'Aucun sujet nouveau après contrôle des doublons'
    label = 'complete' if complete(articles) else 'partielle'
    edition = Edition(edition_id=f'{day}-fr-FR', edition_date=day, generated_at=now,
                      status=label, minutes=sum(a.minutes for a in articles), articles=articles)
    if memory_sink is not None:
        memory_sink[str(day)] = records
    missing = []
    if not any(is_roundup(a) for a in articles):
        missing.append('tour France')
    if not any(article_region(a) == 'Royaume-Uni' for a in articles):
        missing.append('Royaume-Uni')
    reason = (f'Édition {label}: {len(articles)} articles, {edition.minutes} min; '
              'Maroc au maximum une fois, contrôle des événements sur sept jours.')
    if missing:
        reason += ' Manque : ' + ', '.join(missing)
    return edition, status, ai_usage(provider, errors), reason


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'site')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--repair-dates', default='', help='Dates passées à corriger, séparées par des virgules')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    today = now.astimezone(LONDON).date()
    days = sorted({date.fromisoformat(s.strip()) for s in args.repair_dates.split(',') if s.strip()}) or [today]
    if any(not 0 <= (today - day).days <= 6 for day in days):
        parser.error('Les corrections doivent appartenir aux sept derniers jours, sans date future.')
    validate(args.output)
    provider = None
    feeds = load(ROOT / 'config/sources.json')
    memory = load_memory(args.output)
    exit_code = 0
    for day in days:
        previous = today_edition(args.output, day)
        prior = [*earlier_articles(args.output, day), *previous_topics(memory, day)]
        cleaned, removals = seed_articles(previous, prior)
        if previous and complete(cleaned) and not removals and len(cleaned) == len(previous.articles) and not args.force:
            print(f'Édition {day} déjà complète et conforme. Aucun nouvel appel IA.')
            continue
        if provider is None:
            provider = configured()
        edition, source_status, usage, reason = generate(
            args.output, now, provider, feeds, target_day=day if args.repair_dates else None, memory_sink=memory)
        if edition is None:
            if not args.dry_run:
                health(args.output, now, 'failed', reason, source_status, usage)
            print(reason)
            exit_code = 2
            continue
        if not args.dry_run:
            put(args.output, edition, now)
            retained = {stamp: topics for stamp, topics in memory.items()
                        if 0 <= (today - date.fromisoformat(stamp)).days <= 6}
            atomic(args.output / 'editorial-memory.json', (json.dumps(retained, ensure_ascii=False, indent=2) + '\n').encode())
            health(args.output, now, 'success' if complete(edition.articles) else 'partial', reason, source_status, usage)
            validate(args.output)
        print(f'{"DRY RUN" if args.dry_run else "PUBLISHED"}: {day} — {len(edition.articles)} articles — '
              f'{sum(a.word_count for a in edition.articles)} mots — {edition.minutes} min; AI {usage}', flush=True)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
