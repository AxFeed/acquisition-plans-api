# acquisition-plans-api

API REST qui met à disposition les données de plans techniques ferroviaires : objets des plans, nomenclature des équipements, guide des symboles, bulletins et interventions de maintenance, liste des gares de voyageurs, circuits de voie du réseau ferré national et définitions tirées de Wikipédia. Les huit sources (fichiers JSON et CSV, page HTML locale, flux RSS, API REST publique, base PostgreSQL, fichiers Parquet interrogés avec DuckDB, pages web) sont collectées, contrôlées, nettoyées et stockées dans SQLite, puis exposées par une API FastAPI, avec une recherche par mots-clés, un assistant qui répond avec un modèle de langage local (Ollama), réservé aux porteurs d'un jeton, et des routes d'administration protégées par jeton.

Le dépôt sert surtout à démontrer une chaîne d'intégration et de livraison continues avec GitHub Actions.

## Origine et périmètre

Ce projet est le prolongement personnel d'un travail de stage. Le lecteur de fichiers ILV écrit pendant le stage appartient au projet de l'entreprise : **il n'est pas inclus ici**. Les trois plans de `acquisition_plans/donnees/` sont des plans **fictifs**, décodés par ce lecteur puis exportés en JSON ; la nomenclature, le guide, les bulletins et la base de maintenance PostgreSQL sont fictifs eux aussi. La liste des gares, les circuits de voie et les modes de cantonnement viennent de l'open data de SNCF et de SNCF Réseau (`ressources.data.sncf.com`, licence ODbL) ; les définitions du block automatique lumineux et du circuit de voie, de Wikipédia (licence CC BY-SA 4.0, contributeurs de Wikipédia).

## Organisation

```
acquisition_plans/
├── collecte/      collecte des huit sources, déduplication SHA-256 ; extraction depuis l'API REST
│                  (api_rest.py), la base PostgreSQL (base_de_donnees.py), le big data (big_data.py)
│                  et des pages web (scraping_web.py)
├── stockage/      schéma SQLite (schema.sql) et requêtes (PlanDatabase)
├── recherche/     recherche des extraits par mots-clés
├── generation/    réponse du modèle de langage à partir des extraits (Ollama), avec repli sur les extraits
├── api/           application FastAPI (create_app)
├── surveillance/  métriques HTTP au format Prometheus et journal JSON
├── donnees/       données de démonstration ; instantanés des extractions dans api/, base/, big_data/ et web/ ;
│                  schéma et lignes de la base de maintenance dans base_maintenance/init.sql
└── tests/         contrôle des données, tests automatisés et évaluation de la recherche
.github/workflows/ci-cd.yml   chaîne d'intégration et de livraison continues
supervision/                  Prometheus (collecte, règles d'alerte et leurs tests) et Grafana (tableau de bord)
Dockerfile                    image de l'application
```

## Installation

Prérequis : Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Linux : source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Configuration : variables d'environnement décrites dans `.env.example`. `ADMIN_API_KEY` est le jeton attendu dans l'en-tête `X-Admin-Token` par les routes d'administration ; sans lui, elles refusent toute demande.

## Collecter depuis l'API REST

```powershell
python -B -m acquisition_plans.collecte.api_rest --limite 50
```

Le script appelle `https://ressources.data.sncf.com/api/explore/v2.1/catalog/datasets/gares-de-voyageurs/records`, réessaie trois fois en cas d'erreur réseau ou serveur, s'arrête sur une erreur de requête ou une réponse illisible, et enregistre les enregistrements avec leur provenance (URL, licence, date) dans `acquisition_plans/donnees/api/gares-de-voyageurs.json`. Il renvoie le code 1 si rien n'a été enregistré. La collecte suivante (au démarrage de l'API ou par `/api/ingest`) stocke cet instantané dans SQLite avec les autres sources.

## Collecter depuis la base de données PostgreSQL

La base de maintenance est fictive : `acquisition_plans/donnees/base_maintenance/init.sql` crée deux tables, `equipements` (les équipements des plans) et `interventions` (date, nature préventive ou corrective, statut réalisée, planifiée ou annulée, équipe, durée, compte rendu), deux index et dix interventions. Aucune donnée personnelle : une intervention est rattachée à une équipe. Prérequis : Docker.

