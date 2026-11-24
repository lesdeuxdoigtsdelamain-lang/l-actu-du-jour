# L'actu du jour — journal personnel Android en français

**Version 1.0.2 — Samsung Galaxy A56, Android 9+.**

Une application de lecture volontairement sobre : cartes d'actualité illustrées, articles longs rédigés en français, consultation des sources, mode lecture plein écran, polices ajustables, trois thèmes, marquage des articles lus, et archive hors ligne de sept jours. **Sans publicité, compte, quiz, API ni analyse de données embarqués dans l'APK.**

## Architecture

- `android/` — application native Kotlin / Jetpack Compose ; lecture locale d'éditions statiques JSON validées par SHA-256.
- `generator/app/` — collecte RSS/Atom en français, arabe et anglais ; extraction du contenu public lorsque autorisée ; regroupement conservateur des informations et anti-doublons ; génération Groq (Cloudflare en secours) avec preuves, contrôles et comparaison des événements publiés sur les sept derniers jours.
- `config/sources.json` — registre éditable des flux. Les échecs d'accès et les flux non vérifiés ne sont jamais cachés ; voir `site/health.json`.
- `site/` — données publiques sur GitHub Pages ; **aucune information privée**.
- `.github/workflows/` — publication automatisée du journal à 17:05 (Europe/London) avec seconde tentative à 18:20, compilation signée.
- `schema/edition.schema.json` — format public strict des éditions.
- `generator/app/extras.py` et `schema/extras.schema.json` — compléments facultatifs et indépendants des articles : alertes graves et rubrique historique courte, publiés dans `site/extras.json`.

## Urgences et « Ce jour-là » — version 1.0.2

- Les **alertes graves récentes** apparaissent en rouge tout en haut du fil, y compris pendant la consultation d'une archive. La sélection est prudente : événement grave, sources concordantes, exclusion des anniversaires, procès, menaces et exercices. Elle repose sur les flux français configurés et peut manquer un événement ou se tromper ; elle ne garantit pas une couverture exhaustive.
- Une vérification des flux est programmée **toutes les quinze minutes**, sans appel Groq ni Cloudflare. L'application consulte le résultat à l'ouverture, à l'actualisation manuelle et toutes les soixante secondes lorsqu'elle est visible. Les alertes périmées disparaissent aussi hors ligne. Les tâches GitHub peuvent être retardées : ce dispositif n'est pas un service d'alerte instantanée.
- **« Ce jour-là »** est un encadré après les articles : même jour et même mois que l'édition, année passée, six à dix courtes phrases, date historique et sources consultables. La longueur affichée dépend de l'écran et de la taille de police. L'encadré ne remplace aucun article et ne compte pas dans l'objectif de lecture des six à huit articles.
- L'historique est conservé sept jours. Une note déjà produite est réutilisée ; une tentative de génération historique au maximum est autorisée par jour. Si les preuves ou le fournisseur manquent, aucune anecdote n'est inventée pour remplir l'encadré.
- Les lecteurs **1.0.1** continuent de recevoir le journal habituel. Installer l'APK **1.0.2** est nécessaire pour voir les nouveaux encadrés. Le format des éditions et des manifestes existants reste inchangé.

## Comprendre les limites de génération

Le diagnostic public `health.json` distingue les rejets éditoriaux, les plafonds locaux et les erreurs des fournisseurs. `extras-health.json` suit séparément les compléments. Aucun de ces fichiers ne contient de clé, de corps de requête ou d'extrait privé de source.

La rédaction utilise `openai/gpt-oss-120b` chez Groq, avec **18 tentatives maximum et un seuil local de 65 000 tokens consommés** par exécution. Le contrôle s'effectue entre les appels : une réponse peut dépasser légèrement le seuil avant l'arrêt suivant. Ces protections logicielles sont distinctes des quotas du compte. Un grand nombre de textes refusés peut donc produire une édition partielle sans aucune erreur de quota de Groq.

