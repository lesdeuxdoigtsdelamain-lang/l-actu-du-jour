# Installer L'actu du jour — guide simple

## 1. Créer le nouveau dépôt GitHub

Créer un **nouveau dépôt public distinct de My Daily World** sur https://github.com/new, avec :

- Propriétaire : `lesdeuxdoigtsdelamain-lang`
- Nom exact : **`l-actu-du-jour`**
- Visibilité : **Public**
- Ne pas mettre la clé privée dans ce dépôt.

Les fichiers du ZIP source doivent apparaître **décompressés** à la racine du dépôt : `android/`, `generator/`, `config/`, `.github/` etc. **Ne pas déposer le ZIP comme un fichier unique**. Un client Git (GitHub Desktop) peut publier le dossier extrait. L'assistant ChatGPT peut également déposer les fichiers par GitHub après que son accès au nouveau dépôt a été autorisé.

## 2. Activer Pages

Dans le nouveau dépôt : **Settings → Pages → Build and deployment → Source : GitHub Actions**.

L'URL configurée dans l'APK est :
`https://lesdeuxdoigtsdelamain-lang.github.io/l-actu-du-jour/`.

## 3. Ajouter les secrets — à faire une seule fois

Dans le nouveau dépôt : **Settings → Secrets and variables → Actions → New repository secret**.

Ajoute les **six** secrets suivants :

1. `CLOUDFLARE_ACCOUNT_ID` : même valeur que pour My Daily World.
2. `CLOUDFLARE_API_TOKEN` : même valeur existante pour Workers AI.
3. `LADJ_KEYSTORE_BASE64` : contenu du fichier ZIP privé `L-Actu-du-jour-CLE-PRIVEE.zip`.
4. `LADJ_STORE_PASSWORD` : même fichier privé.
5. `LADJ_KEY_ALIAS` : même fichier privé.
6. `LADJ_KEY_PASSWORD` : même fichier privé.

Ne mets jamais ces valeurs dans un message, une capture d'écran ou un dépôt public. Le ZIP privé doit rester en lieu sûr, hors GitHub. Il est **distinct de la clé de My Daily World**.

## 4. Compiler et installer sur ton Samsung Galaxy A56

Dans le nouveau dépôt : **Actions → Compiler l'APK signé → Run workflow → main**. Quand l'exécution devient verte, ouvre-la, descends jusqu'à **Artifacts**, télécharge **`L-actu-du-jour-SIGNED`** et extrais `L-actu-du-jour.apk`.

Envoie uniquement l'APK via WeTransfer/USB, puis installe-le sur le Galaxy A56. Il s'agit d'une nouvelle application avec son propre identifiant Android ; **ne désinstalle pas My Daily World**.

Ne télécharge **pas** `app-debug.apk` pour usage quotidien ; les debug sont signés temporairement. Les versions futures seront compilées avec la même clé privée et un `versionCode` plus élevé.

## 5. Publier ton premier vrai journal

Dans **Actions → Publier le journal quotidien → Run workflow**, sélectionne `main` et coche `force_refresh` pour remplacer l'édition du jour si nécessaire. L'exécution doit générer de vrais articles à partir de sources récupérées et d'un compte Cloudflare Free configuré.

Si le workflow refuse une édition, il explique dans les logs s'il manque des sources, si les articles sont trop courts, si Workers AI échoue ou si le quota est atteint. Il est **normal de ne pas publier** si les faits fiables ne permettent pas d'écrire des articles suffisamment longs.

Une fois **Publish Daily** vert, ouvre l'application et appuie sur l'icône d'actualisation. Les articles seront téléchargés automatiquement et consultables sans connexion pendant sept jours.

## Signatures, notifications, budget

- Toutes les futures compilations doivent utiliser **exactement les mêmes quatre secrets `LADJ_*`**. Garde deux sauvegardes sécurisées du ZIP privé.
- Active les notifications lorsque Android le propose. Android et GitHub peuvent retarder la publication au-delà de 17 h ; la notification n'est envoyée que lorsque le journal daté du jour est téléchargé par la tâche de fond.
- Garder **Cloudflare Workers Free**, sans abonnement, et surveiller le quota commun à toutes les applications du compte (L'actu du jour + My Daily World).
- Les APK et les articles sont distribués gratuitement via GitHub, sous réserve du respect des quotas et conditions de leurs services.