```powershell
docker run -d --name base-maintenance -p 5433:5432 -e POSTGRES_DB=maintenance -e POSTGRES_USER=lecteur -e POSTGRES_PASSWORD=<mot_de_passe> -v "${PWD}\acquisition_plans\donnees\base_maintenance:/docker-entrypoint-initdb.d:ro" postgres:17-alpine
$env:MAINTENANCE_DB_URL = "postgresql://lecteur:<mot_de_passe>@127.0.0.1:5433/maintenance"
python -B -m acquisition_plans.collecte.base_de_donnees
```

Le conteneur exécute `init.sql` à son premier démarrage. Le script lit l'adresse de connexion dans `MAINTENANCE_DB_URL` (jamais écrite dans un fichier), ouvre une session en lecture seule, réessaie trois fois si le serveur ne répond pas et s'arrête sur une requête refusée. Il écrit `acquisition_plans/donnees/base/interventions-maintenance.json` avec les lignes, les deux requêtes, leurs paramètres et le plan d'exécution de PostgreSQL ; le code retour est 1 si aucune intervention n'est trouvée. L'option `--depuis AAAA-MM-JJ` change la date de début (par défaut le 1er janvier 2026).

Les deux requêtes sont dans `collecte/base_de_donnees.py` :

- **interventions réalisées** : jointure interne `interventions` → `equipements` pour obtenir le code de l'équipement (`code_type || '#' || numero`), son libellé et son secteur ; filtres sur le statut `realisee` et sur la date (paramètre `%(depuis)s`, jamais concaténé à la requête) ; tri de la plus récente à la plus ancienne ;
- **synthèse par équipement** : jointure externe `equipements` → `interventions`, pour garder un équipement sans intervention ; comptage par statut avec `COUNT(...) FILTER (WHERE ...)` et date de la dernière intervention réalisée, en une seule lecture.

Optimisations : seules les colonnes utiles sont lues, la session est en lecture seule, et l'index `(statut, date_intervention)` sert la première requête. Le plan d'exécution enregistré dans l'instantané le montre : sur la base neuve, PostgreSQL parcourt cet index à l'envers, ce qui donne directement l'ordre des dates décroissantes, puis retrouve chaque équipement par sa clé primaire. La synthèse, elle, lit toutes les interventions : PostgreSQL la calcule par une jointure par hachage et regroupe sur la seule clé primaire de l'équipement. L'index sur la clé étrangère `equipement_id` suit la pratique habituelle pour les jointures, mais ces requêtes, sur dix lignes, ne l'utilisent pas.

## Collecter depuis un système big data (Parquet et DuckDB)

```powershell
python -B -m acquisition_plans.collecte.big_data --limite 10
```

Le script télécharge deux jeux de SNCF Réseau au format **Parquet**, format en colonnes de l'écosystème big data, en ne demandant que les colonnes utiles : les 84 867 circuits de voie du réseau ferré national (`liste-des-circuits-de-voie` : `code_ligne`, `code_voie`) et le mode de cantonnement de chaque ligne (`mode-de-cantonnement-des-lignes` : `code_ligne`, `lib_ligne`, `libelle`). Les fichiers restent dans `donnees/big_data/cache/` (ignoré par Git). **DuckDB**, moteur SQL analytique sans serveur, les interroge ensuite : il compte les circuits et les voies de chaque ligne, joint le nom de la ligne et ses modes de cantonnement, et garde les lignes les plus équipées. Le résultat, la requête, le plan d'exécution et la provenance (adresses, licence, date) sont écrits dans `donnees/big_data/circuits-de-voie-par-ligne.json`.

