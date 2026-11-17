# Contrôles de qualité et limites connues

## Contrôles implémentés

- Analyse de flux RSS / Atom en français, arabe et anglais, publication datée, refus des URL hors liste.
- Filtrage des thèmes non souhaités (expositions, people, mode, cinéma, etc.).
- Déduplication d'article sur URL/identifiant et comparaison de titres, archive de sept jours.
- Extraction de paragraphes de pages publiquement accessibles, sans contourner les accès, dans la limite des robots.txt ; source non accessible = source ignorée.
- Rédaction originale Cloudflare Workers AI, JSON structuré avec identifiants des preuves, refus si moins de 280 mots, paragraphes trop courts, chiffres absents des sources ou absence de références.
- Source(s) cliquable(s) à la fin de chaque article ; photos limitées aux fichiers Commons sous licence compatible, sinon image neutre locale.
- Contrat JSON strict, manifeste SHA-256, stockage hors ligne, archives de sept jours, lecture plein écran, trois thèmes et police réglable.
- Secrets uniquement dans GitHub Actions ; pas de jeton dans l'APK, dans les éditions JSON ni dans les logs.
- APK release signé en permanence, aucun artifact debug destiné à l'installation finale.

## Contrôles à exécuter sur le vrai déploiement

- Compilation Android sur GitHub Actions, installation sur Galaxy A56.
- Test sur le réseau réel de chaque flux RSS : certains flux peuvent être bloqués ou ne plus exister. Le journal ne doit jamais prétendre avoir consulté une source indisponible.
- Test Cloudflare avec les identifiants privés (non accessibles ici), nombre d'articles acceptés, nombre de mots réels, contenu des actualités arabophones et marocaines.
- Test photos Wikimedia Commons et illustration de secours en mode hors ligne.
- Test notification à l'heure britannique lors des changements d'heure d'été et d'hiver. WorkManager n'assure pas une minute précise.

## Limites importantes

Le système est **automatique** : une vérification humaine de tous les faits n'est pas disponible. Les validations automatiques ne garantissent pas l'absence d'hallucinations ou de traductions erronées. Les synthèses doivent être lues avec esprit critique, surtout dans les conflits. Aucune source n'est considérée comme infaillible.

Les médias peuvent révoquer un flux, modifier leurs conditions, interdire certains usages automatisés ou limiter leurs robots. La liste des sources n'est pas une garantie qu'elles fourniront chacune des articles tous les jours.

Une édition de six à huit longs articles n'est possible **que si** suffisamment de faits vérifiables sont disponibles et si la capacité gratuite de Workers AI le permet. L'application n'affichera pas de faux articles, et la dernière édition correctement datée restera accessible si aucune édition nouvelle ne peut être publiée.
