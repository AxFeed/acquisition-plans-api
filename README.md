# acquisition-plans-api

API REST qui met à disposition les données de plans techniques ferroviaires : objets des plans, nomenclature des équipements, guide des symboles, bulletins et interventions de maintenance, liste des gares de voyageurs et circuits de voie du réseau ferré national. Les sept sources (fichiers JSON et CSV, page HTML, flux RSS, API REST publique, base PostgreSQL, fichiers Parquet interrogés avec DuckDB) sont collectées, contrôlées, nettoyées et stockées dans SQLite, puis exposées par une API FastAPI, avec une recherche par mots-clés et des routes d'administration protégées par jeton.

Le dépôt sert surtout à démontrer une chaîne d'intégration et de livraison continues avec GitHub Actions.

## Origine et périmètre

Ce projet est le prolongement personnel d'un travail de stage. Le lecteur de fichiers ILV écrit pendant le stage appartient au projet de l'entreprise : **il n'est pas inclus ici**. Les trois plans de `acquisition_plans/donnees/` sont des plans **fictifs**, décodés par ce lecteur puis exportés en JSON ; la nomenclature, le guide, les bulletins et la base de maintenance PostgreSQL sont fictifs eux aussi. La liste des gares, les circuits de voie et les modes de cantonnement viennent de l'open data de SNCF et de SNCF Réseau (`ressources.data.sncf.com`, licence ODbL).

## Organisation

```
acquisition_plans/
├── collecte/      collecte des sept sources, déduplication SHA-256 ; extraction depuis l'API REST
│                  (api_rest.py), la base PostgreSQL (base_de_donnees.py) et le big data (big_data.py)
├── stockage/      schéma SQLite (schema.sql) et requêtes (PlanDatabase)
├── recherche/     recherche des extraits par mots-clés
├── api/           application FastAPI (create_app)
├── surveillance/  métriques HTTP au format Prometheus et journal JSON
├── donnees/       données de démonstration ; instantanés des extractions dans api/, base/ et big_data/ ;
│                  schéma et lignes de la base de maintenance dans base_maintenance/init.sql
└── tests/         contrôle des données, tests automatisés et évaluation de la recherche
.github/workflows/ci-cd.yml   chaîne d'intégration et de livraison continues
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
| `GET /api/sources` | jeton | documents et extraits par type de source, contrôle d'intégrité |
| `POST /api/ingest` | jeton | relance la collecte ; un document déjà connu n'est pas réenregistré |

Sans jeton, les routes d'administration répondent 401 ; avec un mauvais jeton, 403.

Sur `/metrics`, `ilv_http_errors_total` compte les erreurs du service (codes 5xx, y compris une exception non gérée, renvoyée en 500 avec un message générique et journalisée avec son type) ; `ilv_http_client_errors_total` compte à part les requêtes refusées (codes 4xx : jeton absent ou faux, saisie invalide, route inconnue comme `/favicon.ico` demandée par un navigateur).

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
```

Le test d'intégration de la base (`tests/test_base_de_donnees.py`) interroge la vraie base quand `MAINTENANCE_DB_URL` est définie (conteneur lancé comme plus haut) ; sinon il est ignoré et les autres tests utilisent une connexion simulée.

La dernière commande pose les douze questions de `tests/questions_reference.json` à la recherche, dont une par source extraite (base de maintenance, big data, API). Elle vérifie que la source attendue figure dans les quatre premiers résultats (taux minimal 87,5 %), que le rang réciproque moyen atteint 0,6 et qu'aucun résultat ne dépasse un score de 0,5 pour la question hors corpus ; elle écrit `tests/rapport_evaluation_recherche.json`.

## Chaîne d'intégration et de livraison continues

Le fichier `.github/workflows/ci-cd.yml` est lu par GitHub Actions. Aucun secret n'est à créer : la publication utilise le jeton `GITHUB_TOKEN` fourni par GitHub.

**Déclencheurs** : envoi sur `main`, pull request vers `main`, lancement manuel (onglet *Actions*, *Run workflow*).

| Job | Dépend de | Ce qu'il fait | Échoue si |
|---|---|---|---|
| `qualite` | — | Ruff (règles E, F, B, SIM, C4, ARG) et Black | une règle de style n'est pas respectée |
| `donnees` | — | Contrôle des fichiers de `donnees/` : plans JSON (champs, positions, identifiants uniques), colonnes du CSV, articles du guide HTML, bulletins RSS, instantanés de l'API, de la base et du big data (champs, totaux cohérents) | une donnée source est invalide |
| `tests` | `qualite`, `donnees` | Lancement de la base de maintenance dans un conteneur PostgreSQL, puis tests unitaires et d'intégration (dont les vraies requêtes SQL sur cette base), mesure de couverture ; rapport HTML publié en artefact | la base ne démarre pas, un test échoue ou la couverture est sous 85 % |
| `evaluation` | `tests` | Questions de référence posées à la recherche ; rapport JSON publié en artefact | un seuil n'est pas atteint |
| `livraison` | `tests`, `evaluation` | Seulement sur un envoi vers `main` : construction de l'image, publication sur GitHub Container Registry (étiquettes `<sha>` et `latest`), lancement de l'image publiée et appel de `/health` | la construction, la publication ou le contrôle de santé échoue |

**Installer la chaîne** : créer le dépôt sur GitHub et y envoyer ce contenu ; GitHub Actions détecte le workflow sans autre configuration. Les artefacts sont téléchargeables sur la page de chaque exécution.

**Tester la chaîne** :

1. Envoyer un commit sur une branche et ouvrir une pull request vers `main` : `qualite`, `donnees`, `tests` et `evaluation` s'exécutent, la livraison est ignorée.
2. Fusionner la pull request : les cinq jobs s'exécutent et l'image apparaît dans l'onglet *Packages* du compte.
3. Vérifier un échec volontaire : sur une branche, supprimer la colonne `description` de `donnees/nomenclature_equipements.csv` ; le job `donnees` échoue et bloque les tests.

**Récupérer l'image livrée** (le paquet doit être rendu public dans ses paramètres, *Package settings*, *Change visibility*, sinon un `docker login ghcr.io` est nécessaire) :

```powershell
docker pull ghcr.io/<compte>/acquisition-plans-api:latest
docker run -d --name acq-plans-api -p 8000:8000 -e ADMIN_API_KEY=<jeton> ghcr.io/<compte>/acquisition-plans-api:latest
```

## Données personnelles

Les données sont techniques : plans, documents et base de maintenance fictifs, liste publique des gares, circuits de voie et cantonnement du réseau. Le journal JSON enregistre la méthode, la route, le code de réponse et la durée de chaque requête, sans le contenu des requêtes ni l'adresse IP.

## Limites

- La chaîne livre une image dans un registre ; aucun serveur de recette n'est déployé.
- La recherche est lexicale : elle ne comprend ni les synonymes ni les accents, et des textes longs qui répètent des mots courts peuvent passer devant le bon document (question Q02 depuis l'ajout des interventions).
- Les extractions (API, base, big data) se lancent à la main ; leurs instantanés sont versionnés et contrôlés par la chaîne, mais aucune collecte n'est planifiée.
- Le volume big data reste modeste (84 867 circuits de voie) : la technique, format en colonnes et moteur analytique, est celle qui passerait à l'échelle, sur une seule machine.
- Le lecteur ILV et les plans réels ne sont pas dans ce dépôt.