La requête (`LINES_QUERY` dans `collecte/big_data.py`) agrège d'abord le cantonnement par ligne (`string_agg(DISTINCT ...)`) dans une expression `WITH`, puis fait une jointure externe gauche pour garder les circuits d'une ligne sans cantonnement connu, regroupe par ligne avec `count(*)` et `count(DISTINCT code_voie)`, trie et limite. Optimisations : l'export ne transfère que les colonnes demandées ; DuckDB ne lit dans le fichier Parquet que les colonnes utilisées par la requête (ligne `Projections` du plan) et applique les filtres pendant la lecture ; l'agrégation et la jointure se font en mémoire, en parallèle, sans charger les données dans une base.

## Scraper des pages web

```powershell
python -B -m acquisition_plans.collecte.scraping_web
```

Le script lit d'abord `https://fr.wikipedia.org/robots.txt` et s'arrête si une page n'y est pas autorisée ; il s'identifie par un nom de client générique (`collecte-pedagogique/1.0`), attend une seconde entre deux pages et réessaie trois fois en cas d'erreur réseau ou serveur. De chaque page (« Block automatique lumineux », « Circuit de voie »), il garde l'introduction : les paragraphes écrits directement dans l'article, avant le premier titre de section, sans les bandeaux, les tableaux, les légendes ni les appels de note. Le résultat, avec l'adresse, le titre, la licence (CC BY-SA 4.0) et la date, est écrit dans `acquisition_plans/donnees/web/pages-wikipedia.json` ; chaque page devient un document de la base. Le code retour est 1 si une page est interdite, introuvable ou sans introduction.

## Lancer l'API

```powershell
$env:ADMIN_API_KEY = "<jeton>"
python -B -m uvicorn acquisition_plans.api.app:app --host 127.0.0.1 --port 8000
```

Au premier démarrage, la base est créée et les données de `donnees/` sont collectées. Documentation OpenAPI : http://127.0.0.1:8000/docs.

| Route | Accès | Rôle |
|---|---|---|
| `GET /health` | public | état du service et nombre de documents |
| `GET /metrics` | public | compteurs HTTP au format Prometheus |
| `GET /api/plans` | public | résumé de chaque plan : objets, groupes, objets sans position |
| `POST /api/search` | public | extraits les plus proches d'une requête (`query` de 2 à 300 caractères, `top_k` de 1 à 10) |
| `POST /api/chat` | jeton utilisateur | réponse du modèle à une question (`question` de 2 à 500 caractères), avec ses sources, ses durées et `used_model` |
| `GET /api/sources` | jeton | documents et extraits par type de source, contrôle d'intégrité |
| `POST /api/ingest` | jeton | relance la collecte ; un document déjà connu n'est pas réenregistré |

Sans jeton, les routes d'administration répondent 401 ; avec un mauvais jeton, 403.

### Le modèle de langage

`POST /api/chat` exige l'en-tête `Authorization: Bearer <jeton>`, comparé en temps constant à la variable `CHAT_API_KEY` : 401 sans jeton (ou si la variable est vide), 403 avec un mauvais jeton. La documentation OpenAPI (`/docs`) décrit ce schéma d'authentification et permet d'essayer la route avec le bouton *Authorize*.

La route cherche les quatre meilleurs extraits, puis demande la réponse au modèle servi par Ollama (`OLLAMA_BASE_URL`, par défaut `http://127.0.0.1:11434` ; modèle `OLLAMA_CHAT_MODEL`, par défaut `qwen3:4b-instruct-2507-q4_K_M`). Le message système impose de répondre en français, à partir des seuls extraits, en les citant ([1]), et de refuser quand l'information manque. La génération est réglée pour être reproductible : température 0, graine fixe, 220 jetons au plus. Sans extrait, la route renvoie le refus sans appeler le modèle ; si Ollama ne répond pas, ou pas avec le modèle demandé, elle renvoie les deux meilleurs extraits avec `used_model: false`, et le journal note la cause.

```powershell
ollama pull qwen3:4b-instruct-2507-q4_K_M
$env:CHAT_API_KEY = "<jeton utilisateur>"
python -B -m uvicorn acquisition_plans.api.app:app --host 127.0.0.1 --port 8000
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat -Headers @{ Authorization = "Bearer <jeton utilisateur>" } -ContentType "application/json" -Body '{"question": "Quelle est la position du sectionneur EQUIP_A#101 ?"}'
```