Groq applique ses limites à l'organisation : **une deuxième clé de la même organisation n'ajoute pas de quota**. Les limites exactes sont à consulter dans le compte ; la documentation publique indique actuellement 30 requêtes/minute, 1 000/jour, 8 000 tokens/minute et 200 000/jour pour le modèle utilisé. Voir [la documentation officielle Groq](https://console.groq.com/docs/rate-limits). Un HTTP 429 est enregistré comme une limite fournisseur, même si la courte reprise a réussi. Aucun passage automatique à une offre payante n'est prévu.

## Déploiement

Lire **[docs/INSTALLATION_FR.md](docs/INSTALLATION_FR.md)** avant d'activer les automatisations.

### Secrets GitHub

- `GROQ_API_KEY` : clé du fournisseur de rédaction principal, stockée uniquement dans les secrets GitHub Actions.
- `CLOUDFLARE_ACCOUNT_ID` et `CLOUDFLARE_API_TOKEN` : à recopier dans le **nouveau dépôt** depuis vos propres valeurs Cloudflare.
- `LADJ_KEYSTORE_BASE64`, `LADJ_STORE_PASSWORD`, `LADJ_KEY_ALIAS`, `LADJ_KEY_PASSWORD` : fournis séparément dans le ZIP privé. **Ne jamais commiter ou transmettre ces secrets**.

Le dépôt doit s'appeler exactement **`l-actu-du-jour`** sous le propriétaire `lesdeuxdoigtsdelamain-lang` pour que l'URL de GitHub Pages préconfigurée soit correcte. Si le nom ou le propriétaire change, mettre à jour `BuildConfig.PAGES_URL` dans `android/app/build.gradle.kts` avant de compiler.

## Journalisme : règles et limites

- Objectif **6–8 articles par jour**, environ 20 minutes calculées à partir du texte réel. Les nouveaux articles approfondis comptent 450–650 mots.
- Chaque édition complète comprend **un article consacré au Royaume-Uni** et **un unique Tour de l’actu en France de 300–400 mots**, regroupant trois ou quatre sujets français du jour.
- **Au maximum un sujet principalement consacré au Maroc**, quelle que soit sa rubrique. Aucun sujet Maroc obligatoire : les autres actualités peuvent prendre sa place.
- Contrôle des doublons par URL et par rapprochement des titres et textes français, pendant la sélection et après rédaction, dans la même édition et sur les sept jours précédents. Les sujets du tour de France sont mémorisés individuellement. Ce rapprochement automatique reste imparfait. Aucun remplissage artificiel.
- Des articles peuvent manquer si les sources ou le quota gratuit ne permettent pas une édition complète. **Jamais de faux contenu pour compléter une édition** : toute édition ne satisfaisant pas le nombre, la durée et les rubriques requises est marquée *partielle*. Une relance complète ou corrige les articles existants sans cumuler plusieurs versions du même événement.
- Depuis la version Android **1.0.1**, le manifeste séparé `archive.json` permet de récupérer les corrections des sept jours conservés, même lorsque la dernière édition n’a pas changé. Les vérifications de date, de format et SHA-256 restent obligatoires ; un échec conserve la copie locale saine.
- Pour les médias, des synthèses **originales** sont produites à partir de faits accessibles publiquement ; l'application ne reproduit pas le texte intégral ou les photos des médias protégés. Les articles et liens originaux restent cités.
- Les articles publiés restent du contenu produit automatiquement : les filtres vérifient preuves citées, longueur, chiffres et provenance, **mais n'assurent pas une vérification humaine ou une exactitude parfaite**. La traduction depuis l'arabe peut contenir des erreurs ; le lecteur peut consulter les sources à la fin.
- Il n'est pas garanti que chaque source réponde chaque jour. Les URL de flux changent, peuvent être bloquées, ou faire l'objet de restrictions contractuelles : **respecter les conditions et robots.txt des éditeurs** ; ne pas contourner un accès payant.
- Photographies uniquement avec attribution vérifiée sur Wikimedia Commons ; quand aucune photo réutilisable n'est disponible, la carte affiche automatiquement une **illustration de rubrique incluse dans l'APK**. Aucune photo de presse copiée.
- Coût récurrent cible : **0 £** tant que GitHub Actions / Pages et Workers AI restent disponibles dans leurs quotas gratuits. Aucun appel automatique payant ; quota modèle plafonné, échec sûr si les limites sont atteintes. Pour éviter toute facturation, rester sur Cloudflare Workers Free et désactiver les offres payantes.
- Publication visée à 17 h au Royaume-Uni, avec notification quand une véritable édition du jour est téléchargée. Les exécutions GitHub et les tâches Android peuvent être retardées ; **aucune garantie à la minute**.

## Tests reproductibles

```bash
python -m pip install -r generator/requirements.lock
python -m pytest -q generator/tests
python -m generator.app.verify_site site
# Nécessite GROQ_API_KEY (ou les secrets Cloudflare) pour un vrai journal :
python -m generator.app.main --dry-run
```

Compilation via GitHub Actions : **Compiler l'APK signé**. Les tests Python ont été exécutés localement ; une compilation Android et des tests d'édition réelle nécessitent les runners GitHub et les secrets. Aucune édition fictive n'est prépubliée.
