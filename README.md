# acquisition-plans-api

API REST qui met à disposition les données de plans techniques ferroviaires : objets des plans, nomenclature des équipements, guide des symboles, bulletins de maintenance et liste des gares de voyageurs. Les cinq sources (fichiers JSON et CSV, page HTML, flux RSS, API REST publique) sont collectées, contrôlées, nettoyées et stockées dans SQLite, puis exposées par une API FastAPI, avec une recherche par mots-clés et des routes d'administration protégées par jeton.

Le dépôt sert surtout à démontrer une chaîne d'intégration continue avec GitHub Actions ; la livraison de l'image Docker est l'étape suivante.

## Origine et périmètre

Ce projet est le prolongement personnel d'un travail de stage. Le lecteur de fichiers ILV écrit pendant le stage appartient au projet de l'entreprise : **il n'est pas inclus ici**. Les trois plans de `acquisition_plans/donnees/` sont des plans **fictifs**, décodés par ce lecteur puis exportés en JSON ; la nomenclature, le guide et les bulletins sont fictifs eux aussi. La liste des gares vient de l'API open data de SNCF (`ressources.data.sncf.com`, licence ODbL).

## Organisation

```
acquisition_plans/
├── collecte/      collecte des cinq sources, dont l'extraction depuis l'API REST (api_rest.py), déduplication SHA-256
├── stockage/      schéma SQLite (schema.sql) et requêtes (PlanDatabase)
├── recherche/     recherche des extraits par mots-clés
├── api/           application FastAPI (create_app)
├── surveillance/  métriques HTTP au format Prometheus et journal JSON
├── donnees/       données fictives de démonstration
└── tests/         contrôle des données, tests automatisés et évaluation de la recherche
.github/workflows/ci-cd.yml   chaîne d'intégration continue
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

La dernière commande pose les neuf questions de `tests/questions_reference.json` à la recherche. Elle vérifie que la source attendue figure dans les quatre premiers résultats (taux minimal 87,5 %), que le rang réciproque moyen atteint 0,6 et qu'aucun résultat ne dépasse un score de 0,5 pour la question hors corpus ; elle écrit `tests/rapport_evaluation_recherche.json`.

## Chaîne d'intégration continue

Le fichier `.github/workflows/ci-cd.yml` est lu par GitHub Actions. Aucun secret n'est à créer.

**Déclencheurs** : envoi sur `main`, pull request vers `main`, lancement manuel (onglet *Actions*, *Run workflow*).

| Job | Dépend de | Ce qu'il fait | Échoue si |
|---|---|---|---|
| `qualite` | — | Ruff (règles E, F, B, SIM, C4, ARG) et Black | une règle de style n'est pas respectée |
| `donnees` | — | Contrôle des fichiers de `donnees/` : plans JSON (champs, positions, identifiants uniques), colonnes du CSV, articles du guide HTML, bulletins RSS, instantanés de l'API | une donnée source est invalide |
| `tests` | `qualite`, `donnees` | Tests unitaires et d'intégration, mesure de couverture ; rapport HTML publié en artefact | un test échoue ou la couverture est sous 85 % |
| `evaluation` | `tests` | Questions de référence posées à la recherche ; rapport JSON publié en artefact | un seuil n'est pas atteint |


**Installer la chaîne** : créer le dépôt sur GitHub et y envoyer ce contenu ; GitHub Actions détecte le workflow sans autre configuration. Les artefacts sont téléchargeables sur la page de chaque exécution.

**Tester la chaîne** :

1. Envoyer un commit sur une branche et ouvrir une pull request vers `main` : `qualite`, `donnees`, `tests` et `evaluation` s'exécutent.
2. Fusionner la pull request : les mêmes jobs s'exécutent sur `main`.
3. Vérifier un échec volontaire : sur une branche, supprimer la colonne `description` de `donnees/nomenclature_equipements.csv` ; le job `donnees` échoue et bloque les tests.

## Données personnelles

Les données sont techniques : plans et documents fictifs, liste publique des gares. Le journal JSON enregistre la méthode, la route, le code de réponse et la durée de chaque requête, sans le contenu des requêtes ni l'adresse IP.

## Limites

- La chaîne ne construit pas encore d'image Docker et ne livre rien.
- La recherche est lexicale : elle ne comprend ni les synonymes ni les accents.
- Le lecteur ILV et les plans réels ne sont pas dans ce dépôt.