Dans un conteneur, Ollama tourne sur la machine hôte : ajouter `-e OLLAMA_BASE_URL=http://host.docker.internal:11434 -e CHAT_API_KEY=<jeton utilisateur>` à `docker run`.

Sur `/metrics`, `ilv_http_errors_total` compte les erreurs du service (codes 5xx, y compris une exception non gérée, renvoyée en 500 avec un message générique et journalisée avec son type) ; `ilv_http_client_errors_total` compte à part les requêtes refusées (codes 4xx : jeton absent ou faux, saisie invalide, route inconnue comme `/favicon.ico` demandée par un navigateur). Pour le modèle : `ilv_chat_requests_total` (questions), `ilv_chat_fallback_total` (réponses données sans le modèle) et `ilv_llm_duration_seconds_sum` (durée cumulée de génération). Le journal ajoute pour chaque question une ligne `chat`, avec le même identifiant que la ligne HTTP, le modèle, `used_model`, le nombre de sources et les durées, sans le texte de la question.

## Environnement de recette

L'image publiée sur GitHub Container Registry tourne aussi chez un hébergeur, Render (offre gratuite), comme environnement de recette : une adresse publique, hors de toute machine de développement, avec la même image que celle livrée. L'image écoute sur le port donné par la variable `PORT` (8000 par défaut), comme l'attendent les hébergeurs. Les jetons `ADMIN_API_KEY` et `CHAT_API_KEY` sont définis dans les paramètres du service, jamais dans le dépôt.

À chaque livraison sur `main`, le dernier pas du job `livraison` appelle le crochet de déploiement de Render, rangé dans le secret `RENDER_DEPLOY_HOOK` du dépôt : Render récupère alors la nouvelle image. Sans ce secret, le pas est ignoré. Un redéploiement peut aussi être lancé à la main (onglet *Actions*, *Run workflow* sur `main`).

Le modèle de langage n'est pas hébergé en recette (il demande Ollama et plusieurs gigaoctets de mémoire) : `/api/chat` y répond avec les extraits seuls (`used_model: false`), ce que compte `ilv_chat_fallback_total`. L'instance gratuite se met en veille après un moment sans visite : la première requête qui suit prend alors une minute environ.

## Supervision : Prometheus, alertes et Grafana

Le dossier `supervision/` lance l'API, Prometheus et Grafana en conteneurs, d'une seule commande. Les jetons et le mot de passe de Grafana sont lus dans l'environnement : aucune valeur par défaut n'est écrite dans le dépôt.

```powershell
$env:ADMIN_API_KEY = "<jeton administrateur>"
$env:CHAT_API_KEY = "<jeton utilisateur>"
$env:GRAFANA_ADMIN_PASSWORD = "<mot de passe Grafana>"
docker compose -f supervision/docker-compose.yml up -d --build
```

- API : http://127.0.0.1:8001 (elle appelle l'Ollama de la machine hôte) ;
- Prometheus : http://localhost:9090 (*Status*, *Targets* : la cible doit être `UP` ; *Alerts* : les quatre règles) ;
- Grafana : http://localhost:3000, compte `admin`, tableau « acquisition-plans-api : API et modèle de langage ».

Prometheus collecte `/metrics` toutes les 15 secondes (`supervision/prometheus/prometheus.yml`) et évalue quatre règles (`alertes.yml`) :

| Alerte | Condition | Gravité |
|---|---|---|
| `ApiIndisponible` | la cible ne répond plus pendant 1 minute | critique |
| `TauxErreursServeur` | plus de 5 % d'erreurs 5xx sur 15 minutes, pendant 5 minutes | avertissement |
| `ReponsesSansModele` | au moins une réponse donnée sans le modèle en 15 minutes | avertissement |
| `GenerationLente` | génération moyenne au-delà de 25 s sur 15 minutes, pendant 5 minutes | avertissement |

Les règles sont testées sans rien lancer, sur des séries simulées minute par minute (`tests_alertes.yml`) :

```powershell
docker run --rm --entrypoint promtool -w /etc/prometheus -v "${PWD}\supervision\prometheus:/etc/prometheus:ro" prom/prometheus:v3.5.0 test rules tests_alertes.yml
```

Le tableau de bord Grafana (`supervision/grafana/dashboards/acquisition-plans-api.json`, chargé au démarrage) compte huit panneaux : disponibilité de l'API, questions, réponses sans le modèle et refus 4xx sur une heure, requêtes par minute, taux d'erreurs 5xx, latence de génération et latence HTTP. Chaque titre donne la fenêtre de calcul, chaque panneau son unité et une description qui rappelle le seuil d'alerte ; les seuils sont tracés en orange sur les courbes. Le test `tests/test_supervision_config.py` vérifie que le tableau de bord et les règles n'utilisent que des compteurs réellement exposés par l'API.

Pour voir une alerte se déclencher : `docker compose -f supervision/docker-compose.yml stop api` ; `ApiIndisponible` passe à « pending », puis à « firing » au bout d'une minute. Pour tout arrêter : `docker compose -f supervision/docker-compose.yml down`.

## Suivi des incidents

Un incident est décrit dans un ticket de l'onglet *Issues* (étiquette `bug`) : symptôme, reproduction, diagnostic. La correction est développée sur une branche avec un test qui reproduit le défaut, puis fusionnée par une pull request qui ferme le ticket après le passage de la chaîne.

## Contrôles et tests en local

Depuis la racine du dépôt, les mêmes commandes que la chaîne :

```powershell
python -B -m ruff check --select E,F,B,SIM,C4,ARG acquisition_plans
python -B -m black --check acquisition_plans
python -B -m acquisition_plans.tests.valider_donnees
python -B -m coverage run --source=acquisition_plans --omit="*/tests/*" -m unittest discover -s acquisition_plans/tests -p "test_*.py" -v
python -B -m coverage report
python -B -m acquisition_plans.tests.evaluer_recherche
python -B -m acquisition_plans.tests.evaluer_modele
```

Le test d'intégration de la base (`tests/test_base_de_donnees.py`) interroge la vraie base quand `MAINTENANCE_DB_URL` est définie (conteneur lancé comme plus haut) ; sinon il est ignoré et les autres tests utilisent une connexion simulée.

La dernière commande pose les treize questions de `tests/questions_reference.json` à la recherche, dont une par source extraite (base de maintenance, big data, API, pages web).

`evaluer_modele` pose les mêmes questions au modèle (Ollama lancé, modèle téléchargé). Une question est réussie si la réponse vient du modèle et contient elle-même chaque fait attendu (`expected_answer`), par exemple les coordonnées `140` et `280` ; pour la question hors corpus, le fait attendu est le refus. Seuils : toutes les réponses viennent du modèle, la question hors corpus est refusée, au moins 80 % des autres réponses contiennent leurs faits. Le rapport `tests/rapport_validation_modele.json` garde le modèle, la version d'Ollama, chaque réponse et sa durée. Les tests automatisés, eux, remplacent Ollama par un faux serveur. Elle vérifie que la source attendue figure dans les quatre premiers résultats (taux minimal 87,5 %), que le rang réciproque moyen atteint 0,6 et qu'aucun résultat ne dépasse un score de 0,5 pour la question hors corpus ; elle écrit `tests/rapport_evaluation_recherche.json`.

## Chaîne d'intégration et de livraison continues

Le fichier `.github/workflows/ci-cd.yml` est lu par GitHub Actions. La publication utilise le jeton `GITHUB_TOKEN` fourni par GitHub ; le seul secret à créer, facultatif, est `RENDER_DEPLOY_HOOK` (environnement de recette).

**Déclencheurs** : envoi sur `main`, pull request vers `main`, lancement manuel (onglet *Actions*, *Run workflow*). La livraison ne s'exécute que sur `main`, après un envoi ou un lancement manuel.

| Job | Dépend de | Ce qu'il fait | Échoue si |
|---|---|---|---|
| `qualite` | — | Ruff (règles E, F, B, SIM, C4, ARG) et Black | une règle de style n'est pas respectée |
| `supervision` | — | `promtool` : configuration de Prometheus valide et tests des quatre règles d'alerte sur des séries simulées | la configuration est invalide ou une alerte ne se déclenche pas comme prévu |
| `donnees` | — | Contrôle des fichiers de `donnees/` : plans JSON (champs, positions, identifiants uniques), colonnes du CSV, articles du guide HTML, bulletins RSS, instantanés de l'API, de la base, du big data (champs, totaux cohérents) et des pages web (licence, adresses, paragraphes) | une donnée source est invalide |
| `tests` | `qualite`, `donnees`, `supervision` | Lancement de la base de maintenance dans un conteneur PostgreSQL, puis tests unitaires et d'intégration (dont les vraies requêtes SQL sur cette base), mesure de couverture ; rapport HTML publié en artefact | la base ne démarre pas, un test échoue ou la couverture est sous 85 % |
| `evaluation` | `tests` | Questions de référence posées à la recherche ; rapport JSON publié en artefact | un seuil n'est pas atteint |
| `validation_modele` | `evaluation` | Ollama 0.40.16 dans un conteneur de service, téléchargement du modèle, questions de référence posées au modèle ; rapport JSON publié en artefact | un seuil n'est pas atteint |
| `livraison` | `tests`, `evaluation`, `validation_modele` | Seulement sur `main` (envoi ou lancement manuel) : construction de l'image, publication sur GitHub Container Registry (étiquettes `<sha>` et `latest`), lancement de l'image publiée et appel de `/health`, puis demande de déploiement à l'environnement de recette si le secret `RENDER_DEPLOY_HOOK` existe | la construction, la publication, le contrôle de santé ou la demande de déploiement échoue |

**Installer la chaîne** : créer le dépôt sur GitHub et y envoyer ce contenu ; GitHub Actions détecte le workflow sans autre configuration. Les artefacts sont téléchargeables sur la page de chaque exécution.

**Tester la chaîne** :

1. Envoyer un commit sur une branche et ouvrir une pull request vers `main` : `qualite`, `donnees`, `supervision`, `tests`, `evaluation` et `validation_modele` s'exécutent, la livraison est ignorée.
2. Fusionner la pull request : les sept jobs s'exécutent et l'image apparaît dans l'onglet *Packages* du compte.
3. Vérifier un échec volontaire : sur une branche, supprimer la colonne `description` de `donnees/nomenclature_equipements.csv` ; le job `donnees` échoue et bloque les tests.

**Récupérer l'image livrée** (le paquet doit être rendu public dans ses paramètres, *Package settings*, *Change visibility*, sinon un `docker login ghcr.io` est nécessaire) :

```powershell
docker pull ghcr.io/<compte>/acquisition-plans-api:latest
docker run -d --name acq-plans-api -p 8000:8000 -e ADMIN_API_KEY=<jeton> ghcr.io/<compte>/acquisition-plans-api:latest
```

## Données personnelles

Les données sont techniques : plans, documents et base de maintenance fictifs, liste publique des gares, circuits de voie et cantonnement du réseau. Le journal JSON enregistre la méthode, la route, le code de réponse et la durée de chaque requête, sans le contenu des requêtes ni l'adresse IP.

## Limites

- L'environnement de recette est une instance gratuite, sans le modèle de langage ; il n'y a pas d'environnement de production.
- La recherche est lexicale : elle ne comprend ni les synonymes ni les accents, et des textes longs qui répètent des mots courts peuvent passer devant le bon document (question Q02 depuis l'ajout des interventions).
- Le modèle tourne sur processeur : quelques secondes par réponse sur un poste, davantage sur les machines de GitHub. La validation porte sur treize questions, ce qui reste un petit échantillon.
- Les extractions (API, base, big data, pages web) se lancent à la main ; leurs instantanés sont versionnés et contrôlés par la chaîne, mais aucune collecte n'est planifiée.
- Le volume big data reste modeste (84 867 circuits de voie) : la technique, format en colonnes et moteur analytique, est celle qui passerait à l'échelle, sur une seule machine.
- Le lecteur ILV et les plans réels ne sont pas dans ce dépôt.
